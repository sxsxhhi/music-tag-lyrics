# -*- coding: utf-8 -*-
"""时间戳解析与格式化：LRC/KRC/TTML、offset/adjust 叠加。"""

import re
from typing import Match

TIME_COLON_RE = re.compile(r"^(?:(\d{1,3}):)?(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?$")
TIME_SECONDS_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*s?$", re.IGNORECASE)

def _frac_to_ms(frac: str) -> int:
    """把小数部分归一成毫秒：1 位=百毫秒，2 位=十毫秒，3 位=毫秒（G2 去重）。"""
    if len(frac) == 2:
        return int(frac) * 10
    if len(frac) == 1:
        return int(frac) * 100
    return int(frac.ljust(3, "0")[:3])

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
        ms = _frac_to_ms(m.group(4) or "0")
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
        
    return (mins * 60 + secs) * 1000 + _frac_to_ms(millis_str)

_TIMING_TAG_RE = re.compile(r"<\d{1,3}:\d{2}(?:\.\d{1,3})?>|\[\d{1,3}:\d{2}(?:\.\d{1,3})?\]")


def strip_timing_tags(text: str) -> str:
    """去掉 [mm:ss.xx] / <mm:ss.xx> 时间标签，只留正文（G4 统一实现）。"""
    if not text:
        return ""
    return _TIMING_TAG_RE.sub("", text).strip()

def format_time_tag(ms: int, bracket: str = "[]", precision: int = 2) -> str:
    """
    格式化为 [mm:ss.xx]（precision=2，默认）或 [mm:ss.xxx]（precision=3）。

    P1-6: 用整数厘秒做舍入（Python round，银行家舍入，见下），避免 59996ms 被格式化成 [00:60.00]（非法时间戳）；
    分钟位不再做两位截断，超过 99 分钟输出 [100:00.00]，与解析侧的三位分钟正则配套。

    精度策略（v1.2.8）：源是 3 位毫秒就出 3 位，源只有 1-2 位才退回 2 位。
    precision=3 时不量化，直接保留毫秒；precision=2 时走整数厘秒舍入。
    注意 2 位分支用的是 Python 的 round（银行家舍入），±5ms 有界、不累积。
    """
    if ms < 0:
        ms = 0
    l_b, r_b = bracket[0], bracket[1]
    if precision >= 3:
        minutes, rem_ms = divmod(int(ms), 60000)
        seconds, millis = divmod(rem_ms, 1000)
        return f"{l_b}{minutes:02d}:{seconds:02d}.{millis:03d}{r_b}"
    total_cs = int(round(ms / 10.0))
    minutes, rem_cs = divmod(total_cs, 6000)
    seconds = rem_cs / 100.0
    return f"{l_b}{minutes:02d}:{seconds:05.2f}{r_b}"

def apply_time_offset(timestamp_ms: int, offset_ms: int = 0) -> int:
    """Applies an offset adjustment in milliseconds, strictly clamped to >= 0."""
    return max(0, timestamp_ms + offset_ms)


def compute_duration_axis(lines: list, body_dur_ms: int = 0) -> int:
    """计算用于时长门禁判定的基准轴（毫秒）。
    取非零的 body_dur_ms 与末行 end 的较小者；若均无则返回 0。
    """
    last_end = lines[-1].get("end", 0) if lines else 0
    body = body_dur_ms or 0
    if body and last_end:
        return min(body, last_end)
    return body or last_end or 0

