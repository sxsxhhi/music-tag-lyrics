# -*- coding: utf-8 -*-
"""缓存路径解析与五级回退保障。"""

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

_CACHE_DIR_OVERRIDE: Optional[Path] = None
_WARNED_SYSTEM_CACHE: bool = False

STRICT_STORAGE_ENV = "MTL_STRICT_STORAGE"
STRICT_CACHE_ENV = "MTL_CACHE_DIR"


class StrictStorageError(RuntimeError):
    """``MTL_STRICT_STORAGE=1`` 下缓存路径不满足确定契约。

    注意：本异常只覆盖「应用缓存目录」这一件事，不构成「全进程无系统盘写入」的承诺；
    是否禁止系统盘由维护者的本地策略另行检查。
    """


def _strict_storage_enabled() -> bool:
    return os.environ.get(STRICT_STORAGE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _strict_cache_dir() -> Path:
    """严格模式：只接受已存在、绝对、可写的 MTL_CACHE_DIR，绝不回退。"""
    raw = os.environ.get(STRICT_CACHE_ENV, "").strip()
    if not raw:
        raise StrictStorageError(
            f"{STRICT_STORAGE_ENV}=1 时必须显式设置 {STRICT_CACHE_ENV}（绝对路径）"
        )
    path = Path(raw)
    if not path.is_absolute():
        raise StrictStorageError(f"{STRICT_CACHE_ENV} 必须是绝对路径：{raw}")
    if not path.exists():
        raise StrictStorageError(f"{STRICT_CACHE_ENV} 指向的目录不存在：{path}")
    if not path.is_dir():
        raise StrictStorageError(f"{STRICT_CACHE_ENV} 不是目录：{path}")
    if not os.access(str(path), os.W_OK):
        raise StrictStorageError(f"{STRICT_CACHE_ENV} 目录不可写：{path}")
    return path


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
    """返回可写的缓存目录。

    默认（``MTL_STRICT_STORAGE`` 未设置或为 0）保持五级回退：
      1. 代码内/测试 override (_CACHE_DIR_OVERRIDE)
      2. 环境变量 MTL_CACHE_DIR
      3. Skill 根目录下的 .cache/（跟随 skill 所在卷；创建失败才会回退）
      4. 系统标准用户缓存目录（Win %LOCALAPPDATA%，Mac ~/Library/Caches，
         Linux ~/.cache；首次触发打印一次性告警）
      5. 系统临时目录 tempfile.gettempdir()

    ``MTL_STRICT_STORAGE=1`` 时改为确定契约：只返回已存在、绝对、可写的
    ``MTL_CACHE_DIR``；未设置/空/相对/不存在/不可写一律抛
    :class:`StrictStorageError`，**绝不**回退到 Skill ``.cache``、系统缓存或 TEMP。

    该开关只约束应用缓存目录，不等价于「整个进程不写系统盘」。
    """
    global _CACHE_DIR_OVERRIDE
    if _CACHE_DIR_OVERRIDE is not None:
        _CACHE_DIR_OVERRIDE.mkdir(parents=True, exist_ok=True)
        return _CACHE_DIR_OVERRIDE

    if _strict_storage_enabled():
        return _strict_cache_dir()

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

