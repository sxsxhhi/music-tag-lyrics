# -*- coding: utf-8 -*-
"""缓存路径解析与三级回退保障。"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

_CACHE_DIR_OVERRIDE: Optional[Path] = None


def get_cache_dir() -> Path:
    """
    返回可写的缓存目录。
    优先级：
      1. 代码内/测试 override
      2. 环境变量 MTL_CACHE_DIR
      3. 系统标准用户缓存目录（Win %LOCALAPPDATA%，Mac ~/Library/Caches，Linux ~/.cache）
      4. 系统临时目录 tempfile.gettempdir()
    """
    global _CACHE_DIR_OVERRIDE
    if _CACHE_DIR_OVERRIDE is not None:
        _CACHE_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _CACHE_DIR_OVERRIDE

    env_dir = os.environ.get("MTL_CACHE_DIR")
    if env_dir:
        p = Path(env_dir)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass

    # 平台缺省缓存
    candidates = []
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "music-tag-lyrics")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Caches" / "music-tag-lyrics")
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache:
            candidates.append(Path(xdg_cache) / "music-tag-lyrics")
        else:
            candidates.append(Path.home() / ".cache" / "music-tag-lyrics")

    candidates.append(Path(tempfile.gettempdir()) / "music-tag-lyrics")

    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            # 测试可写性
            test_file = c / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return c
        except Exception:
            continue

    # 兜底临时目录根
    fallback = Path(tempfile.gettempdir())
    return fallback


def get_cache_path(filename: str) -> Path:
    """获取缓存文件的绝对路径。"""
    return get_cache_dir() / filename


def set_cache_dir_override(path: Optional[Path]) -> None:
    """仅供单元测试重定向缓存目录。"""
    global _CACHE_DIR_OVERRIDE
    _CACHE_DIR_OVERRIDE = path
