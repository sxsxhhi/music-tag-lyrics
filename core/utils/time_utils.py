# -*- coding: utf-8 -*-
"""
Universal Timestamp Math and Formatting Utility (v1.1.3)
Centralizes time string parsing (LRC, KRC, TTML, SMPTE), millisecond arithmetic,
bracket formatting, and offset/adjust application with boundary clamping.
Zero external dependencies.
"""

import re
from typing import Union, Match

TIME_COLON_RE = re.compile(r"^(?:(\d{1,3}):)?(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?$")
TIME_SECONDS_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*s?$", re.IGNORECASE)

def parse_time_string_to_ms(time_str: str) -> int:
    """
    Parses various timestamp notations into integer milliseconds:
    - '01:23.45' -> 83450
    - '01:23.456' -> 83456
    - '00:01:23.456' -> 83456
    - '83.456s' or '83.456' -> 83456
    """
    if not time_str:
        return 0
    s = time_str.strip()
    
    # 1. Colon notation (hh:mm:ss.xxx or mm:ss.xxx)
    m = TIME_COLON_RE.match(s)
    if m:
        hrs = int(m.group(1)) if m.group(1) else 0
        mins = int(m.group(2))
        secs = int(m.group(3))
        ms_str = m.group(4) or "0"
        if len(ms_str) == 2:
            ms = int(ms_str) * 10
        elif len(ms_str) == 1:
            ms = int(ms_str) * 100
        else:
            ms = int(ms_str.ljust(3, "0")[:3])
        return (hrs * 3600 + mins * 60 + secs) * 1000 + ms
        
    # 2. Pure seconds notation ('83.45s' or '83.45')
    sm = TIME_SECONDS_RE.match(s)
    if sm:
        try:
            return int(float(sm.group(1)) * 1000)
        except ValueError:
            return 0
            
    return 0

def parse_match_to_ms(m: Match) -> int:
    """
    Extracts milliseconds from a regex match of <mm:ss.xx> or [mm:ss.xx].
    Handles group(1-3) or group(4-6).
    """
    if m.group(1) is not None:
        mins, secs, millis_str = int(m.group(1)), int(m.group(2)), m.group(3) or "0"
    else:
        mins, secs, millis_str = int(m.group(4)), int(m.group(5)), m.group(6) or "0"
        
    if len(millis_str) == 2:
        ms = int(millis_str) * 10
    elif len(millis_str) == 1:
        ms = int(millis_str) * 100
    else:
        ms = int(millis_str.ljust(3, "0")[:3])
        
    return (mins * 60 + secs) * 1000 + ms

def format_time_tag(ms: int, bracket: str = "[]") -> str:
    """
    格式化为 [mm:ss.xx] / <mm:ss.xx>。

    P1-6: 用整数厘秒做四舍五入，避免 59996ms 被格式化成 [00:60.00]（非法时间戳）；
    分钟位不再做两位截断，超过 99 分钟输出 [100:00.00]，与解析侧的三位分钟正则配套。
    """
    if ms < 0:
        ms = 0
    total_cs = int(round(ms / 10.0))
    minutes, rem_cs = divmod(total_cs, 6000)
    seconds = rem_cs / 100.0
    l_b, r_b = bracket[0], bracket[1]
    return f"{l_b}{minutes:02d}:{seconds:05.2f}{r_b}"

def apply_time_offset(timestamp_ms: int, offset_ms: int = 0) -> int:
    """Applies an offset adjustment in milliseconds, strictly clamped to >= 0."""
    return max(0, timestamp_ms + offset_ms)
