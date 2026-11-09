# -*- coding: utf-8 -*-
r"""Release packaging and manifest generator for music-tag-lyrics (R22, R24, P2-02, P2-03, P2-04, H01).

Generates:
1. dist/music-tag-lyrics-skill-v{__version__}.zip (deterministic skill distribution package)
2. release-manifest.json + docs/release-manifest.json (byte-identical release metadata)

H01 约束（见 v1.8.1 终审定稿方案 §4/H01）：
- 成员来源只取 `git ls-files -z --cached`，Git 不可用即失败，**不回落 os.walk**；
- 安全过滤：绝对路径 / `..` 越界 / 符号链接与 junction / 索引缺失文件 / 子模块一律拒绝，
  已跟踪的高危文件名（`.env*`、`*.pem`、`*.key`、`id_rsa`、`*.p12`、`cookies.*`）直接构建失败；
- `--release`（默认）要求构建前已跟踪工作树干净；`--dev-package` 允许脏但只写 dist/dev/；
- ZIP 逐成员显式 `ZipInfo` + `writestr`，固定排序 / 时间戳 / 权限 / 无 extra / 无 comment，
  保证同环境重复构建字节相同；
- Manifest 先构建再比对：两份已存在且与实际包一致时**原样复用**，一份都不写。
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.version import __version__
from scripts._dist_view import is_dir_excluded, is_file_excluded

DIST_DIR = REPO_ROOT / "dist"
DEV_DIST_DIR = DIST_DIR / "dev"
STAGING_DIR = DIST_DIR / ".staging"
MANIFEST_PATH = REPO_ROOT / "release-manifest.json"
DOCS_MANIFEST_PATH = REPO_ROOT / "docs" / "release-manifest.json"
ZIP_NAME = f"music-tag-lyrics-skill-v{__version__}.zip"
ZIP_PATH = DIST_DIR / ZIP_NAME
DEV_ZIP_PATH = DEV_DIST_DIR / ZIP_NAME
DEV_MANIFEST_PATH = DEV_DIST_DIR / "release-manifest.dev.json"

# 确定性归档参数
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_CREATE_SYSTEM = 3  # 3 = Unix，避免跨平台外部属性差异
REGULAR_FILE_ATTR = (stat.S_IFREG | 0o644) << 16
EXEC_FILE_ATTR = (stat.S_IFREG | 0o755) << 16

# 需要保留可执行位的分发成员（相对 POSIX 路径）；其余统一 0o644
EXECUTABLE_RELPATHS = frozenset()

# 已跟踪但绝不允许进入发布包的高危文件名（命中即失败，不静默排除）
RISKY_NAME_PATTERNS = (
    (".env", "LEAK_RISKY_ENV_FILE"),
    (".pem", "LEAK_RISKY_PRIVATE_KEY"),
    (".key", "LEAK_RISKY_PRIVATE_KEY"),
    (".p12", "LEAK_RISKY_KEYSTORE"),
    ("cookies.", "LEAK_RISKY_CREDENTIAL"),
    ("id_rsa", "LEAK_RISKY_SSH_KEY"),
    ("id_ed25519", "LEAK_RISKY_SSH_KEY"),
)

GITLINK_MODE = "160000"


class BuildError(RuntimeError):
    """构建前置条件或安全校验失败。"""


# --------------------------------------------------------------------------- #
# Git 输入层
# --------------------------------------------------------------------------- #
def _git(args: list[str]) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args], cwd=str(REPO_ROOT), text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as exc:
        raise BuildError("GIT_UNAVAILABLE: 找不到 git 可执行文件，构建必须基于已跟踪文件") from exc
    except subprocess.CalledProcessError as exc:
        raise BuildError(f"GIT_FAILED: git {' '.join(args)} 返回 {exc.returncode}") from exc
    return out


def get_git_commit_sha() -> str:
    return _git(["rev-parse", "HEAD"]).strip()


def list_tracked_entries() -> list[tuple[str, str]]:
    """返回 [(mode, posix_relpath)]，按 posix 路径稳定排序。"""
    raw = _git(["ls-files", "-z", "--stage"])
    entries: list[tuple[str, str]] = []
    for record in raw.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        fields = meta.split()
        if len(fields) != 3:
            raise BuildError(f"GIT_INDEX_MALFORMED: 无法解析索引记录 {path!r}")
        mode, _obj, stage = fields
        if stage != "0":
            raise BuildError(f"GIT_INDEX_CONFLICT: {path} 存在未合并的索引条目")
        entries.append((mode, path.replace("\\", "/")))
    entries.sort(key=lambda item: item[1])
    return entries


def tracked_dirty_report() -> list[str]:
    """已跟踪工作树相对 HEAD 的改动（含 staged / unstaged / 删除）。"""
    out = _git(["status", "--porcelain", "--untracked-files=no"])
    return [ln for ln in out.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# 成员选择与安全过滤
# --------------------------------------------------------------------------- #
def _risky_rule_for(name: str) -> str | None:
    lowered = name.lower()
    if lowered.startswith(".env") or lowered.endswith(".env"):
        return "LEAK_RISKY_ENV_FILE"
    for needle, rule in RISKY_NAME_PATTERNS:
        if needle in lowered:
            return rule
    return None


def _path_has_excluded_dir(posix_rel: str) -> bool:
    parts = posix_rel.split("/")
    return any(is_dir_excluded(part) for part in parts[:-1])


def select_members() -> list[str]:
    """确定最终进入发布包的成员（排序后的 posix 相对路径）。"""
    selected: list[str] = []
    risky: list[tuple[str, str]] = []

    for mode, posix_rel in list_tracked_entries():
        if mode == GITLINK_MODE:
            raise BuildError(f"SUBMODULE_UNSUPPORTED: {posix_rel} 是子模块，发布包不接受子模块")
        if posix_rel.startswith("/") or (len(posix_rel) > 1 and posix_rel[1] == ":"):
            raise BuildError(f"ABSOLUTE_PATH_REJECTED: {posix_rel}")
        if ".." in posix_rel.split("/"):
            raise BuildError(f"PATH_TRAVERSAL_REJECTED: {posix_rel}")

        abs_path = (REPO_ROOT / posix_rel)
        try:
            abs_path.resolve().relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise BuildError(f"OUTSIDE_REPO_REJECTED: {posix_rel}") from exc

        name = posix_rel.rsplit("/", 1)[-1]
        rule = _risky_rule_for(name)
        if rule:
            risky.append((posix_rel, rule))
            continue

        if _path_has_excluded_dir(posix_rel) or is_file_excluded(posix_rel, name):
            continue

        if abs_path.is_symlink() or (hasattr(abs_path, "is_junction") and abs_path.is_junction()):
            raise BuildError(f"SYMLINK_REJECTED: {posix_rel} 是符号链接或 junction")
        if not abs_path.is_file():
            raise BuildError(f"INDEX_FILE_MISSING: {posix_rel} 在索引中但磁盘上不存在")

        selected.append(posix_rel)

    if risky:
        detail = "\n".join(f"  [{rule}] {rel}" for rel, rule in risky)
        raise BuildError(
            "RISKY_TRACKED_FILE: 检测到已跟踪的高危文件，构建中止（请先从索引移除）：\n" + detail
        )

    return selected


# --------------------------------------------------------------------------- #
# 确定性 ZIP
# --------------------------------------------------------------------------- #
def _member_attr(posix_rel: str) -> int:
    return EXEC_FILE_ATTR if posix_rel in EXECUTABLE_RELPATHS else REGULAR_FILE_ATTR


def _member_digests(members: list[str]) -> dict[str, str]:
    return {rel: sha256_file(REPO_ROOT / rel) for rel in members}


def build_zip_at(target: Path, members: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as zf:
        for rel in members:
            data = (REPO_ROOT / rel).read_bytes()
            info = zipfile.ZipInfo(filename=rel, date_time=FIXED_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = FIXED_CREATE_SYSTEM
            info.external_attr = _member_attr(rel)
            info.extra = b""
            info.comment = b""
            zf.writestr(info, data)


def verify_zip_members(zip_path: Path, expected: list[str]) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        actual = [info.filename for info in zf.infolist()]
        for info in zf.infolist():
            name = info.filename
            if name.startswith("/") or ".." in name.split("/"):
                raise BuildError(f"ZIP_PATH_TRAVERSAL: {name}")
            if info.file_size != (REPO_ROOT / name).stat().st_size:
                raise BuildError(f"ZIP_SIZE_MISMATCH: {name}")
    if actual != expected:
        missing = [p for p in expected if p not in actual]
        extra = [p for p in actual if p not in expected]
        raise BuildError(f"ZIP_MEMBER_MISMATCH: 缺失={missing} 多余={extra}")


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_compatible(
    manifest: dict | None, *, zip_name: str, zip_size: int, zip_sha: str, file_count: int
) -> bool:
    if not manifest:
        return False
    if manifest.get("version") != __version__:
        return False
    if manifest.get("release_tag") != f"v{__version__}":
        return False
    if "manifest_scope" not in manifest:
        return False
    asset = manifest.get("release_asset") or {}
    if asset.get("filename") != zip_name:
        return False
    if asset.get("size_bytes") != zip_size:
        return False
    if asset.get("sha256") != zip_sha:
        return False
    if asset.get("file_count") != file_count:
        return False
    commit = (manifest.get("git_commit") or "").strip()
    if not commit or commit == "unknown":
        return False
    try:
        _git(["cat-file", "-e", f"{commit}^{{commit}}"])
    except BuildError:
        return False
    return True


def _existing_manifests_identical() -> tuple[dict | None, bool]:
    a = _load_manifest(MANIFEST_PATH)
    b = _load_manifest(DOCS_MANIFEST_PATH)
    if a is None and b is None:
        return None, True
    if a is None or b is None:
        return None, False
    return a, a == b


def write_manifest_atomic(payload: dict, targets: list[Path]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)


def _sanitized_change_list() -> list[str]:
    """开发包用的改动路径清单（相对路径，已脱敏）。"""
    out = _git(["status", "--porcelain", "--untracked-files=no"])
    changed: list[str] = []
    for line in out.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            changed.append(path.replace("\\", "/"))
    return sorted(set(changed))


# --------------------------------------------------------------------------- #
# 构建流程
# --------------------------------------------------------------------------- #
def build_release(*, dev: bool) -> int:
    print("=" * 60)
    mode_label = "开发包（dev）" if dev else "正式发布包"
    print(f"📦 正在构建 music-tag-lyrics v{__version__} {mode_label}")
    print("=" * 60)

    dirty = tracked_dirty_report()
    if not dev:
        if dirty:
            print("❌ 拒绝构建：正式包要求构建前已跟踪工作树干净。")
            for ln in dirty:
                print(f"   {ln}")
            print("   请先提交或还原改动，或改用 --dev-package。")
            return 3
    else:
        changed = _sanitized_change_list()
        if changed:
            print(f"⚠️  开发包：工作树有 {len(changed)} 个已跟踪改动，将在 manifest 标记 dirty=true")
            for rel in changed:
                print(f"   - {rel}")

    members = select_members()
    print(f"📋 已跟踪且通过安全过滤的成员数: {len(members)}")

    commit_sha = get_git_commit_sha()
    print(f"Git HEAD Commit: {commit_sha}")

    if dev:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        staging_zip = STAGING_DIR / ZIP_NAME
        build_zip_at(staging_zip, members)
        verify_zip_members(staging_zip, members)
        zip_size = staging_zip.stat().st_size
        zip_sha = sha256_file(staging_zip)

        payload = {
            "schema_version": "1.0",
            "name": "music-tag-lyrics",
            "version": __version__,
            "release_tag": f"v{__version__}",
            "manifest_scope": "DEV PACKAGE - not a release; describes working tree at git_commit",
            "dirty": bool(dirty),
            "changed_paths": _sanitized_change_list(),
            "build_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "git_commit": commit_sha,
            "release_asset": {
                "filename": ZIP_NAME,
                "size_bytes": zip_size,
                "sha256": zip_sha,
                "file_count": len(members),
            },
        }
        DEV_DIST_DIR.mkdir(parents=True, exist_ok=True)
        os.replace(staging_zip, DEV_ZIP_PATH)
        write_manifest_atomic(payload, [DEV_MANIFEST_PATH])
        print(f"✅ 开发包: {DEV_ZIP_PATH}")
        print(f"📝 开发 Manifest: {DEV_MANIFEST_PATH}（dirty={bool(dirty)}）")
        print(f"🔑 SHA-256: {zip_sha}")
        return 0

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    staging_zip = STAGING_DIR / ZIP_NAME
    build_zip_at(staging_zip, members)
    verify_zip_members(staging_zip, members)

    zip_size = staging_zip.stat().st_size
    zip_sha = sha256_file(staging_zip)
    file_count = len(members)
    print(f"📦 发布包大小: {zip_size:,} 字节 ({zip_size / 1024:.1f} KB)")
    print(f"🔑 SHA-256 校验和: {zip_sha}")

    existing, identical = _existing_manifests_identical()
    consistent = identical and _manifest_compatible(
        existing, zip_name=ZIP_NAME, zip_size=zip_size, zip_sha=zip_sha, file_count=file_count
    )
    if consistent and existing is not None:
        reuse_commit = existing.get("git_commit")
        reuse_stamp = existing.get("build_timestamp_utc")
        print(f"♻️  两份 Manifest 与当前包一致（git_commit={str(reuse_commit)[:12]}），原样复用、不写盘")
        manifest_data = dict(existing)
        manifest_data["git_commit"] = reuse_commit
        manifest_data["build_timestamp_utc"] = reuse_stamp
    else:
        if existing is None and not identical:
            print("⚠️  两份 Manifest 只有一份存在或不一致，将重建并同时写入两份")
        manifest_data = {
            "schema_version": "1.0",
            "name": "music-tag-lyrics",
            "version": __version__,
            "release_tag": f"v{__version__}",
            "manifest_scope": "describes source tree at git_commit; manifest commit itself not included",
            "build_timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "git_commit": commit_sha,
            "release_asset": {
                "filename": ZIP_NAME,
                "size_bytes": zip_size,
                "sha256": zip_sha,
                "file_count": file_count,
            },
            "platforms_verified": [
                "Windows 11 (Python 3.13)",
                "Ubuntu 24.04 (GitHub Actions CI, Python 3.13)",
            ],
            "python_compatibility": "Python 3.10 - 3.13",
            "container_support_matrix": {
                "FLAC (.flac)": "Verified (real audio tracks)",
                "MP3 (.mp3)": "Verified (Real ID3v2.3/2.4 USLT)",
                "M4A / AAC (.m4a)": "Verified (Real QuickTime atoms)",
                "WAV (.wav)": "Verified (Real RIFF ID3 chunk)",
                "DSF (.dsf)": "Pending Physical Fixtures Verification",
                "OGG (.ogg)": "Pending Physical Fixtures Verification",
            },
            "quality_and_safety_guarantees": {
                "mother_disk_protection": "STRICT_ENFORCED (mother library zero-touch)",
                "sample_audio_contamination": "ZERO_CONTAMINATION (all sampled tracks verified by SHA-256)",
                "network_tripwire": "ACTIVE (offline tests protected against public egress)",
                "isrc_safety_gate": "STRICT_VERIFIED (unknown artist & version mismatch blocked)",
            },
        }
        write_manifest_atomic(manifest_data, [MANIFEST_PATH, DOCS_MANIFEST_PATH])
        print(f"📝 已输出 Manifest 至 {MANIFEST_PATH.name} 与 {DOCS_MANIFEST_PATH}")

    # 最后一步才替换正式 ZIP，失败时旧包保持可用
    os.replace(staging_zip, ZIP_PATH)
    print(f"🎉 发布包构建完毕: {ZIP_PATH.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="music-tag-lyrics 发布包与 Manifest 构建器（H01）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--release", action="store_true", help="正式包（默认），要求工作树干净")
    group.add_argument(
        "--dev-package", action="store_true", help="开发包，允许已跟踪脏改，只写 dist/dev/"
    )
    args = ap.parse_args(argv)

    try:
        return build_release(dev=bool(args.dev_package))
    except BuildError as exc:
        print(f"❌ 构建中止: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
