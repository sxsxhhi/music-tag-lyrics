# -*- coding: utf-8 -*-
"""缓存路径解析与五级回退保障。"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

_CACHE_DIR_OVERRIDE: Optional[Path] = None
_WARNED_SYSTEM_CACHE: bool = False


def _warn_system_cache_once(target_path: Path) -> None:
    """首次回退至系统盘时向 stderr 打印一次性醒目告警。"""
    global _WARNED_SYSTEM_CACHE
    if not _WARNED_SYSTEM_CACHE:
        _WARNED_SYSTEM_CACHE = True
        try:
            sys.stderr.write(
                f"⚠️ 缓存回退至系统盘目录（{target_path}）；如需改道请设置 MTL_CACHE_DIR\n"
            )
            sys.stderr.flush()
        except Exception:
            pass


def _is_writable_dir(path: Path) -> bool:
    """检查目录是否可创建并写入。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        test_file = path / ".write_test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def get_cache_dir() -> Path:
    """
    返回可写的缓存目录。
    五级优先级：
      1. 代码内/测试 override (_CACHE_DIR_OVERRIDE)
      2. 环境变量 MTL_CACHE_DIR
      3. Skill 根目录下的 .cache/（跟随 skill 所在卷，零侵入系统盘）
      4. 系统标准用户缓存目录（Win %LOCALAPPDATA%，Mac ~/Library/Caches，Linux ~/.cache；首次触发打印一次性告警）
      5. 系统临时目录 tempfile.gettempdir()
    """
    global _CACHE_DIR_OVERRIDE
    if _CACHE_DIR_OVERRIDE is not None:
        _CACHE_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _CACHE_DIR_OVERRIDE

    env_dir = os.environ.get("MTL_CACHE_DIR")
    if env_dir and env_dir.strip():
        p = Path(env_dir.strip())
        if _is_writable_dir(p):
            return p

    # 3. Skill 根目录下的 .cache/
    repo_cache = Path(__file__).resolve().parents[2] / ".cache"
    if _is_writable_dir(repo_cache):
        return repo_cache

    # 4. 平台缺省标准缓存
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

    for c in candidates:
        if _is_writable_dir(c):
            _warn_system_cache_once(c)
            return c

    # 5. 系统临时目录兜底
    temp_dir = Path(tempfile.gettempdir()) / "music-tag-lyrics"
    if _is_writable_dir(temp_dir):
        return temp_dir

    return Path(tempfile.gettempdir())


def get_cache_path(filename: str) -> Path:
    """获取缓存文件的绝对路径。"""
    return get_cache_dir() / filename


def set_cache_dir_override(path: Optional[Path]) -> None:
    """仅供单元测试重定向缓存目录。"""
    global _CACHE_DIR_OVERRIDE
    _CACHE_DIR_OVERRIDE = path

