# -*- coding: utf-8 -*-
"""ID3v1 尾部 128 字节的字节级保护（v1.8.5 / H-A1）。

## 为什么需要这一层

mutagen 的 ``ID3.save()`` 只有三个 ``v1`` 取值，且**没有「保持不变」**：

* ``v1=0`` → 删除 ID3v1
* ``v1=1`` → 更新已有 ID3v1（默认值）
* ``v1=2`` → 创建并更新 ID3v1

于是「只改歌词」这类操作会顺带把既有 ID3v1 的 title/artist/album/year 从
ID3v2 重写一遍，并把 comment 字段**无条件清空**；非 Latin-1 文本（中文等）
还会被降级成 ``???``。更糟的是音频流摘要刻意排除了 ID3v1，改动完全不可见。

## 做法

在 mutagen 完全看不到 ID3v1 的前提下完成写入：

1. 保存前把文件末尾的 128 字节 ``TAG`` 块**切下来**（截断文件）；
2. 此时 ``v1=1`` 语义变成「没有就不新增」，ID3v1 不会被创建或修改；
3. 保存成功后把原始 128 字节**原封附加**回去。

这是字节级恒等，不依赖 mutagen 内部的字段映射表，也不用关心它把
Comment 写成了什么。
"""

import contextlib
import hashlib
import os
from typing import Optional

ID3V1_SIZE = 128
ID3V1_MAGIC = b"TAG"


class Id3v1IntegrityError(RuntimeError):
    """ID3v1 保护失败：调用方必须放弃落盘。"""


def read_id3v1_tail(path: str | os.PathLike) -> Optional[bytes]:
    """返回末尾 128 字节的 ID3v1 块；不存在则返回 None。

    只做字节判定，不解析字段——解析会让「保留原样」失去意义。
    """
    try:
        size = os.path.getsize(path)
        if size < ID3V1_SIZE:
            return None
        with open(path, "rb") as handle:
            handle.seek(-ID3V1_SIZE, os.SEEK_END)
            tail = handle.read(ID3V1_SIZE)
    except OSError:
        return None
    if len(tail) != ID3V1_SIZE or not tail.startswith(ID3V1_MAGIC):
        return None
    return tail


def id3v1_sha256(path: str | os.PathLike) -> str:
    """ID3v1 块的摘要；不存在时返回空串（便于直接比较）。"""
    tail = read_id3v1_tail(path)
    return hashlib.sha256(tail).hexdigest() if tail is not None else ""


def _strip(path: str | os.PathLike) -> Optional[bytes]:
    tail = read_id3v1_tail(path)
    if tail is None:
        return None
    size = os.path.getsize(path)
    with open(path, "r+b") as handle:
        handle.truncate(size - ID3V1_SIZE)
    return tail


def _append(path: str | os.PathLike, tail: bytes) -> None:
    with open(path, "ab") as handle:
        handle.write(tail)


@contextlib.contextmanager
def preserve_id3v1(path: str | os.PathLike):
    """在 with 体内写入标签，退出时保证 ID3v1 与原文件逐字节相同。

    正常退出：确认写入库没有凭空造出新的 ID3v1，再把原块附回。
    异常退出：尽力把原块附回后继续抛出，让上层放弃整个临时副本。

    不满足契约时抛 :class:`Id3v1IntegrityError`，绝不静默放行。
    """
    tail = _strip(path)
    try:
        yield tail
    except BaseException:
        # 写入失败：恢复原样（若中途被改坏，上层本来就会丢弃副本）
        if tail is not None and read_id3v1_tail(path) is None:
            _append(path, tail)
        raise
    else:
        present = read_id3v1_tail(path)
        if tail is None:
            if present is not None:
                raise Id3v1IntegrityError("ID3V1_CREATED: 原文件没有 ID3v1，写入后却出现")
            return
        if present is not None:
            raise Id3v1IntegrityError("ID3V1_DUPLICATED: 写入库自行生成了 ID3v1 尾块")
        _append(path, tail)


def assert_preserved(before_sha256: str, path: str | os.PathLike, *, code: str) -> None:
    """比对写入前后的 ID3v1 摘要，不一致即抛错。"""
    after = id3v1_sha256(path)
    if after != before_sha256:
        raise Id3v1IntegrityError(f"{code}: ID3v1 尾块被改写")
