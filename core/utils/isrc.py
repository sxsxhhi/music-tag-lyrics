# -*- coding: utf-8 -*-
"""ISRC (International Standard Recording Code) 校验与标准化。

规格参考：ISO 3901
格式：2位国家码 + 3位登记码 + 2位年份 + 5位编号，共 12 位 ASCII 大写字母或数字。
标准正则：^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$
"""

from __future__ import annotations

import re
from typing import Optional

_ISRC_CLEAN_RE = re.compile(r"[\s\-_.]")
_ISRC_STRICT_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def normalize_isrc(raw: Optional[str]) -> Optional[str]:
    """清洗并标准化 ISRC 编码。
    
    返回大写的无分隔符标准 12 位 ISRC，若非法则返回 None。
    """
    if not raw or not isinstance(raw, str):
        return None
    cleaned = _ISRC_CLEAN_RE.sub("", raw.strip()).upper()
    if _ISRC_STRICT_RE.match(cleaned):
        return cleaned
    return None


def is_valid_isrc(raw: Optional[str]) -> bool:
    """快速判定字符串是否为格式合法的 ISRC。"""
    return normalize_isrc(raw) is not None
