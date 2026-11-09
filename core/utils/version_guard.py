# -*- coding: utf-8 -*-
"""Live/Studio 互斥、标题括号清洗、时长容差、标题模糊匹配。

双轨分工（勿合并）：本模块是**模糊派**——子串/去括号/互串的宽松匹配，服务
酷狗/网易/LRCSHARE/LRCLIB/全球搜词/封面酷狗档等噪声大的商业接口；
严格派见 core/utils/text_match.py（NFKC 归一化键全等，服务 Apple 三件套防串歌）。
"""

import re

LIVE_KEYWORDS = ("live", "现场", "巡回", "巡演", "演唱会", "不插电", "acoustic")
DEMO_KEYWORDS = ("demo", "小样")
INSTRUMENTAL_KEYWORDS = ("instrumental", "伴奏", "纯音乐", "karaoke", "off vocal")
COVER_KEYWORDS = ("cover", "翻唱")
from typing import Optional

REMIX_PATTERN = re.compile(
    r"(?:(?<![A-Za-z])(?:remix|re-mix)(?![A-Za-z])|混音|[\(\[\{][^\)\]\}]*\b(?:mix|remix|club|extended|radio\s+edit)\b[^\)\]\}]*[\)\]\}]|\b\w+\s+mix\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# H04：英文版本关键词必须按 ASCII 字母边界匹配
#
# 原先统一用 `k in t_lower` 子串判定，导致真实歌名被误判版本：
#   Alive / Olive / Deliver / Lived In Bars  → 被判为 Live
#   Discover / Recovery                      → 被判为 Cover
#   Demons                                   → 被判为 Demo
#
# 不能用 Unicode 的 `\b`：`\b` 基于 `\w`，而 `\w` 含汉字，`r"\blive\b"` 在
# `Live现场` 里不会命中（`e` 与 `现` 之间不构成词界），恰好把必须保留的正例漏掉。
# 因此改用只针对 ASCII 字母的前后断言。
# 中文关键词（现场/翻唱/伴奏…）保持原生子串语义，不受此规则影响。
# ---------------------------------------------------------------------------
_ASCII_KEYWORD_RE_CACHE: dict = {}


def _normalize_spaces(text: str) -> str:
    """把连续空白（含全角空格）压成单个半角空格，供多词关键词匹配。"""
    return re.sub(r"[\s\u3000]+", " ", text or "").strip()


def _ascii_keyword_re(keyword: str) -> "re.Pattern[str]":
    """构造 ASCII 边界关键词正则（大小写不敏感）。"""
    cached = _ASCII_KEYWORD_RE_CACHE.get(keyword)
    if cached is None:
        cached = re.compile(
            r"(?<![A-Za-z])" + re.escape(keyword) + r"(?![A-Za-z])",
            re.IGNORECASE,
        )
        _ASCII_KEYWORD_RE_CACHE[keyword] = cached
    return cached


def _contains_keyword(text: str, keyword: str) -> bool:
    """中文关键词走子串；英文关键词走 ASCII 边界正则。"""
    if not keyword:
        return False
    if any(ord(ch) > 127 for ch in keyword):
        return keyword in text.lower()
    haystack = _normalize_spaces(text) if " " in keyword else text
    return _ascii_keyword_re(keyword).search(haystack) is not None


def is_live_version(text: str) -> bool:
    """Checks whether the text explicitly designates a live performance edition."""
    if not text:
        return False
    return any(_contains_keyword(text, k) for k in LIVE_KEYWORDS)


def is_demo_version(text: str) -> bool:
    """Checks whether the text explicitly designates a demo edition."""
    if not text:
        return False
    return any(_contains_keyword(text, k) for k in DEMO_KEYWORDS)


def is_remix_version(text: str) -> bool:
    """Checks whether the text explicitly designates a remix edition."""
    if not text:
        return False
    return bool(REMIX_PATTERN.search(text))


def is_instrumental_version(text: str) -> bool:
    """Checks whether the text explicitly designates an instrumental / accompaniment edition."""
    if not text:
        return False
    return any(_contains_keyword(text, k) for k in INSTRUMENTAL_KEYWORDS)


def is_cover_version(text: str) -> bool:
    """Checks whether the text explicitly designates a cover / tribute edition."""
    if not text:
        return False
    return any(_contains_keyword(text, k) for k in COVER_KEYWORDS)


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
    if (
        is_live_version(title)
        or is_demo_version(title)
        or is_remix_version(title)
        or is_instrumental_version(title)
        or is_cover_version(title)
    ):
        return title.strip()
    # Strip trailing bracketed subtitles
    cleaned = re.sub(r"[\(（][^)）]+[\)）]$", "", title).strip()
    return cleaned if cleaned else title

def matches_version_policy(
    target_is_live: bool,
    cand_title: str,
    cand_album: str = "",
    target_is_remix: Optional[bool] = None,
    target_is_instrumental: Optional[bool] = False,
    target_is_cover: Optional[bool] = False,
    target_is_demo: Optional[bool] = None,
) -> bool:
    """
    Enforces bidirectional strict version exclusion:
    - If target is Live: candidate MUST be live (either in title or album).
    - If target is Studio: candidate MUST NOT be live.
    - If target_is_remix is provided:
      - If target is Remix: candidate MUST be remix (either in title or album).
      - If target is Original (non-remix): candidate MUST NOT be remix.
    - If target_is_instrumental is provided (default False):
      - If target is Instrumental: candidate MUST be instrumental.
      - If target is Vocal: candidate MUST NOT be instrumental.
    - If target_is_cover is provided (default False):
      - If target is Cover: candidate MUST be cover.
      - If target is Original: candidate MUST NOT be cover.
    - If target_is_demo is provided:
      - If target is Demo: candidate MUST be demo.
      - If target is Non-demo: candidate MUST NOT be demo.
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

    if target_is_instrumental is not None:
        cand_is_inst = is_instrumental_version(cand_title) or is_instrumental_version(cand_album)
        if target_is_instrumental:
            if not cand_is_inst:
                return False
        else:
            if cand_is_inst:
                return False

    if target_is_cover is not None:
        cand_is_cover = is_cover_version(cand_title) or is_cover_version(cand_album)
        if target_is_cover:
            if not cand_is_cover:
                return False
        else:
            if cand_is_cover:
                return False

    if target_is_demo is not None:
        cand_is_demo = is_demo_version(cand_title) or is_demo_version(cand_album)
        if target_is_demo:
            if not cand_is_demo:
                return False
        else:
            if cand_is_demo:
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
