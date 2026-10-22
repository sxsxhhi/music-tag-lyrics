# -*- coding: utf-8 -*-
"""
Safe I/O Utilities (v1.3.0)
原子写与安全标签落盘：任何一步失败都不会让原文件处于半写状态。

设计要点
--------
1. ``atomic_write_text``：同目录临时文件 → 写入 → fsync → ``os.replace``。
   ``os.replace`` 在同一分区上是原子的，进程被杀也只会留下临时文件，不会留下半截歌词。
2. ``safe_save_audio_tags``：先把音频整份复制到同目录临时文件，在**副本**上执行标签写入，
   成功且副本非空才 ``os.replace`` 回原路径。mutagen 的 ``save()`` 是"截断后重写"，
   直接作用于原文件时一旦中断就会损坏音频；复制-改写-替换把这段风险关在副本里。
3. ``copy2`` 会保留原文件的 mtime，因此**替换后音频的 mtime 不变**。
   判断"标签是否真的写进去了"必须回读标签内容，不能看 mtime。

Zero external dependencies (stdlib only).
"""

import logging
import os
import shutil
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def _tmp_path_for(filepath: str, prefix: str = ".tmp", keep_ext: bool = True) -> str:
    """在同一目录下生成一个不会与现有文件冲突的临时文件名。"""
    dir_name = os.path.dirname(os.path.abspath(filepath)) or "."
    ext = os.path.splitext(filepath)[1] if keep_ext else ""
    token = uuid.uuid4().hex[:8]
    return os.path.join(dir_name, f"{prefix}_{os.getpid()}_{token}{ext}")


def _silent_remove(path: Optional[str]) -> None:
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def atomic_write_text(filepath: str, content: str, encoding: str = "utf-8", newline: str = "\n") -> None:
    """
    原子地写入文本文件。

    :raises OSError: 写入或替换失败（此时原文件保持原样）
    """
    tmp_path = _tmp_path_for(filepath, prefix=".tmp_txt", keep_ext=False)
    try:
        with open(tmp_path, "w", encoding=encoding, newline=newline) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filepath)
    finally:
        _silent_remove(tmp_path)


def atomic_write_many(files: dict, encoding: str = "utf-8", newline: str = "\n") -> None:
    """
    一次性原子写入多个文本文件（P1-9）。

    先把所有内容都写成临时文件，全部成功后才逐个 ``os.replace``。这样"第一个写成功、
    第二个失败"不会留下半对产物；任何一步失败都会清理临时文件，已存在的目标文件保持原样。

    :param files: {目标路径: 文本内容}
    """
    tmps = {}
    try:
        for path, content in files.items():
            tmp = _tmp_path_for(path, prefix=".tmp_txt", keep_ext=False)
            with open(tmp, "w", encoding=encoding, newline=newline) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            tmps[path] = tmp
        for path, tmp in tmps.items():
            os.replace(tmp, path)
    finally:
        for tmp in tmps.values():
            _silent_remove(tmp)


def safe_save_audio_tags(filepath: str, save_callback: Callable[[str], bool]) -> bool:
    """
    在副本上执行标签写入，成功后才替换原文件。

    :param filepath: 原音频绝对/相对路径
    :param save_callback: 接收临时文件路径、返回 bool 的写入函数
    :return: True 表示已成功替换；False 表示失败且**原文件未被改动**
    """
    if not os.path.exists(filepath):
        return False

    tmp_path = _tmp_path_for(filepath, prefix=".tmp_audio", keep_ext=True)
    try:
        shutil.copy2(filepath, tmp_path)
        ok = bool(save_callback(tmp_path))
        if not ok:
            logger.warning("safe_save_audio_tags: 写入回调返回失败，原文件未改动: %s", filepath)
            return False
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            logger.warning("safe_save_audio_tags: 临时文件缺失或为空，原文件未改动: %s", filepath)
            return False
        os.replace(tmp_path, filepath)
        return True
    except Exception as exc:  # noqa: BLE001 - 任何异常都不能让原文件承担风险
        logger.warning("safe_save_audio_tags 失败，原文件未改动: %s (%s)", filepath, exc)
        return False
    finally:
        _silent_remove(tmp_path)
