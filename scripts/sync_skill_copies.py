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
    valid_rel_files = set()

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

            rel_file = rel_root / file if str(rel_root) != "." else Path(file)
            valid_rel_files.add(str(rel_file).replace("\\", "/"))

            src_file = Path(root) / file
            dest_file = dest_subdir / file
            shutil.copy2(src_file, dest_file)
            copied_count += 1

    # 清理目标目录中多余/过期的陈旧文件与被排除目录
    pruned_count = 0
    for root, dirs, files in os.walk(target_dir, topdown=False):
        rel_root = Path(root).relative_to(target_dir)
        for file in files:
            rel_file = rel_root / file if str(rel_root) != "." else Path(file)
            rel_str = str(rel_file).replace("\\", "/")
            if rel_str not in valid_rel_files:
                try:
                    (Path(root) / file).unlink()
                    pruned_count += 1
                except OSError:
                    pass
        for d in dirs:
            dir_path = Path(root) / d
            if d in EXCLUDE_DIRS or d.startswith(".test-work") or d.startswith("_baseline"):
                shutil.rmtree(dir_path, ignore_errors=True)
            elif dir_path.is_dir() and not any(dir_path.iterdir()):
                try:
                    dir_path.rmdir()
                except OSError:
                    pass

    return copied_count, pruned_count


def main():
    print("=" * 60)
    print("🔄 正在执行 Skill 权威副本与可调用副本纯净同步")
    print("=" * 60)

    n1, p1 = sync_to(DEST_AGENT)
    print(f"✅ 已同步至 Antigravity 可调用副本: {DEST_AGENT} (同步 {n1} 个文件，清理 {p1} 个陈旧文件)")

    n2, p2 = sync_to(DEST_AUTHORITATIVE)
    print(f"✅ 已同步至纯净权威安装包: {DEST_AUTHORITATIVE} (同步 {n2} 个文件，清理 {p2} 个陈旧文件)")

    print("🎉 Skill 三地同步全部完成！")


if __name__ == "__main__":
    main()
