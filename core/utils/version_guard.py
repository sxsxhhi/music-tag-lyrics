# -*- coding: utf-8 -*-
"""Live/Studio 互斥、标题括号清洗、时长容差、标题模糊匹配。

双轨分工（勿合并）：本模块是**模糊派**——子串/去括号/互串的宽松匹配，服务
酷狗/网易/LRCSHARE/LRCLIB/全球搜词/封面酷狗档等噪声大的商业接口；
严格派见 core/utils/text_match.py（NFKC 归一化键全等，服务 Apple 三件套防串歌）。
"""

import re

LIVE_KEYWORDS = ("live", "现场", "巡回", "巡演", "演唱会", "不插电", "acoustic")
DEMO_KEYWORDS = ("demo", "小样")
from typing import Optional

REMIX_PATTERN = re.compile(
    r"(?:\b(?:remix|re-mix)\b|混音|[\(\[\{][^\)\]\}]*\b(?:mix|remix|club|extended|radio\s+edit)\b[^\)\]\}]*[\)\]\}]|\b\w+\s+mix\b)",
    re.IGNORECASE,
)

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
    return bool(REMIX_PATTERN.search(text))

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
    cand_album: str = "",
    target_is_remix: Optional[bool] = None,
) -> bool:
    """
    Enforces bidirectional strict version exclusion:
    - If target is Live: candidate MUST be live (either in title or album).
    - If target is Studio: candidate MUST NOT be live.
    - If target_is_remix is provided:
      - If target is Remix: candidate MUST be remix (either in title or album).
      - If target is Original (non-remix): candidate MUST NOT be remix.
    """
    cand_is_live = is_live_version(cand_title) or is_live_version(cand_album)
    if target_is_live:
        if not cand_is_live:
            return False
    else:
        if cand_is_live:
            return False

    if target_is_remix is not None:
        cand_is_remix = is_remix_version(cand_title) or is_remix_version(cand_album)
        if target_is_remix:
            if not cand_is_remix:
                return False
        else:
            if cand_is_remix:
                return False

    return True

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


def album_match_score(target_album: str, cand_album: str) -> int:
    """同名专辑加权：去括号精确相等 60 / 互为子串 30 / 其余 0。

    供酷狗 / 网易 / LRCLIB 三源共用的单一出处（原先是 kugou 内部函数）。
    O2 防穿透：极短专辑名（< 3 字符，如 "1"、"21"、"Red" 的数字/单字位）
    只允许去括号精确相等，禁止模糊子串加权——"21" 不再命中 "1989"、"100 Hits"。
    """
    alb_c = (target_album or "").strip().lower()
    c_alb = (cand_album or "").strip().lower()
    if not alb_c or not c_alb:
        return 0
    if alb_c == c_alb or strip_subtitle_brackets(alb_c) == strip_subtitle_brackets(c_alb):
        return 60
    if min(len(alb_c), len(c_alb)) >= 3 and (alb_c in c_alb or c_alb in alb_c):
        return 30
    return 0
