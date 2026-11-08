# -*- coding: utf-8 -*-
r"""Synchronize clean skill copies according to AGENTS.md discipline.

Source (only git repo):
  F:\TOOLs\AI's DATA\ABC\Music\Work\music-tag-lyrics\

Destinations:
  1. F:\TOOLs\AI's DATA\ABC\Music\.agent\skills\music-tag-lyrics\
  2. F:\TOOLs\My skill\歌曲歌词修补skill\music-tag-lyrics\
"""

import os
import sys
import shutil
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEST_AGENT = Path(r"F:\TOOLs\AI's DATA\ABC\Music\.agent\skills\music-tag-lyrics")
DEST_AUTHORITATIVE = Path(r"F:\TOOLs\My skill\歌曲歌词修补skill\music-tag-lyrics")

EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".test-work",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "_baseline",
    "_baseline_dump",
    ".venv",
    "venv",
    "env",
}

EXCLUDE_EXTS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
}

EXCLUDE_FILES = {
    "Thumbs.db",
    "desktop.ini",
}


def sync_to(target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    copied_count = 0

    for root, dirs, files in os.walk(REPO_ROOT):
        # 排除忽略目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".test-work") and not d.startswith("_baseline")]

        rel_root = Path(root).relative_to(REPO_ROOT)
        dest_subdir = target_dir / rel_root
        dest_subdir.mkdir(parents=True, exist_ok=True)

        for file in files:
            if file in EXCLUDE_FILES:
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in EXCLUDE_EXTS:
                continue
            if file.endswith(".zip") and str(rel_root).startswith("dist"):
                continue

            src_file = Path(root) / file
            dest_file = dest_subdir / file
            shutil.copy2(src_file, dest_file)
            copied_count += 1

    return copied_count


def main():
    print("=" * 60)
    print("🔄 正在执行 Skill 权威副本与可调用副本纯净同步")
    print("=" * 60)

    n1 = sync_to(DEST_AGENT)
    print(f"✅ 已同步至 Antigravity 可调用副本: {DEST_AGENT} ({n1} 个文件)")

    n2 = sync_to(DEST_AUTHORITATIVE)
    print(f"✅ 已同步至纯净权威安装包: {DEST_AUTHORITATIVE} ({n2} 个文件)")

    print("🎉 Skill 三地同步全部完成！")


if __name__ == "__main__":
    main()
