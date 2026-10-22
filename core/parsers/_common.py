# -*- coding: utf-8 -*-
"""
Parser 共享工具 (v1.2.4)

R3：KRC 与 LRC 两个解析器原先各写了一遍 ``[offset:xxx]`` 标签解析（逐字节相同）。
统一到这里，避免两处漂移（例如只有一处改了异常类型，另一处没改）。
"""

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
