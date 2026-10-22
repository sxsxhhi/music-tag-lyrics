# -*- coding: utf-8 -*-
"""Live/Studio 互斥、标题括号清洗、时长容差、标题模糊匹配。"""

import re

LIVE_KEYWORDS = ("live", "现场", "巡回", "巡演", "演唱会", "不插电", "acoustic")
DEMO_KEYWORDS = ("demo", "小样")
REMIX_KEYWORDS = ("remix", "混音")

def is_live_version(text: str) -> bool:
    """Checks whether the text explicitly designates a live performance edition."""
    if not text:
        return False
    t_lower = text.lower()
    return any(k in t_lower for k in LIVE_KEYWORDS)

def is_demo_version(text: str) -> bool:
    """Checks whether the text explicitly designates a demo edition."""
    if not text:
        return False
    t_lower = text.lower()
    return any(k in t_lower for k in DEMO_KEYWORDS)

def is_remix_version(text: str) -> bool:
    """Checks whether the text explicitly designates a remix edition."""
    if not text:
        return False
    t_lower = text.lower()
    return any(k in t_lower for k in REMIX_KEYWORDS)

def is_title_fuzzy_match(cand_title: str, target_title: str) -> bool:
    """与酷狗/网易原判断逐字节相同：全等、去括号全等、或互为子串。"""
    c_t = (cand_title or "").strip().lower()
    t_c = (target_title or "").strip().lower()
    return (
        c_t == t_c or
        strip_subtitle_brackets(c_t) == strip_subtitle_brackets(t_c) or
        t_c in c_t or
        c_t in t_c
    )


def strip_subtitle_brackets(title: str) -> str:
    """
    Cleans non-version subtitle/foreign brackets while strictly preserving version tags.
    e.g. '不擅生长的树 (Isness)' -> '不擅生长的树'
         '一腔诗意喂了狗 (Live)' -> '一腔诗意喂了狗 (Live)' (Protected!)
         '起风了 (Live现场)' -> '起风了 (Live现场)' (Protected!)
    """
    if not title:
        return ""
    if is_live_version(title) or is_demo_version(title) or is_remix_version(title):
        return title.strip()
    # Strip trailing bracketed subtitles
    cleaned = re.sub(r"[\(（][^)）]+[\)）]$", "", title).strip()
    return cleaned if cleaned else title

def matches_version_policy(
    target_is_live: bool,
    cand_title: str,
    cand_album: str = ""
) -> bool:
    """
    Enforces bidirectional strict version exclusion:
    - If target is Live: candidate MUST be live (either in title or album).
    - If target is Studio: candidate MUST NOT be live.
    """
    cand_is_live = is_live_version(cand_title) or is_live_version(cand_album)
    if target_is_live:
        return cand_is_live
    else:
        return not cand_is_live

def is_duration_acceptable(
    cand_dur_ms: int,
    target_dur_ms: int,
    tolerance_sec: int = 35
) -> bool:
    """
    Checks if candidate duration is within acceptable range.
    Rejects massive mismatches (e.g. 131s studio vs 230s live).
    """
    if target_dur_ms <= 0 or cand_dur_ms <= 0:
        return True
    # Convert to seconds if ms
    c_sec = cand_dur_ms // 1000 if cand_dur_ms > 1000 else cand_dur_ms
    t_sec = target_dur_ms // 1000 if target_dur_ms > 1000 else target_dur_ms
    return abs(c_sec - t_sec) <= tolerance_sec
