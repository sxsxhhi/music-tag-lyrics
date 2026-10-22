# -*- coding: utf-8 -*-
"""
Linguistic Character & Text Analysis Utility (v1.1.3)
Centralizes CJK detection, Japanese Kana, Chinese Hanzi identification,
and non-Chinese translation line differentiation.
Zero external dependencies.
"""

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
# 汉字判定统一到 CJK 统一表意区（旧代码这里用 \u9fa5，会把 \u9fa6~\u9fff 的字漏掉）
HANZI_RE = re.compile(r"[\u4e00-\u9fff]")
KANA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")
HANGUL_RE = re.compile(r"[\uac00-\ud7af]")

def is_cjk(text: str) -> bool:
    """Checks if text contains Chinese, Japanese, or Korean characters."""
    if not text:
        return False
    return bool(CJK_RE.search(text))

def has_hanzi(text: str) -> bool:
    """Checks if text contains Chinese characters."""
    if not text:
        return False
    return bool(HANZI_RE.search(text))

def has_kana(text: str) -> bool:
    """Checks if text contains Japanese Hiragana or Katakana."""
    if not text:
        return False
    return bool(KANA_RE.search(text))

def has_hangul(text: str) -> bool:
    """Checks if text contains Korean Hangul characters."""
    if not text:
        return False
    return bool(HANGUL_RE.search(text))

def is_foreign_track(title: str, artist: str = "") -> bool:
    """
    Checks if a track is foreign (requires Chinese translation).
    A track is foreign if it contains Hangul (Korean), Kana (Japanese), or lacks Hanzi (Western).
    """
    combined = f"{title} {artist}".strip()
    if has_hangul(combined) or has_kana(combined):
        return True
    return not has_hanzi(combined)

def is_translation_line(text: str, has_words: bool = False) -> bool:
    """
    Checks if a lyric line in a non-Chinese song is an interleaved Chinese translation.
    Criteria: Contains Hanzi, lacks Kana, and has no word-level timestamps.
    """
    if has_words or not text:
        return False
    return has_hanzi(text) and not has_kana(text)

# ---- G5/G7: 跨模块共用的文本判定（原先散落在 lrcshare / kugou / aggregator / acoustic 各写一份）----

SOURCE_LINE_RE = re.compile(
    r"(?i)(?:^|\b)(?:lrc|elrc)\s*[:：]"
    r"|https?://|www\."
    r"|歌词来源|来源\s*[:：]|由.{0,12}提供|powered\s+by|歌词由.{0,12}制作"
)

_HAS_ALNUM_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

INSTRUMENTAL_KEYWORDS = ("纯音乐", "请欣赏", "伴奏", "Instrumental", "No Lyrics")


def is_source_or_credit_line(text: str) -> bool:
    """判断一行是来源标注/署名而非歌词正文（X-21）。"""
    t = (text or "").strip()
    if not t:
        return True
    if SOURCE_LINE_RE.search(t):
        return True
    return not _HAS_ALNUM_RE.search(t)


def is_instrumental_placeholder(lines, target_duration_ms: int = 0, max_lines: int = 6) -> bool:
    """
    判断候选是不是"纯音乐/伴奏"占位（G7：原先 4 份实现、3 份关键词表）。

    只在时长已知且足够长时才判，避免把短视频/铃声误杀。
    """
    if not lines or not target_duration_ms or target_duration_ms <= 60000:
        return False
    if len(lines) > max_lines:
        return False
    joined = " ".join(str(l.get("text", "")) for l in lines)
    low = joined.lower()
    return any(kw.lower() in low for kw in INSTRUMENTAL_KEYWORDS)
