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
import stat as stat_mod
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._dist_view import is_dir_excluded, is_file_excluded

# Windows 上 junction（目录重解析点）不是 S_ISLNK，需要借助 st_file_attributes 识别。
# 非 Windows 平台该属性不存在，取 0 即退化为只判符号链接。
_REPARSE_POINT_FLAG = getattr(stat_mod, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

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
def collect_members() -> dict[str, bytes]:
    """收集应分发的成员 ``{posix_rel: 字节}``。

    镜像加固 F1：成员集合与字节**一律来自 git 索引**，不再遍历工作树。

    旧的 ``os.walk`` 实现不参考 Git 跟踪清单、也不参考 ``.gitignore``，结果
    ``.env``、私钥、私人笔记、以及 CLI 落在仓库里的状态文件（如
    ``.local/state/gh/device-id``）都会被当成成员复制进镜像；``Path.read_bytes()``
    还会跟随源符号链接，把仓库外的内容读进来。

    现在直接复用 ``build_release_manifest`` 的成员选择与 blob 装载：
      * 成员来源 = ``git ls-files --stage``（索引），未跟踪/被忽略的文件天然排除；
      * 已跟踪但越界/子模块/符号链接/高危名由构建器统一拒绝（fail-closed）；
      * 字节 = 索引 blob，已按 ``.gitattributes`` 统一为 LF，与发布产物逐字节一致，
        不再需要按扩展名猜测文本或二进制（F3 一并解决）。
    """
    from scripts import build_release_manifest as builder

    return builder.load_member_bytes(builder.select_members(), from_git=True)


def _is_link_like(path: Path) -> bool:
    """判断路径本身是否为符号链接 / junction（不跟随）。"""
    try:
        st = os.lstat(path)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return False
    if stat_mod.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & _REPARSE_POINT_FLAG)


def _assert_no_link_in_path(root: Path, dest: Path) -> None:
    """确保 ``root`` 到 ``dest`` 的**每一级**都不是符号链接 / junction。

    镜像加固 F2：``validate_dest()`` 只校验目标根路径，而逐文件写入时
    ``dest.parent.mkdir(parents=True)`` 会顺着已存在的子目录链接走出去；
    形如 ``mirror/core -> ../protected`` 的布局会把文件写到受保护目录里。
    """
    root_abs = Path(os.path.abspath(root))
    dest_abs = Path(os.path.abspath(dest))
    try:
        rel = dest_abs.relative_to(root_abs)
    except ValueError as exc:
        raise SyncError(f"目标路径不在受信任根内: {dest}") from exc

    current = root_abs
    for part in rel.parts:
        current = current / part
        if _is_link_like(current):
            raise SyncError(
                f"目标路径存在符号链接/junction，拒绝写入: {current}"
            )


def _write_member(target_root: Path, dest: Path, data: bytes) -> None:
    """把成员字节原子写入目标路径，写入前后各校验一次路径安全性。

    临时文件必须**不可预测且原子独占创建**：固定名 ``*.mtl-tmp`` 可被预先布置成
    符号链接，``write_bytes()`` 会顺着链接把内容写到镜像之外；随后的
    ``os.replace`` 还可能把该链接移成最终目标。因此改用
    ``tempfile.mkstemp``（随机名 + ``O_EXCL``），并通过返回的 fd 写入，
    不再按路径二次打开。
    """
    _assert_no_link_in_path(target_root, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # mkdir 之后再查一次：父目录可能是本次创建前刚被替换成链接
    _assert_no_link_in_path(target_root, dest)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(dest.parent), prefix=dest.name + ".", suffix=".mtl-tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        # mkstemp 创建的文件是 0600，恢复为与常规写入一致的可读权限
        os.chmod(tmp, 0o644)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _read_manifest(target: Path) -> set[str] | None:
    path = target / MANIFEST_NAME
    # 受管清单自身也必须是普通文件：若是符号链接/junction，读到的是链接外的内容，
    # 会据此决定删除范围，属于不可信输入（fail-closed）。
    if _is_link_like(path):
        raise SyncError(f"受管清单路径是符号链接/junction，拒绝读取: {path}")
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
        # 落盘前先对全部目标路径做一次链接预检：任何一个目标路径上已有
        # 符号链接/junction 就在**写入任何文件之前**失败，避免留下半更新镜像。
        # 受管清单与成员同等对待——它同样会写入目标目录。
        # 此检查不要求目标已存在，全新目标上是无操作。
        for rel_str in sorted(members):
            _assert_no_link_in_path(target, target / rel_str)
        _assert_no_link_in_path(target, target / MANIFEST_NAME)
        target.mkdir(parents=True, exist_ok=True)

    for rel_str, data in sorted(members.items()):
        dest_file = target / rel_str
        if not dry_run:
            _write_member(target, dest_file, data)
        stats["copied"] += 1

    if not no_prune and previous is not None:
        stale = sorted(previous - set(members))
        for rel_str in stale:
            victim = target / rel_str
            if not victim.is_file():
                continue
            # 只删清单登记过、且仍位于目标内的文件。
            # 两侧都必须 resolve()：Windows 上 mkdtemp 可能返回 8.3 短路径
            # （如 <user-home>\RUNNER~1\...），而 resolve() 得到长路径
            # （<user-home>\runneradmin\...），直接 relative_to 会抛 ValueError，
            # 导致清理被静默跳过（CI 实测：Windows runner 上 pruned 恒为 0）。
            try:
                victim.resolve().relative_to(target.resolve())
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
        payload = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        # 清单与成员走同一套受保护写入：临时文件随机名 + 路径链接校验 + 原子替换，
        # 不再直接 write_text()（那会跟随清单路径上的符号链接写到镜像之外）。
        _write_member(target, target / MANIFEST_NAME, payload)
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
