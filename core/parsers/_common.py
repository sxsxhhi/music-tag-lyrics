# -*- coding: utf-8 -*-
"""解析器共享：KRC/LRC 的 [offset] 标签。"""

from typing import Any, Mapping

__all__ = ["parse_offset_tag"]


def parse_offset_tag(tags: Mapping[str, Any]) -> int:
    """
    读取并解析 ``[offset:xxx]`` 标签值，单位毫秒。

    - 标签缺失 → 0
    - 值为空/非数字 → 0（旧实现只捕获 ValueError，None 会漏成 TypeError）
    """
    raw = tags.get("offset", None) if tags else None
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0
