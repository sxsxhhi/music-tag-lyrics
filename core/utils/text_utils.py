# -*- coding: utf-8 -*-
"""
Linguistic Character & Text Analysis Utility (v1.1.3)
Centralizes CJK detection, Japanese Kana, Chinese Hanzi identification,
and non-Chinese translation line differentiation.
Zero external dependencies.
"""

import re

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
HANZI_RE = re.compile(r"[\u4e00-\u9fa5]")
KANA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")

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

def is_translation_line(text: str, has_words: bool = False) -> bool:
    """
    Checks if a lyric line in a non-Chinese song is an interleaved Chinese translation.
    Criteria: Contains Hanzi, lacks Kana, and has no word-level timestamps.
    """
    if has_words or not text:
        return False
    return has_hanzi(text) and not has_kana(text)
