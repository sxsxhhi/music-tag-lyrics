# -*- coding: utf-8 -*-
r"""Synchronize clean skill copies according to AGENTS.md discipline（H03 加固）。

Source: this git repository (resolved from __file__).

Destinations, resolved in this order:
  1. --dest   / env MTL_DEST_AGENT        -- callable copy inside the agent workspace
  2. default: <workspace>/.agent/skills/music-tag-lyrics
  3. --dest-2 / env MTL_DEST_AUTHORITATIVE -- external authoritative install

安全约束（H03）：
  * ``--dry-run`` 绝对零写：不创建目录、不复制、不删除、不写清单；
  * ``--no-prune`` 只复制不删除；
  * 目标必须显式授权：要么经由 ``--dest`` / 环境变量指定，要么其父目录出现在
    ``MTL_TRUSTED_MIRROR_ROOTS`` 中；**仅凭目录名相同不构成信任**；
  * 目标不得等于/包含/位于 git 仓库、仓库父目录、磁盘根、``MTL_PROTECTED_DIRS``
    所列受保护目录；逐级检查符号链接与 junction；
  * 清理只针对「上次同步清单」证明由本工具管理的旧文件；首次无清单时默认不清理。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._dist_view import is_dir_excluded, is_file_excluded

# 二进制成员：复制时保持原字节，不做换行归一。
# 目前分发视图里只有 tests/fixtures/media 的两个夹具属于此类。
_BINARY_SUFFIXES = frozenset(
    {
        ".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".wav", ".dsf",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip",
    }
)

MANIFEST_NAME = ".mtl-sync-manifest.json"
TRUSTED_ROOTS_ENV = "MTL_TRUSTED_MIRROR_ROOTS"
PROTECTED_DIRS_ENV = "MTL_PROTECTED_DIRS"

_DRIVE_ROOT_NAMES = {"", os.sep}


class SyncError(RuntimeError):
    """目标路径不安全或不可写。"""


# --------------------------------------------------------------------------- #
# 路径安全
# --------------------------------------------------------------------------- #
def _split_paths(raw: str) -> list[Path]:
    out: list[Path] = []
    for part in (raw or "").replace(";", os.pathsep).split(os.pathsep):
        part = part.strip().strip('"')
        if part:
            out.append(Path(part).expanduser())
    return out


def _trusted_roots(cli_roots: list[str], allow_default: bool, default_dest: Path) -> list[Path]:
    roots = [Path(p).expanduser() for p in cli_roots]
    roots += _split_paths(os.environ.get(TRUSTED_ROOTS_ENV, ""))
    if allow_default:
        roots.append(default_dest.parent)
    resolved = []
    for r in roots:
        try:
            resolved.append(r.resolve())
        except OSError:
            continue
    return resolved


def _has_link_ancestor(path: Path) -> str | None:
    """逐级检查路径上是否出现符号链接 / junction。返回首个命中路径。"""
    for parent in [path, *path.parents]:
        try:
            if parent.is_symlink():
                return str(parent)
            if hasattr(parent, "is_junction") and parent.is_junction():
                return str(parent)
        except OSError:
            return str(parent)
    return None


def validate_dest(dest: Path, trusted_roots: list[Path]) -> Path:
    """校验目标目录，返回 resolve 后的路径；不通过则抛 SyncError。"""
    try:
        resolved = dest.expanduser().resolve()
    except OSError as exc:
        raise SyncError(f"目标路径无法解析: {dest}") from exc

    if str(resolved) in _DRIVE_ROOT_NAMES or resolved.parent == resolved:
        raise SyncError(f"拒绝写入磁盘根目录: {resolved}")

    repo = REPO_ROOT.resolve()
    if resolved == repo:
        raise SyncError("目标不能是 git 仓库本身")
    if resolved in repo.parents:
        raise SyncError(f"目标是 git 仓库的祖先目录: {resolved}")
    if repo in resolved.parents:
        raise SyncError(f"目标位于 git 仓库内部: {resolved}")
    if resolved == repo.parent:
        raise SyncError(f"目标是 git 仓库的父目录: {resolved}")

    for protected in _split_paths(os.environ.get(PROTECTED_DIRS_ENV, "")):
        try:
            pr = protected.resolve()
        except OSError:
            continue
        if resolved == pr or pr in resolved.parents:
            raise SyncError(f"目标是受保护目录或其子目录: {resolved}")

    link = _has_link_ancestor(resolved)
    if link:
        raise SyncError(f"目标路径上存在符号链接/junction: {link}")

    if resolved.exists() and not resolved.is_dir():
        raise SyncError(f"目标已存在且不是目录: {resolved}")

    if not any(resolved == root or root in resolved.parents for root in trusted_roots):
        raise SyncError(
            "目标目录未获显式授权；请用 --dest 指定，或把其父目录加入 "
            f"{TRUSTED_ROOTS_ENV}，或显式传 --allow-default-dest"
        )
    return resolved


# --------------------------------------------------------------------------- #
# 同步
# --------------------------------------------------------------------------- #
def collect_members() -> dict[str, Path]:
    """收集应分发的成员 {posix_rel: 源绝对路径}。"""
    members: dict[str, Path] = {}
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if not is_dir_excluded(d)]
        rel_root = Path(root).relative_to(REPO_ROOT)
        for file in sorted(files):
            rel_file = rel_root / file if str(rel_root) != "." else Path(file)
            rel_str = str(rel_file).replace("\\", "/")
            if is_file_excluded(rel_str, file):
                continue
            if rel_str == MANIFEST_NAME:
                continue
            members[rel_str] = Path(root) / file
    return members


def _read_manifest(target: Path) -> set[str] | None:
    path = target / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    managed = data.get("managed")
    if not isinstance(managed, list):
        return None
    return {str(x).replace("\\", "/") for x in managed if isinstance(x, str)}


def _copy_member(src: Path, dest: Path) -> None:
    """复制单个成员，并把文本文件的换行归一为 LF。

    v1.8.5 起正式包成员字节取自 **git 索引 blob**（`.gitattributes` 统一 `eol=lf`），
    而本工具直接复制工作树字节；在 `core.autocrlf=true` 的 Windows 检出上工作树是
    CRLF，会导致镜像与 `release-manifest.json` 的成员哈希不符，也让「权威包 == 发布
    产物」无法用同一份哈希清单核验。这里统一归一为 LF。
    """
    suffix = dest.suffix.lower()
    data = src.read_bytes()
    if suffix not in _BINARY_SUFFIXES and b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
    dest.write_bytes(data)
    shutil.copystat(src, dest)


def sync_to(
    target: Path,
    *,
    dry_run: bool = False,
    no_prune: bool = False,
    adopt_existing: bool = False,
) -> dict:
    """把仓库分发给定视图同步到 target。返回统计信息。

    ``adopt_existing=True`` 时，若目标没有受管清单，则把**当前已存在的文件**视作
    受管基线，使首次同步也能清理陈旧残留（显式 opt-in，默认关闭以免误删）。
    """
    members = collect_members()
    previous = _read_manifest(target)
    if previous is None and adopt_existing and target.is_dir():
        previous = {
            p.relative_to(target).as_posix()
            for p in target.rglob("*")
            if p.is_file() and p.name != MANIFEST_NAME
        }
    stats = {
        "target": str(target),
        "copied": 0,
        "pruned": 0,
        "skipped_prune": 0,
        "created_dirs": 0,
        "manifest_written": False,
    }

    if not dry_run:
        target.mkdir(parents=True, exist_ok=True)

    for rel_str, src in sorted(members.items()):
        dest_file = target / rel_str
        if not dry_run:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            _copy_member(src, dest_file)
        stats["copied"] += 1

    if not no_prune and previous is not None:
        stale = sorted(previous - set(members))
        for rel_str in stale:
            victim = target / rel_str
            if not victim.is_file():
                continue
            # 只删清单登记过、且仍位于目标内的文件
            try:
                victim.resolve().relative_to(target)
            except ValueError:
                continue
            if not dry_run:
                try:
                    victim.unlink()
                except OSError:
                    continue
            stats["pruned"] += 1

    # 清理所有被排除的运行时目录（如 __pycache__, .pytest_cache）与被排除的文件（如 *.pyc, *.pyo）及陈旧空目录
    if not no_prune and not dry_run and target.is_dir():
        for root, dirs, files in os.walk(target, topdown=False):
            for f in files:
                if f == MANIFEST_NAME:
                    continue
                rel_f = Path(root).relative_to(target) / f if root != str(target) else Path(f)
                rel_posix = rel_f.as_posix()
                if is_file_excluded(rel_posix, f):
                    try:
                        (Path(root) / f).unlink()
                        stats["pruned"] += 1
                    except OSError:
                        pass
            for d in dirs:
                dir_path = Path(root) / d
                if is_dir_excluded(d):
                    shutil.rmtree(dir_path, ignore_errors=True)
                    continue
                if dir_path.is_dir() and not any(dir_path.iterdir()):
                    try:
                        dir_path.rmdir()
                    except OSError:
                        pass
    elif not no_prune and previous is None and target.is_dir():
        stats["skipped_prune"] = -1  # 首次同步：无受管清单，不清理未知文件

    if not dry_run:
        manifest = {
            "version": 1,
            "generated_by": "scripts/sync_skill_copies.py",
            "managed": sorted(members),
        }
        (target / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        stats["manifest_written"] = True

    return stats


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def default_dest_agent() -> Path:
    return REPO_ROOT.parent.parent / ".agent" / "skills" / "music-tag-lyrics"


def _resolve_targets(args) -> list[tuple[str, Path]]:
    targets: list[tuple[str, Path]] = []
    primary = args.dest or os.environ.get("MTL_DEST_AGENT")
    targets.append(("可调用副本", Path(primary) if primary else default_dest_agent()))
    secondary = args.dest_2 or os.environ.get("MTL_DEST_AUTHORITATIVE")
    if secondary:
        targets.append(("权威安装包", Path(secondary)))
    return targets


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Skill 副本纯净同步（H03 加固）")
    ap.add_argument("--dest", default="", help="主目标目录（可调用副本）")
    ap.add_argument("--dest-2", default="", help="次目标目录（权威安装包）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写任何文件、不建目录")
    ap.add_argument("--no-prune", action="store_true", help="只复制，不删除任何旧文件")
    ap.add_argument(
        "--trusted-root",
        action="append",
        default=[],
        metavar="DIR",
        help="显式授权目标所在的父目录（可重复）",
    )
    ap.add_argument(
        "--adopt-existing",
        action="store_true",
        help="目标无受管清单时，把当前已存在文件视作受管基线（首次迁移用，之后即可正常清理陈旧残留）",
    )
    ap.add_argument(
        "--allow-default-dest",
        action="store_true",
        help="显式信任默认目标 <workspace>/.agent/skills/music-tag-lyrics",
    )
    args = ap.parse_args(argv)

    print("=" * 60)
    mode = "（dry-run：不做任何写入）" if args.dry_run else ""
    print(f"🔄 正在执行 Skill 副本纯净同步{mode}")
    print("=" * 60)

    trusted = _trusted_roots(args.trusted_root, args.allow_default_dest, default_dest_agent())
    rc = 0
    for label, target in _resolve_targets(args):
        try:
            resolved = validate_dest(target, trusted)
        except SyncError as exc:
            print(f"❌ {label} 校验失败: {exc}", file=sys.stderr)
            rc = 2
            continue
        stats = sync_to(
            resolved,
            dry_run=args.dry_run,
            no_prune=args.no_prune,
            adopt_existing=args.adopt_existing,
        )
        verb = "将同步" if args.dry_run else "已同步"
        print(
            f"✅ {verb}至{label}: {resolved} "
            f"(成员 {stats['copied']} 个，清理 {stats['pruned']} 个陈旧文件)"
        )
        if stats["skipped_prune"] == -1:
            print(
                "   ℹ️  首次同步无受管清单：未删除任何未知文件（防误删）；"
                "如需接管既有残留请加 --adopt-existing"
            )
    if not args.dry_run:
        print("🎉 Skill 副本同步完成！")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
