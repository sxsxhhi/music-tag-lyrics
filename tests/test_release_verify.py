# -*- coding: utf-8 -*-
"""H14 验收：--verify-release 本地篡改必失败、dev 包不被认作正式、远端未验证如实标注。"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
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
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        env=env,
        stderr=subprocess.DEVNULL,
    )


def _make_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="mtl_verify_"))
    (root / "core").mkdir()
    (root / "scripts").mkdir()
    shutil.copy2(VERSION_PY, root / "core" / "version.py")
    shutil.copy2(DIST_VIEW, root / "scripts" / "_dist_view.py")
    shutil.copy2(BUILDER, root / "scripts" / "build_release_manifest.py")
    (root / "README.md").write_text("# sample\n", encoding="utf-8", newline="\n")
    (root / "core" / "a.py").write_text("A = 1\n", encoding="utf-8", newline="\n")
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


def _zip_path(repo: Path) -> Path:
    return repo / "dist" / "music-tag-lyrics-skill-v1.8.1.zip"


def test_verify_positive_and_remote_unverified_note():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        proc = _run(repo, "--verify-release")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "远端未验证" in proc.stdout, "未如实标注远端未验证"
        manifest = json.loads((repo / "release-manifest.json").read_text(encoding="utf-8"))
        assert isinstance(manifest.get("members"), dict) and manifest["members"], "manifest 未写成员哈希"
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_verify_detects_tampered_member():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        zip_path = _zip_path(repo)
        # 重建一个被篡改成员的 ZIP（保持成员名不变）
        tmp = zip_path.with_suffix(".tampered")
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(tmp, "w") as dst:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "core/a.py":
                    data = b"A = 999  # tampered\n"
                dst.writestr(info, data)
        tmp.replace(zip_path)

        proc = _run(repo, "--verify-release")
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "不一致" in proc.stdout or "篡改" in proc.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_verify_detects_extra_member():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        zip_path = _zip_path(repo)
        tmp = zip_path.with_suffix(".extra")
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(tmp, "w") as dst:
            for info in src.infolist():
                dst.writestr(info, src.read(info.filename))
            dst.writestr("core/injected.py", b"EVIL = 1\n")
        tmp.replace(zip_path)

        proc = _run(repo, "--verify-release")
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "多余成员" in proc.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_verify_rejects_dev_package():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        (repo / "core" / "a.py").write_text("A = 2\n", encoding="utf-8", newline="\n")
        assert _run(repo, "--dev-package").returncode == 0
        dev_zip = repo / "dist" / "dev" / "music-tag-lyrics-skill-v1.8.1.zip"
        dev_manifest = repo / "dist" / "dev" / "release-manifest.dev.json"
        proc = _run(
            repo, "--verify-release", "--zip", str(dev_zip), "--manifest", str(dev_manifest)
        )
        assert proc.returncode == 1, "开发包被当成正式发布物"
        assert "DEV PACKAGE" in proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_verify_against_zip_identical_and_different():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        zip_path = _zip_path(repo)
        copy = repo / "dist" / "downloaded.zip"
        shutil.copy2(zip_path, copy)

        proc = _run(repo, "--verify-release", "--against-zip", str(copy))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "成员逐一一致" in proc.stdout
        assert "远端未验证" not in proc.stdout

        # 对照包内容不同 → 失败
        other = repo / "dist" / "other.zip"
        with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(other, "w") as dst:
            for info in src.infolist():
                data = src.read(info.filename)
                if info.filename == "README.md":
                    data = b"# different\n"
                dst.writestr(info, data)
        proc = _run(repo, "--verify-release", "--against-zip", str(other))
        assert proc.returncode == 1
        assert "对照 ZIP 与本包内容不同" in proc.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_verify_missing_inputs():
    repo = _make_repo()
    try:
        assert _run(repo, "--release").returncode == 0
        proc = _run(repo, "--verify-release", "--zip", str(repo / "nope.zip"))
        assert proc.returncode == 2
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
