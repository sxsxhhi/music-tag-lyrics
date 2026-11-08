# -*- coding: utf-8 -*-
r"""Synchronize clean skill copies according to AGENTS.md discipline.

Source: this git repository (resolved from __file__).

Destinations, resolved in this order:
  1. env MTL_DEST_AGENT          -- callable copy inside the agent workspace
  2. default: <workspace>/.agent/skills/music-tag-lyrics
     (<workspace> = REPO_ROOT.parent.parent, i.e. the directory holding Work/)
  3. env MTL_DEST_AUTHORITATIVE  -- external authoritative install; no default,
     skipped with a hint when unset.

No destination is hard-coded, so the script is portable across machines.
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
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts._dist_view import is_dir_excluded, is_file_excluded

_env_agent = os.environ.get("MTL_DEST_AGENT")
DEST_AGENT = Path(_env_agent) if _env_agent else (
    REPO_ROOT.parent.parent / ".agent" / "skills" / "music-tag-lyrics"
)
_env_auth = os.environ.get("MTL_DEST_AUTHORITATIVE")
DEST_AUTHORITATIVE = Path(_env_auth) if _env_auth else None


def sync_to(target_dir: Path):
    target_dir.mkdir(parents=True, exist_ok=True)
    copied_count = 0
    valid_rel_files = set()

    for root, dirs, files in os.walk(REPO_ROOT):
        # 排除忽略目录
        dirs[:] = [d for d in dirs if not is_dir_excluded(d)]

        rel_root = Path(root).relative_to(REPO_ROOT)
        dest_subdir = target_dir / rel_root
        dest_subdir.mkdir(parents=True, exist_ok=True)

        for file in sorted(files):
            rel_file = rel_root / file if str(rel_root) != "." else Path(file)
            rel_str = str(rel_file).replace("\\", "/")

            if is_file_excluded(rel_str, file):
                continue

            valid_rel_files.add(rel_str)
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
            if is_dir_excluded(d):
                shutil.rmtree(dir_path, ignore_errors=True)
                pruned_count += 1
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
    print(f"✅ 已同步至可调用副本: {DEST_AGENT} (同步 {n1} 个文件，清理 {p1} 个陈旧文件)")

    if DEST_AUTHORITATIVE is None:
        print("⏭️  跳过外部权威安装包：未设置 MTL_DEST_AUTHORITATIVE")
    else:
        n2, p2 = sync_to(DEST_AUTHORITATIVE)
        print(f"✅ 已同步至权威安装包: {DEST_AUTHORITATIVE} (同步 {n2} 个文件，清理 {p2} 个陈旧文件)")

    print("🎉 Skill 副本同步完成！")


if __name__ == "__main__":
    main()
