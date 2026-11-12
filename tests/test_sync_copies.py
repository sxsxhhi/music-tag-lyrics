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


def test_adopt_existing_allows_first_prune():
    """显式 --adopt-existing 时，首次同步可清理既有残留。"""
    base = _tmp()
    target = base / "mirror"
    target.mkdir()
    stale = target / "old_report.md"
    stale.write_text("stale\n", encoding="utf-8")
    try:
        stats = sync_mod.sync_to(target, adopt_existing=True)
        assert stats["pruned"] >= 1, stats
        assert not stale.exists(), "--adopt-existing 未清理既有残留"
        assert stats["manifest_written"] is True
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_sync_prunes_excluded_pycache_and_pyc():
    """同步时必须无条件清理目标目录中遗留的 __pycache__ 与 *.pyc 文件。"""
    base = _tmp()
    target = base / "mirror"
    target.mkdir()
    pycache = target / "core" / "__pycache__"
    pycache.mkdir(parents=True, exist_ok=True)
    pyc_file = pycache / "converter.cpython-313.pyc"
    pyc_file.write_bytes(b"\x00\x01\x02\x03")
    loose_pyc = target / "stray.pyc"
    loose_pyc.write_bytes(b"\x00\x01\x02\x03")
    try:
        stats = sync_mod.sync_to(target)
        assert not pycache.exists(), "__pycache__ 目录未被清理"
        assert not pyc_file.exists(), "pyc 文件未被清理"
        assert not loose_pyc.exists(), "散落的 pyc 文件未被清理"
        assert stats["manifest_written"] is True
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 5. 镜像加固 F1+F2+F4：成员来自 git 索引、目标路径拒绝链接、.DS_Store 归位
# --------------------------------------------------------------------------- #
def _make_git_repo(files: dict[str, str], *, commit: bool = True) -> Path:
    """建一个最小 git 仓库，返回仓库根。"""
    import subprocess

    root = _tmp()
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
    if commit:
        subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "commit", "-q", "-m", "init"],
            cwd=str(root), check=True,
        )
    return root


def _patch_repo_root(repo: Path):
    """把两个开发脚本的 REPO_ROOT 临时指向 repo，返回恢复所需的状态。"""
    from scripts import build_release_manifest as builder

    saved = (sync_mod.REPO_ROOT, builder.REPO_ROOT)
    sync_mod.REPO_ROOT = repo
    builder.REPO_ROOT = repo
    return saved


def _restore_repo_root(saved) -> None:
    from scripts import build_release_manifest as builder

    sync_mod.REPO_ROOT, builder.REPO_ROOT = saved


def test_members_come_from_git_index_not_worktree():
    """F1：成员字节取自索引 blob，工作树的未提交改动不会进镜像。"""
    repo = _make_git_repo({"core/version.py": 'V = "A"\n'})
    saved = _patch_repo_root(repo)
    try:
        # 工作树改成 B，但不暂存
        (repo / "core" / "version.py").write_text('V = "B"\n', encoding="utf-8", newline="\n")
        assert sync_mod.collect_members()["core/version.py"] == b'V = "A"\n', "取到了工作树字节"

        # 暂存后索引变了，成员随之变化
        import subprocess

        subprocess.run(["git", "add", "core/version.py"], cwd=str(repo), check=True)
        assert sync_mod.collect_members()["core/version.py"] == b'V = "B"\n'
    finally:
        _restore_repo_root(saved)
        import shutil

        shutil.rmtree(repo, ignore_errors=True)


def test_untracked_and_ignored_files_never_enter_members():
    """F1：未跟踪 / 被忽略 / 源符号链接 / CLI 状态目录都不进成员集合。"""
    repo = _make_git_repo({"core/version.py": "V = 1\n", ".gitignore": ".env\n"})
    saved = _patch_repo_root(repo)
    try:
        (repo / ".env").write_text("TOKEN=example\n", encoding="utf-8")
        (repo / ".env.production").write_text("TOKEN=example2\n", encoding="utf-8")
        (repo / "private_notes.txt").write_text("note\n", encoding="utf-8")
        (repo / ".local" / "state" / "gh").mkdir(parents=True)
        (repo / ".local" / "state" / "gh" / "device-id").write_text("uuid\n", encoding="utf-8")
        try:
            (repo / "linked_secret.txt").symlink_to(repo / ".env")
            link_ok = True
        except (OSError, NotImplementedError):
            link_ok = False

        members = sync_mod.collect_members()
        assert "core/version.py" in members
        for bad in (".env", ".env.production", "private_notes.txt",
                    ".local/state/gh/device-id"):
            assert bad not in members, f"未跟踪/被忽略文件进入了成员集合: {bad}"
        if link_ok:
            assert "linked_secret.txt" not in members, "未跟踪的源符号链接进入了成员集合"
    finally:
        _restore_repo_root(saved)
        import shutil

        shutil.rmtree(repo, ignore_errors=True)


def test_tracked_symlink_is_rejected_fail_closed():
    """F1：已跟踪的符号链接必须让成员选择直接失败，而不是被当作普通文件复制。"""
    repo = _make_git_repo({"core/version.py": "V = 1\n"})
    try:
        link = repo / "linked.py"
        try:
            link.symlink_to(repo / "core" / "version.py")
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建符号链接")
        import subprocess

        subprocess.run(["git", "add", "-A"], cwd=str(repo), check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             "commit", "-q", "-m", "link"],
            cwd=str(repo), check=True,
        )
        saved = _patch_repo_root(repo)
        try:
            try:
                sync_mod.collect_members()
            except Exception as exc:  # 构建器抛 BuildError（不是 SyncError）
                assert "SYMLINK" in str(exc) or "符号链接" in str(exc) or "GIT" in str(exc), str(exc)
            else:
                raise AssertionError("已跟踪的符号链接未被拒绝")
        finally:
            _restore_repo_root(saved)
    finally:
        import shutil

        shutil.rmtree(repo, ignore_errors=True)


