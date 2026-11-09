# -*- coding: utf-8 -*-
"""发布构建器 H01 验收：Git 已跟踪成员源、安全过滤、确定性 ZIP、Manifest 幂等复用。

全部用例在临时迷你 git 仓库中执行，不触碰真实发布物与真实曲库。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

BUILDER = REPO_ROOT / "scripts" / "build_release_manifest.py"
DIST_VIEW = REPO_ROOT / "scripts" / "_dist_view.py"
VERSION_PY = REPO_ROOT / "core" / "version.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "mtl-test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mtl-test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ)
    env.update(GIT_ENV)
    return subprocess.check_output(
        ["git", "-c", "core.autocrlf=false", "-c", "core.safecrlf=false", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        env=env,
        stderr=subprocess.DEVNULL,
    )


def _make_repo(files: dict[str, str] | None = None) -> Path:
    """建一个带 .git 的迷你仓库，初始提交干净。"""
    root = Path(tempfile.mkdtemp(prefix="mtl_relbuild_"))
    (root / "core").mkdir()
    (root / "scripts").mkdir()
    shutil.copy2(VERSION_PY, root / "core" / "version.py")
    shutil.copy2(DIST_VIEW, root / "scripts" / "_dist_view.py")
    shutil.copy2(BUILDER, root / "scripts" / "build_release_manifest.py")
    (root / "README.md").write_text("# sample\n", encoding="utf-8", newline="\n")
    payload = files or {}
    for rel, body in payload.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", str(repo / "scripts" / "build_release_manifest.py"), *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return [i.filename for i in zf.infolist()]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cleanup(*repos: Path) -> None:
    for r in repos:
        shutil.rmtree(r, ignore_errors=True)


# --------------------------------------------------------------------------- #
# 1. 未跟踪文件绝不入包
# --------------------------------------------------------------------------- #
def test_untracked_files_never_enter_package():
    """未跟踪的高危/未知文件不进入正式包，也不进入开发包。"""
    repo = _make_repo()
    try:
        for name in ("private-test.env", "secret.key", "data.sqlite", "unknown.xyz"):
            (repo / name).write_text("untracked\n", encoding="utf-8", newline="\n")

        rel = _run(repo, "--release")
        assert rel.returncode == 0, rel.stderr
        names = _zip_names(repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip")
        for bad in ("private-test.env", "secret.key", "data.sqlite", "unknown.xyz"):
            assert bad not in names, f"未跟踪文件进入正式包: {bad}"

        dev = _run(repo, "--dev-package")
        assert dev.returncode == 0, dev.stderr
        dev_names = _zip_names(repo / "dist" / "dev" / "music-tag-lyrics-skill-v1.8.1.zip")
        for bad in ("private-test.env", "secret.key", "data.sqlite", "unknown.xyz"):
            assert bad not in dev_names, f"未跟踪文件进入开发包: {bad}"
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 2. 已跟踪高危文件 → 构建失败并给出规则 ID
# --------------------------------------------------------------------------- #
def test_tracked_risky_file_aborts_build():
    """已跟踪的 .env / 私钥类文件必须让构建非零退出并输出规则 ID。"""
    repo = _make_repo({"config/.env.local": "TOKEN=dummy\n"})
    try:
        proc = _run(repo, "--release")
        assert proc.returncode != 0, "已跟踪 .env 竟构建成功"
        assert "LEAK_RISKY_ENV_FILE" in proc.stderr, proc.stderr
        assert "config/.env.local" in proc.stderr
        assert not (repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip").exists()
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 3. 正式包拒绝脏树；开发包放行并标记 dirty
# --------------------------------------------------------------------------- #
def test_release_rejects_dirty_tree_dev_allows():
    """正式包要求干净工作树；开发包放行但写 dist/dev/ 并标 dirty=true。"""
    repo = _make_repo({"core/mod.py": "X = 1\n"})
    try:
        (repo / "core" / "mod.py").write_text("X = 2\n", encoding="utf-8", newline="\n")

        rel = _run(repo, "--release")
        assert rel.returncode == 3, f"脏树正式构建应返回 3，实际 {rel.returncode}"
        assert "拒绝构建" in rel.stdout
        assert not (repo / "release-manifest.json").exists()

        dev = _run(repo, "--dev-package")
        assert dev.returncode == 0, dev.stderr
        dev_manifest = repo / "dist" / "dev" / "release-manifest.dev.json"
        assert dev_manifest.is_file(), "开发包未写 dist/dev/ manifest"
        data = json.loads(dev_manifest.read_text(encoding="utf-8"))
        assert data["dirty"] is True
        assert "core/mod.py" in data["changed_paths"]
        # 开发模式不得产生正式 Manifest
        assert not (repo / "release-manifest.json").exists()
        assert not (repo / "docs" / "release-manifest.json").exists()
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 4. 索引文件在磁盘上缺失 → 明确拒绝
# --------------------------------------------------------------------------- #
def test_missing_index_file_rejected():
    """索引里有、磁盘上没有的文件必须让构建失败，而不是静默跳过。"""
    repo = _make_repo({"core/ghost.py": "Y = 1\n"})
    try:
        (repo / "core" / "ghost.py").unlink()
        proc = _run(repo, "--dev-package")
        assert proc.returncode != 0
        assert "INDEX_FILE_MISSING" in proc.stderr, proc.stderr
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 5. 成员完整、唯一、可复现
# --------------------------------------------------------------------------- #
def test_members_complete_and_unique():
    """ZIP 成员与预期集合完全一致且无重复。"""
    repo = _make_repo({"core/a.py": "A = 1\n", "core/b.py": "B = 2\n"})
    try:
        proc = _run(repo, "--release")
        assert proc.returncode == 0, proc.stderr
        names = _zip_names(repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip")
        assert len(names) == len(set(names)), "ZIP 成员重复"
        assert names == sorted(names), "ZIP 成员未按稳定顺序排列"
        assert "core/a.py" in names and "core/b.py" in names
        assert "README.md" in names
        # 打包脚本自身按既有排除规则不入包
        assert "scripts/build_release_manifest.py" not in names
        assert "scripts/_dist_view.py" not in names
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 6. 幂等闭环：A 构建 → 提交双 Manifest → B 再构建 → SHA/字节不变 + 工作树干净
# --------------------------------------------------------------------------- #
def test_idempotent_build_loop():
    """A→提交→B→再构建：ZIP SHA 与两份 Manifest 字节完全不变。"""
    repo = _make_repo({"core/a.py": "A = 1\n"})
    try:
        first = _run(repo, "--release")
        assert first.returncode == 0, first.stderr
        zip_path = repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip"
        sha1 = _sha(zip_path)
        m1 = (repo / "release-manifest.json").read_bytes()
        d1 = (repo / "docs" / "release-manifest.json").read_bytes()
        assert m1 == d1, "两份 Manifest 内容不一致"

        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "lock manifest")
        assert _git(repo, "status", "--porcelain").strip() == "", "提交后工作树仍不干净"

        second = _run(repo, "--release")
        assert second.returncode == 0, second.stderr
        assert _sha(zip_path) == sha1, "二次构建 ZIP SHA 变化"
        assert (repo / "release-manifest.json").read_bytes() == m1, "二次构建覆盖了 Manifest"
        assert (repo / "docs" / "release-manifest.json").read_bytes() == d1
        assert _git(repo, "status", "--porcelain").strip() == "", "二次构建把工作树弄脏了"
        assert "复用" in second.stdout, second.stdout
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 7. 源文件 mtime 变化不影响 ZIP 字节
# --------------------------------------------------------------------------- #
def test_zip_bytes_independent_of_mtime():
    """touch 源文件后重建，ZIP SHA 必须不变。"""
    repo = _make_repo({"core/a.py": "A = 1\n"})
    try:
        assert _run(repo, "--release").returncode == 0
        zip_path = repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip"
        sha1 = _sha(zip_path)

        future = time.time() + 86400
        os.utime(repo / "core" / "a.py", (future, future))
        proc = _run(repo, "--release")
        assert proc.returncode == 0, proc.stderr
        assert _sha(zip_path) == sha1, "源文件 mtime 影响了 ZIP 字节"
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 8. ZIP 元数据归一化
# --------------------------------------------------------------------------- #
def test_zipinfo_normalized():
    """所有成员固定时间戳、Unix 创建系统、无 extra/comment、权限归一。"""
    repo = _make_repo({"core/a.py": "A = 1\n"})
    try:
        assert _run(repo, "--release").returncode == 0
        with zipfile.ZipFile(repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip") as zf:
            for info in zf.infolist():
                assert info.date_time == (1980, 1, 1, 0, 0, 0), info.filename
                assert info.create_system == 3, info.filename
                assert info.extra == b"", info.filename
                assert info.comment == b"", info.filename
                assert (info.external_attr >> 16) & 0o170000 == 0o100000, info.filename
                assert (info.external_attr >> 16) & 0o777 == 0o644, info.filename
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 9. 单份 Manifest 缺失 → 重建并同时写两份
# --------------------------------------------------------------------------- #
def test_single_manifest_missing_rebuilds_both():
    """docs/ 下那份被删时，必须重建并同时写两份，不能只补一份。"""
    repo = _make_repo({"core/a.py": "A = 1\n"})
    try:
        assert _run(repo, "--release").returncode == 0
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "lock")
        (repo / "docs" / "release-manifest.json").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "drop docs manifest")

        proc = _run(repo, "--release")
        assert proc.returncode == 0, proc.stderr
        a = repo / "release-manifest.json"
        b = repo / "docs" / "release-manifest.json"
        assert b.is_file(), "单份缺失时未重建 docs 侧 Manifest"
        assert a.read_bytes() == b.read_bytes()
    finally:
        _cleanup(repo)


# --------------------------------------------------------------------------- #
# 10. 单元层：路径越界与符号链接由 select_members 拦截
# --------------------------------------------------------------------------- #
def test_select_members_rejects_traversal_and_symlink():
    """越界路径与符号链接必须在成员选择阶段被拒绝。"""
    sys.path.insert(0, str(REPO_ROOT))
    from scripts import build_release_manifest as mod

    with patch.object(mod, "list_tracked_entries", return_value=[("100644", "../escape.py")]):
        try:
            mod.select_members()
        except mod.BuildError as exc:
            assert "PATH_TRAVERSAL_REJECTED" in str(exc), str(exc)
        else:
            raise AssertionError("路径穿越未被拒绝")

    repo = _make_repo()
    try:
        link = repo / "core" / "link.py"
        try:
            link.symlink_to(repo / "README.md")
        except (OSError, NotImplementedError):
            _testkit.skip("当前环境不支持创建符号链接")
        _git(repo, "add", "-A")
        proc = _run(repo, "--dev-package")
        assert proc.returncode != 0
        assert "SYMLINK_REJECTED" in proc.stderr, proc.stderr
    finally:
        _cleanup(repo)


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
