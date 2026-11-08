# -*- coding: utf-8 -*-
"""分发视图过滤规范（P2-02 / P2-04 / P2-07）。

统一收拢 build_release_manifest.py 与 sync_skill_copies.py 的排除清单，
确保发布压缩包、Antigravity 副本与纯净权威安装包三者视图严格一致。
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# 排除目录
EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".test-work",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "_baseline",
    "_baseline_dump",
    ".venv",
    "venv",
    "env",
    # 仓库开发用 git 钩子，不进分发包
    "git-hooks",
}

# 排除文件后缀
EXCLUDE_EXTS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".DS_Store",
}

# 排除文件名（精确匹配）
EXCLUDE_FILES = {
    "Thumbs.db",
    "desktop.ini",
    # 本机私有维护与打包脚本（P2-02）
    "sync_skill_copies.py",
    "build_release_manifest.py",
    "_dist_view.py",
    # Manifest 元数据文件不打入分发包本体以消除自指死锁（P2-04-A）
    "release-manifest.json",
    # 本机运行生成的基线快照（含私人曲库文件名与音频哈希，禁止分发）
    "baseline.json",
}

# 通配文件名排除模式（P2-02：私有基准测试脚本等）
EXCLUDE_FILE_PATTERNS = [
    "run_*_ab_benchmark.py",
    "run_*benchmark*.py",
    "*.zip",
]


def is_dir_excluded(dir_name: str) -> bool:
    """判定子目录是否应在分发中排除。"""
    if dir_name in EXCLUDE_DIRS:
        return True
    if dir_name.startswith(".test-work") or dir_name.startswith("_baseline"):
        return True
    return False


def is_file_excluded(rel_path: Path | str, filename: str = "") -> bool:
    """判定文件是否应在分发中排除。

    rel_path: 相对于仓库根目录的路径（Path 或使用正斜杠的字符串）
    filename: 文件名（可选，未提供时从 rel_path 提取）
    """
    rel_str = str(rel_path).replace("\\", "/")
    fname = filename or Path(rel_str).name
    ext = Path(fname).suffix.lower()

    if ext in EXCLUDE_EXTS:
        return True

    if fname in EXCLUDE_FILES:
        return True

    for pattern in EXCLUDE_FILE_PATTERNS:
        if fnmatch.fnmatch(fname, pattern):
            return True

    # 排除 dist 目录下的任何产物
    if rel_str.startswith("dist/"):
        return True

    # 排除 docs/ 下除 images/ 之外的全部过程审计/复审报告/内部清单（P2-02）
    if rel_str.startswith("docs/"):
        if not rel_str.startswith("docs/images/"):
            return True

    return False