def test_target_subdir_symlink_is_rejected_and_not_written_through():
    """F2：目标子目录是指向外部目录的链接时，必须拒绝且不得写到链接外。"""
    repo = _make_git_repo({"core/version.py": "NEW_MIRROR_CONTENT\n"})
    base = _tmp()
    mirror = base / "mirror"
    protected = base / "protected"
    mirror.mkdir()
    protected.mkdir()
    victim = protected / "version.py"
    victim.write_text("KEEP_ORIGINAL\n", encoding="utf-8")
    try:
        try:
            (mirror / "core").symlink_to(protected, target_is_directory=True)
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建目录符号链接")

        saved = _patch_repo_root(repo)
        try:
            trusted = sync_mod.validate_dest(mirror, [base.resolve()])
            try:
                sync_mod.sync_to(trusted, no_prune=True)
            except sync_mod.SyncError as exc:
                assert "符号链接" in str(exc) or "junction" in str(exc), str(exc)
            else:
                raise AssertionError("目标子目录符号链接未被拒绝")
            assert victim.read_text(encoding="utf-8") == "KEEP_ORIGINAL\n", "写穿到了受保护目录"
            # 预检在任何写入之前生效：排序在前面的成员也不应被写入
            assert not (mirror / ".gitignore").exists(), "失败前已写入部分成员（半更新镜像）"
        finally:
            _restore_repo_root(saved)
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)


def test_ds_store_is_excluded_by_exact_name():
    """F4：.DS_Store 没有后缀，必须按精确文件名排除。"""
    from scripts import _dist_view as dv

    assert ".DS_Store" not in dv.EXCLUDE_EXTS, ".DS_Store 仍留在后缀表（永远匹配不到）"
    assert ".DS_Store" in dv.EXCLUDE_FILES
    assert dv.is_file_excluded(".DS_Store") is True
    assert dv.is_file_excluded("core/.DS_Store") is True


def test_prune_survives_path_spelling_mismatch():
    """清理越界校验必须两侧同口径。

    Windows runner 上 ``tempfile.mkdtemp()`` 会返回 8.3 短路径
    （``<user-home>\\RUNNER~1\\...``），而 ``Path.resolve()`` 返回长路径
    （``<user-home>\\runneradmin\\...``）；若只对 victim 做 resolve、拿原始 target
    去 ``relative_to``，比较必然失败，结果是**所有陈旧文件都被跳过、清理静默失效**。
    这里用「符号链接父目录」在任何平台复现同一类路径拼写不一致。
    """
    import shutil

    base = _tmp()
    real = base / "real"
    real.mkdir()
    link = base / "link"
    try:
        try:
            link.symlink_to(real, target_is_directory=True)
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建目录符号链接")

        target = link / "mirror"          # 经过链接访问，拼写与 resolve 结果不同
        target.mkdir()
        stale = target / "old_report.md"
        stale.write_text("stale\n", encoding="utf-8")

        stats = sync_mod.sync_to(target, adopt_existing=True)
        assert stats["pruned"] >= 1, stats
        assert not stale.exists(), "陈旧文件未被清理（越界校验把合法路径误判为越界）"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_predictable_tmp_symlink_cannot_redirect_write():
    """固定名临时文件被预先布置成符号链接时，不得把内容写到链接外。"""
    import shutil

    base = _tmp()
    mirror = base / "mirror"
    mirror.mkdir()
    outside = base / "external-private.txt"
    outside.write_bytes(b"KEEP_PRIVATE")
    try:
        try:
            (mirror / "SKILL.md.mtl-tmp").symlink_to(outside)
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建符号链接")

        sync_mod._write_member(mirror, mirror / "SKILL.md", b"NEW_RELEASE_BYTES")
        assert outside.read_bytes() == b"KEEP_PRIVATE", "临时文件符号链接把内容写到了镜像之外"
        assert (mirror / "SKILL.md").read_bytes() == b"NEW_RELEASE_BYTES"
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_manifest_symlink_is_rejected_and_not_written_through():
    """受管清单路径是符号链接时必须 fail-closed，且不得修改链接外内容。"""
    import shutil

    base = _tmp()
    mirror = base / "mirror"
    mirror.mkdir()
    outside = base / "external-private.txt"
    outside.write_text("KEEP_PRIVATE\n", encoding="utf-8")
    saved = sync_mod.collect_members
    sync_mod.collect_members = lambda: {"SKILL.md": b"NEW_RELEASE_BYTES"}
    try:
        try:
            (mirror / sync_mod.MANIFEST_NAME).symlink_to(outside)
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建符号链接")

        try:
            sync_mod.sync_to(mirror, no_prune=True)
        except sync_mod.SyncError as exc:
            assert "受管清单" in str(exc), str(exc)
        else:
            raise AssertionError("受管清单符号链接未被拒绝")
        assert outside.read_text(encoding="utf-8") == "KEEP_PRIVATE\n", "清单写入穿到了镜像之外"
    finally:
        sync_mod.collect_members = saved
        shutil.rmtree(base, ignore_errors=True)


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
