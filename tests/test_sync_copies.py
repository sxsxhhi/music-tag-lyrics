# -*- coding: utf-8 -*-
"""H03 验收：同步工具的 dry-run 零写、no-prune 保护、受管清单与越界拒绝。

全部在临时目录中进行，**不触碰任何真实镜像副本**。
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO_ROOT))
from scripts import sync_skill_copies as sync_mod  # noqa: E402


def _tree_digest(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="mtl_sync_"))


def _trusted(root: Path) -> list[Path]:
    return [root.resolve()]


# --------------------------------------------------------------------------- #
# 1. dry-run 绝对零写
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing():
    """dry-run 不得创建目标目录、不得写文件、不得写清单。"""
    base = _tmp()
    target = base / "mirror"
    try:
        assert sync_mod.main(["--dest", str(target), "--dry-run", "--trusted-root", str(base)]) == 0
        assert not target.exists(), "dry-run 创建了目标目录"
        assert _tree_digest(base) == {}
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_dry_run_preserves_existing_tree():
    """目标已存在时，dry-run 前后树内容与 SHA 完全一致。"""
    base = _tmp()
    target = base / "mirror"
    target.mkdir()
    (target / "extra.txt").write_text("keep me\n", encoding="utf-8")
    try:
        before = _tree_digest(target)
        assert sync_mod.main(["--dest", str(target), "--dry-run", "--trusted-root", str(base)]) == 0
        assert _tree_digest(target) == before, "dry-run 改动了既有文件"
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 2. 首次同步不清理未知文件；后续只清理受管清单内的陈旧文件
# --------------------------------------------------------------------------- #
def test_first_sync_does_not_prune_unknown():
    base = _tmp()
    target = base / "mirror"
    target.mkdir()
    (target / "user-note.md").write_text("手工放置\n", encoding="utf-8")
    try:
        stats = sync_mod.sync_to(target)
        assert stats["pruned"] == 0
        assert (target / "user-note.md").is_file(), "首次同步误删了非受管文件"
        assert stats["manifest_written"] is True
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_second_sync_prunes_only_managed_stale():
    base = _tmp()
    target = base / "mirror"
    try:
        sync_mod.sync_to(target)
        managed = json.loads((target / sync_mod.MANIFEST_NAME).read_text(encoding="utf-8"))["managed"]
        stale_rel = managed[0]
        stale = target / stale_rel
        assert stale.is_file()
        # 手工伪造一个「受管但已过时」的文件 + 一个未知文件
        (target / "unknown.txt").write_text("x\n", encoding="utf-8")
        # 把清单改成包含一个不存在的受管文件
        data = json.loads((target / sync_mod.MANIFEST_NAME).read_text(encoding="utf-8"))
        data["managed"].append("legacy/gone.txt")
        legacy = target / "legacy" / "gone.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("old\n", encoding="utf-8")
        (target / sync_mod.MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")

        stats = sync_mod.sync_to(target)
        assert stats["pruned"] == 1, stats
        assert not legacy.exists(), "受管陈旧文件未被清理"
        assert (target / "unknown.txt").is_file(), "未知文件被误删"
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_no_prune_keeps_everything():
    base = _tmp()
    target = base / "mirror"
    try:
        sync_mod.sync_to(target)
        data = json.loads((target / sync_mod.MANIFEST_NAME).read_text(encoding="utf-8"))
        data["managed"].append("legacy/gone.txt")
        legacy = target / "legacy" / "gone.txt"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("old\n", encoding="utf-8")
        (target / sync_mod.MANIFEST_NAME).write_text(json.dumps(data), encoding="utf-8")

        stats = sync_mod.sync_to(target, no_prune=True)
        assert stats["pruned"] == 0
        assert legacy.is_file(), "no-prune 仍删除了文件"
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 3. 越界与未授权目标必须拒绝
# --------------------------------------------------------------------------- #
def test_reject_repo_internal_and_ancestors():
    """仓库内部、仓库本身、仓库父目录、磁盘根都不得作为目标。"""
    trusted = [Path(tempfile.gettempdir()).resolve()]
    for bad in (
        REPO_ROOT / "dist" / "mirror",
        REPO_ROOT,
        REPO_ROOT.parent,
        Path(os.sep),
    ):
        try:
            sync_mod.validate_dest(bad, trusted)
        except sync_mod.SyncError:
            pass
        else:
            raise AssertionError(f"越界目标未被拒绝: {bad}")


def test_reject_untrusted_dest_even_if_name_matches():
    """仅目录名相同不构成信任。"""
    base = _tmp()
    target = base / "skills" / "music-tag-lyrics"
    try:
        try:
            sync_mod.validate_dest(target, [])
        except sync_mod.SyncError as exc:
            assert "未获显式授权" in str(exc), str(exc)
        else:
            raise AssertionError("未授权目标被接受")
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_reject_protected_dir_via_env():
    base = _tmp()
    protected = base / "mother"
    target = protected / "mirror"
    protected.mkdir()
    try:
        old = os.environ.get(sync_mod.PROTECTED_DIRS_ENV)
        os.environ[sync_mod.PROTECTED_DIRS_ENV] = str(protected)
        try:
            sync_mod.validate_dest(target, _trusted(base))
        except sync_mod.SyncError as exc:
            assert "受保护目录" in str(exc), str(exc)
        else:
            raise AssertionError("受保护目录下的目标被接受")
        finally:
            if old is None:
                os.environ.pop(sync_mod.PROTECTED_DIRS_ENV, None)
            else:
                os.environ[sync_mod.PROTECTED_DIRS_ENV] = old
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 4. 正常同步内容与分发包成员一致
# --------------------------------------------------------------------------- #
def test_synced_members_match_dist_view():
    base = _tmp()
    target = base / "mirror"
    try:
        stats = sync_mod.sync_to(target)
        members = sync_mod.collect_members()
        assert stats["copied"] == len(members)
        for rel in ("SKILL.md", "README.md", "core/version.py"):
            assert (target / rel).is_file(), rel
        for rel in ("scripts/sync_skill_copies.py", "scripts/build_release_manifest.py"):
            assert rel not in members, f"开发资产不应同步: {rel}"
        assert (target / sync_mod.MANIFEST_NAME).is_file()
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
