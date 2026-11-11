# -*- coding: utf-8 -*-
"""原子写文本与「副本改签再替换」的音频标签落盘。"""

import hashlib
import logging
import os
import shutil
import uuid
from typing import Callable, Optional

logger = logging.getLogger(__name__)

TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030")


def sha256_file(filepath: str | os.PathLike, chunk_size: int = 1024 * 1024) -> str:
    """流式分块计算文件的 SHA-256 哈希，杜绝大文件一次性加载至内存。"""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


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
    若替换过程中发生异常，尝试按逆序恢复已替换文件。若 OS 再次拒绝
    回滚，保留 .tmp_bak 恢复材料并抛出 SIDECAR_ROLLBACK_INCOMPLETE。
    注意：多个 os.replace 不能保证断电/进程崩溃时跨文件原子性。

    :param files: {目标路径: 文本内容}
    """
    tmps = {}
    backups = {}
    replaced = []
    failed_restore = set()
    try:
        for path, content in files.items():
            tmp = _tmp_path_for(path, prefix=".tmp_txt", keep_ext=False)
            with open(tmp, "w", encoding=encoding, newline=newline) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            tmps[path] = tmp

        # 替换前为已存在的文件保留临时副本以备异常回滚
        for path in tmps.keys():
            if os.path.exists(path):
                bak = _tmp_path_for(path, prefix=".tmp_bak", keep_ext=True)
                shutil.copy2(path, bak)
                backups[path] = bak

        try:
            for path, tmp in tmps.items():
                os.replace(tmp, path)
                replaced.append(path)
        except Exception:
            for r_path in reversed(replaced):
                try:
                    if r_path in backups:
                        if not os.path.isfile(backups[r_path]):
                            raise OSError("ROLLBACK_BACKUP_MISSING: " + backups[r_path])
                        os.replace(backups[r_path], r_path)
                    elif os.path.lexists(r_path):
                        # For a newly created target, deletion is the rollback.
                        # Never ignore a failed unlink in this critical path.
                        os.unlink(r_path)
                except Exception as rb_exc:
                    failed_restore.add(r_path)
                    logger.error("atomic_write_many: rollback failed for %s; original backup retained at %s: %s",
                                 r_path, backups.get(r_path), rb_exc)
            if failed_restore:
                # Preserve recoverable originals even when the operating system
                # refuses both commit and compensation. Never hide this state.
                raise OSError("SIDECAR_ROLLBACK_INCOMPLETE: recovery paths "
                              + ", ".join(str(backups.get(p, p))
                                          for p in sorted(failed_restore)))
            raise
    finally:
        for tmp in tmps.values():
            _silent_remove(tmp)
        for path, bak in backups.items():
            if path not in failed_restore:
                _silent_remove(bak)


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
    在副本上执行标签写入，成功且音频流校验一致后才替换原文件。

    :param filepath: 原音频绝对/相对路径
    :param save_callback: 接收临时文件路径、返回 bool 的写入函数
    :return: True 表示已成功替换；False 表示失败且**原文件未被改动**
    """
    if not os.path.exists(filepath):
        return False

    orig_stream_st, orig_stream_hash = "AUDIO_STREAM_NOT_VERIFIED", ""
    try:
        from core.adapters.container_tag_adapter import compute_audio_stream_digest
        orig_stream_st, orig_stream_hash = compute_audio_stream_digest(filepath)
    except Exception:
        pass
    if orig_stream_st != "VERIFIED":
        logger.warning("safe_save_audio_tags: 音频流无法可靠验证，拒绝改签: %s", filepath)
        return False

    # Guard against an external application changing the source while our
    # private copy is being rewritten. This cannot replace an OS file lock,
    # but prevents committing over an observed concurrent content change.
    try:
        original_file_hash = sha256_file(filepath)
    except OSError:
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

        try:
            from core.adapters.container_tag_adapter import compute_audio_stream_digest
            tmp_stream_st, tmp_stream_hash = compute_audio_stream_digest(tmp_path)
            if tmp_stream_st != "VERIFIED" or tmp_stream_hash != orig_stream_hash:
                logger.warning("safe_save_audio_tags: 音频流被篡改或校验不通过，放弃落盘: %s", filepath)
                return False
        except Exception as stream_exc:
            logger.warning("safe_save_audio_tags: 音频流校验异常: %s (%s)", filepath, stream_exc)
            return False

        if sha256_file(filepath) != original_file_hash:
            logger.warning("safe_save_audio_tags: 原音频在写回期间被外部修改，拒绝覆盖: %s", filepath)
            return False
        os.replace(tmp_path, filepath)
        return True
    except Exception as exc:  # noqa: BLE001 - 任何异常都不能让原文件承担风险
        logger.warning("safe_save_audio_tags 失败，原文件未改动: %s (%s)", filepath, exc)
        return False
    finally:
        _silent_remove(tmp_path)
