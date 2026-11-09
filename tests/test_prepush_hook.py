# -*- coding: utf-8 -*-
"""pre-push 钩子 H02 验收：公开模式通用扫描、可信模式 fail-closed、Python 探测链。

全部在临时仓库中模拟，**不执行任何真实 push**（钩子本身不发起网络操作）。
"""

import os
import shutil
import subprocess
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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

HOOK_SRC = REPO_ROOT / "scripts" / "git-hooks" / "pre-push"
SCANNER_SRC = REPO_ROOT / "scripts" / "check_no_leaks.py"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "mtl-test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "mtl-test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}

SYNTH_LEAK = "Q:" + chr(92) + "corpus" + chr(92) + "song.flac"


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


def _make_repo(leaky: bool = False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="mtl_hook_"))
    (root / "scripts" / "git-hooks").mkdir(parents=True)
    shutil.copy2(SCANNER_SRC, root / "scripts" / "check_no_leaks.py")
    shutil.copy2(HOOK_SRC, root / "scripts" / "git-hooks" / "pre-push")
    body = f"clean text\n"
    if leaky:
        body = f"数据源 {SYNTH_LEAK}\n"
    (root / "README.md").write_text(body, encoding="utf-8", newline="\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _find_sh() -> str:
    s = shutil.which("sh")
    if s:
        return s
    git = shutil.which("git")
    if git:
        for sub in ("bin", "usr/bin"):
            candidate = Path(git).resolve().parent.parent / sub / "sh.exe"
            if candidate.is_file():
                return str(candidate)
    return "sh"


def _run_hook(repo: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("MTL_PRIVATE_FINGERPRINTS_FILE", None)
    env.pop("MTL_PYTHON", None)
    env.update(GIT_ENV)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [_find_sh(), str(repo / "scripts" / "git-hooks" / "pre-push")],
        cwd=str(repo),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )


def _private_file(text: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="mtl_hook_fp_"))
    p = d / "fp.txt"
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def test_public_clone_requires_no_private_config():
    """公开克隆（无 trustedMaintainer）只跑通用规则，不要求私有词表。"""
    repo = _make_repo()
    try:
        proc = _run_hook(repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_public_clone_still_blocks_generic_leak():
    """公开克隆模式下通用规则命中仍然阻断。"""
    repo = _make_repo(leaky=True)
    try:
        proc = _run_hook(repo)
        assert proc.returncode != 0, "通用泄漏未阻断"
        assert "推送被阻断" in proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_trusted_without_private_config_fails_closed():
    """可信模式缺私有配置必须阻断，不得降级为通用扫描。"""
    repo = _make_repo()
    try:
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        proc = _run_hook(repo)
        assert proc.returncode != 0, "可信模式缺私有配置竟放行"
        assert "可信维护者模式要求私有指纹门禁通过" in proc.stderr, proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_trusted_with_valid_private_config_passes():
    """可信模式 + 合法私有词表且仓库干净 → 放行。"""
    repo = _make_repo()
    try:
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        fp = _private_file("segment:synthprivcorpus\n")
        _git(repo, "config", "--local", "mtl.privateFingerprintsFile", str(fp))
        proc = _run_hook(repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_trusted_hits_private_entry():
    """可信模式下私有词条命中必须阻断。"""
    repo = _make_repo()
    try:
        (repo / "notes.md").write_text("路径 synthprivcorpus/子目录\n", encoding="utf-8", newline="\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "leak")
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        fp = _private_file("segment:synthprivcorpus\n")
        _git(repo, "config", "--local", "mtl.privateFingerprintsFile", str(fp))
        proc = _run_hook(repo)
        assert proc.returncode != 0
        assert "PRIVATE_SEGMENT" in proc.stdout
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_trusted_rejects_unaudited_short_entry():
    """私有词表含未审核短词 → 可信模式阻断。"""
    repo = _make_repo()
    try:
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        fp = _private_file("literal:ab\n")
        _git(repo, "config", "--local", "mtl.privateFingerprintsFile", str(fp))
        proc = _run_hook(repo)
        assert proc.returncode != 0
        assert "未经审核的短词条" in proc.stderr, proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_python_probe_validates_executability():
    """MTL_PYTHON 指向不可执行路径时探测链继续回退，不直接采信。"""
    repo = _make_repo()
    try:
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        fp = _private_file("segment:synthprivcorpus\n")
        _git(repo, "config", "--local", "mtl.privateFingerprintsFile", str(fp))
        proc = _run_hook(repo, {"MTL_PYTHON": "/nonexistent/dir/python"})
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_trusted_missing_scanner_fails_closed():
    """守卫脚本缺失时可信模式必须阻断。"""
    repo = _make_repo()
    try:
        (repo / "scripts" / "check_no_leaks.py").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "drop scanner")
        _git(repo, "config", "--local", "mtl.trustedMaintainer", "true")
        proc = _run_hook(repo)
        assert proc.returncode != 0
        assert "找不到守卫脚本" in proc.stderr, proc.stderr
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_hook_does_not_use_shell_eval_of_paths():
    """钩子源码不得用字符串拼接 eval 方式调用解释器。"""
    text = HOOK_SRC.read_text(encoding="utf-8")
    assert "eval " not in text, "钩子中出现 eval"
    assert '"$PY" "$SCANNER_REL"' in text, "解释器与脚本必须以变量引用方式调用"


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
