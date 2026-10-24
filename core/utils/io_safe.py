# -*- coding: utf-8 -*-
"""原子写文本与「副本改签再替换」的音频标签落盘。"""

import logging
import os
import shutil
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)

TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030")


def read_text_guess_encoding(path: str) -> Optional[str]:
    """按 utf-8 / utf-8-sig / gbk / gb18030 依次读文本，返回首个非空结果。"""
    for enc in TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                txt = f.read().strip()
            if txt:
                return txt
        except Exception:
            continue
    return None


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
    原子地写入单个文本文件（R4：等价于 ``atomic_write_many`` 的单文件特例）。

    :raises OSError: 写入或替换失败（此时原文件保持原样）
    """
    atomic_write_many({filepath: content}, encoding=encoding, newline=newline)


def atomic_write_many(files: dict, encoding: str = "utf-8", newline: str = "\n") -> None:
    """
    一次性原子写入多个文本文件（P1-9）。

    先把所有内容都写成临时文件并 fsync，全部成功后才逐个 ``os.replace``。
    这样"第一个写成功、第二个失败"不会留下半对产物。

    注意：``os.replace`` 阶段不是跨文件事务 —— 若替换到一半失败（例如目标被占用），
    已替换的文件已经是新内容，只有尚未替换的保持原样。这是本函数能提供的最强保证。

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


def atomic_write_bytes(filepath: str, data: bytes) -> bool:
    """原子写入字节内容（temp + fsync + os.replace）。失败返回 False，原文件保持原样。

    B3 去重：itunes-cache / amll-index 的缓存落盘原先各自手写 temp+replace。
    """
    tmp = _tmp_path_for(filepath, prefix=".tmp_bin", keep_ext=False)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
        return True
    except Exception:
        return False
    finally:
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
