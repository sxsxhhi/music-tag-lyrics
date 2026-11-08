# -*- coding: utf-8 -*-
"""路径与个人信息泄漏守卫。

在公开仓库与发布包中拦截本机环境信息：绝对路径、用户名、私有目录名、个人邮箱。

用法：
    python scripts/check_no_leaks.py                 # 扫描仓库（git 索引优先）
    python scripts/check_no_leaks.py --zip dist/xxx.zip   # 扫描发布包内文本成员
    python scripts/check_no_leaks.py --verbose       # 额外打印已放行的命中

退出码：0 = 干净；1 = 发现泄漏。

设计要点：
  * 禁止串用字符串拼接构造，避免守卫自身成为泄漏样本；脚本自身始终排除。
  * 文档中的占位符（<workspace> / <repo> / <mother-library> 等）整行放行。
  * 仅保留两类合法 Windows 绝对路径：chocolatey 与 ffmpeg 的公共安装目录。
  * 测试用虚构盘符（Z:）与假用户名（fake/example/...）放行。
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_REL = "scripts/check_no_leaks.py"

# ---- 本机指纹：字符串拼接构造，避免本文件命中自身 -------------------------
_DENY_PARTS = [
    ("AI", "'s DATA"),
    ("TOOLs", "\\"),
    ("TOOLs", "/"),
    ("Windows", "Appp"),
    ("只", "音"),
    ("待", "修补"),
    ("My ", "skill"),
    ("sxh", "20"),
    ("sxhxu", "@qq.com"),
    ("957451576", "@qq.com"),
    ("RTX ", "3060"),
    ("AI_Audio", "_Env"),
]
DENY_SUBSTRINGS = ["".join(p) for p in _DENY_PARTS]

# 文档占位符：命中即整行放行
PLACEHOLDER_RE = re.compile(
    r"<(?:workspace|repo|hf-cache|user-home|mother-library|test-library|process-dir)>"
)

# 盘符绝对路径（前面不能紧跟字母数字，避免误伤 "Source:\\s*(" 这类正则）
DRIVE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:(?:\\|/)[^\s`'\"<>|*?]{2,}")

# 用户目录
USERPROFILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}([A-Za-z0-9._-]+)"
)

# 仓库内仅允许的公共工具安装路径（与 core/acoustic_aligner.py 的 ffmpeg 探测一致）
SAFE_PREFIXES = (
    "c:\\programdata\\chocolatey\\bin",
    "c:\\ffmpeg\\bin",
)
# 虚构盘符（测试用）
SAFE_DRIVES = {"z"}
# 用户目录白名单
SAFE_USERNAMES = {"fake", "example", "user", "public", "runner", "username"}

TEXT_EXTS = {
    ".py", ".md", ".json", ".yml", ".yaml", ".txt", ".csv", ".lrc", ".ttml",
    ".jsonl", ".ini", ".cfg", ".toml", ".sh", ".bat", ".ps1", ".svg", ".hex",
    ".gitignore", ".gitattributes",
}

# 与 _dist_view 保持一致的基础排除
SKIP_DIRS = {
    ".git", ".github", ".test-work", ".cache", "__pycache__", ".pytest_cache",
    "dist", ".venv", "venv", "env", ".mypy_cache", ".ruff_cache",
}


def _is_text(name: str) -> bool:
    base = os.path.basename(name)
    if base in (".gitignore", ".gitattributes"):
        return True
    return os.path.splitext(base)[1].lower() in TEXT_EXTS


def _norm(p: str) -> str:
    return p.replace("/", "\\").lower()


def _path_allowed(p: str) -> bool:
    n = _norm(p)
    if n[:2] in {d + ":" for d in SAFE_DRIVES}:
        return True
    if any(n.startswith(prefix) for prefix in SAFE_PREFIXES):
        return True
    # 测试用假用户名目录（C:/Users/fake/...）放行
    m = re.match(r"(?i)^[a-z]:[\\/]+users[\\/]+([a-z0-9._-]+)", n)
    if m and m.group(1) in SAFE_USERNAMES:
        return True
    return False


def scan_text(text: str, label: str, hits: list, allowed: list, verbose: bool) -> None:
    for lineno, line in enumerate(text.splitlines(), 1):
        if PLACEHOLDER_RE.search(line):
            if verbose:
                allowed.append((label, lineno, "placeholder", line.strip()[:100]))
            continue

        for bad in DENY_SUBSTRINGS:
            if bad in line:
                hits.append((label, lineno, "denylist:" + bad, line.strip()[:160]))
                break

        for m in USERPROFILE_RE.finditer(line):
            if m.group(1).lower() not in SAFE_USERNAMES:
                hits.append((label, lineno, "user-profile", line.strip()[:160]))
                break

        for m in DRIVE_RE.finditer(line):
            if not _path_allowed(m.group(0)):
                hits.append((label, lineno, "drive-path:" + m.group(0)[:40], line.strip()[:160]))
                break


def iter_repo_files():
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL
        )
        names = [n for n in out.decode("utf-8", "ignore").split("\0") if n]
        if names:
            return [REPO_ROOT / n for n in names]
    except Exception:
        pass
    files = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".test-work")]
        for n in names:
            files.append(Path(root) / n)
    return files


def scan_repo(verbose: bool):
    hits, allowed = [], []
    for path in iter_repo_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel == SELF_REL or not _is_text(rel) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scan_text(text, rel, hits, allowed, verbose)
    return hits, allowed


def scan_zip(zip_path: Path, verbose: bool):
    hits, allowed = [], []
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not _is_text(name):
                continue
            try:
                text = zf.read(name).decode("utf-8", "ignore")
            except Exception:
                continue
            scan_text(text, f"{zip_path.name}!{name}", hits, allowed, verbose)
    return hits, allowed


def main() -> int:
    ap = argparse.ArgumentParser(description="路径与个人信息泄漏守卫")
    ap.add_argument("--zip", default="", help="扫描指定 zip 内的文本成员")
    ap.add_argument("--verbose", action="store_true", help="打印被放行的命中")
    args = ap.parse_args()

    if args.zip:
        target = Path(args.zip)
        if not target.is_file():
            print(f"ERROR: 找不到 {target}")
            return 2
        hits, allowed = scan_zip(target, args.verbose)
        scope = f"发布包 {target.name}"
    else:
        hits, allowed = scan_repo(args.verbose)
        scope = f"仓库 {REPO_ROOT.name}"

    if args.verbose and allowed:
        print(f"ℹ️  已放行 {len(allowed)} 处（占位符）")

    if not hits:
        print(f"✅ 泄漏守卫通过（{scope}）：未发现本机路径或个人信息")
        return 0

    print(f"❌ 泄漏守卫失败（{scope}）：命中 {len(hits)} 处")
    for label, lineno, rule, snippet in hits:
        print(f"  {label}:{lineno}  [{rule}]  {snippet}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
