# -*- coding: utf-8 -*-
"""
Multi-Engine Lyric Aggregator & Dispatcher
Orchestrates concurrent/cascading retrieval across:
1. AMLL (Apple Music Lossless Lyrics TTML)
2. Kugou (KRC word-level verbatim)
3. LRCLIB (Global open synchronized database)
4. NetEase (Cloud music synced & translation)
5. Acoustic Aligner (AI syllable energy synthesizer fallback)
Zero external dependencies.
"""

import re
from typing import Dict, Any, Optional

from .fetchers.amll import AMLLFetcher
from .fetchers.kugou import KugouFetcher
from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
from .acoustic_aligner import enrich_track_with_acoustic_words

CJK_PATTERN = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

def is_cjk(text: str) -> bool:
    """Checks if text contains Chinese, Japanese, or Korean characters."""
    return bool(CJK_PATTERN.search(text))

class LyricAggregator:
    """Aggregates multiple engines with language-aware dispatching."""

    @classmethod
    def fetch_best_lyrics(
        cls,
        title: str,
        artist: str = "",
        album: str = "",
        duration_ms: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves the best available lyrics using language-aware hierarchy:
        - Chinese/CJK: Kugou (KRC word) -> AMLL (TTML word) -> NetEase (LRC) -> LRCLIB
        - Non-CJK/Global: AMLL (TTML word) -> LRCLIB (LRC) -> NetEase (LRC) -> Kugou
        If only line-level lyrics are found, automatically applies acoustic word synthesizer.
        """
        cjk = is_cjk(f"{title} {artist}")
        candidate = None
        
        if cjk:
            # 1. Kugou KRC (Highest coverage for Chinese verbatim)
            try:
                res = KugouFetcher.fetch(title, artist, duration_ms)
                if res and res.get("lines"):
                    candidate = res
            except Exception:
                pass
                
            # 2. AMLL TTML (Apple Music word-level)
            if not candidate:
                try:
                    res = AMLLFetcher.fetch(title, artist, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass

            # 3. NetEase (Line-level)
            if not candidate:
                try:
                    res = NetEaseFetcher.fetch(title, artist, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass

            # 4. LRCLIB (Global open sync)
            if not candidate:
                try:
                    res = LRCLIBFetcher.fetch(title, artist, album, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass
        else:
            # Non-CJK: Global priority
            # 1. AMLL TTML
            try:
                res = AMLLFetcher.fetch(title, artist, duration_ms)
                if res and res.get("lines"):
                    candidate = res
            except Exception:
                pass

            # 2. LRCLIB (Global database)
            if not candidate:
                try:
                    res = LRCLIBFetcher.fetch(title, artist, album, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass

            # 3. NetEase
            if not candidate:
                try:
                    res = NetEaseFetcher.fetch(title, artist, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass

            # 4. Kugou
            if not candidate:
                try:
                    res = KugouFetcher.fetch(title, artist, duration_ms)
                    if res and res.get("lines"):
                        candidate = res
                except Exception:
                    pass

        if not candidate:
            return None

        # Check if candidate has word-level timestamps; if not, synthesize via acoustic aligner
        has_words = any(line.get("words") for line in candidate.get("lines", []))
        if not has_words:
            candidate = enrich_track_with_acoustic_words(candidate)
            candidate["synthesized_words"] = True

        return candidate
