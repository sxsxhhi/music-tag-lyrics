# -*- coding: utf-8 -*-
"""
Multi-Engine Lyric Aggregator & Dispatcher (v1.2.2)
Orchestrates concurrent/cascading retrieval across:
1. Kugou (KRC word-level verbatim with native embedded 1:1 translation)
2. AMLL (Apple Music Lossless Lyrics TTML)
3. NetEase (Cloud music synced & official reviewed translation)
4. LRCLIB (Global open synchronized database)
5. Acoustic Aligner (AI syllable energy synthesizer fallback)
Natively enforces bilingual translation enrichment for foreign tracks.
Zero external dependencies.
"""

from typing import Dict, Any, Optional

from .fetchers.amll import AMLLFetcher
from .fetchers.kugou import KugouFetcher
from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
from .acoustic_aligner import enrich_track_with_acoustic_words
from .parsers.lrc_parser import parse_lrc
from .utils.text_utils import is_cjk, is_foreign_track

def enrich_with_netease_translation(candidate: Dict[str, Any], title: str, artist: str) -> Dict[str, Any]:
    """Helper to attach NetEase official tlyric to a candidate that lacks translations."""
    if not candidate or not candidate.get("lines"):
        return candidate
    has_trans = any(line.get("translation") for line in candidate.get("lines", []))
    if has_trans:
        return candidate
        
    try:
        ne_data = NetEaseFetcher.fetch(title, artist)
        tlyric_text = ne_data.get("tlyric", "") if ne_data else ""
        if tlyric_text:
            t_parsed = parse_lrc(tlyric_text)
            clean_t = [l for l in t_parsed.get("lines", []) if l.get("text", "").strip()]
            used = set()
            for line in candidate["lines"]:
                k_time = line.get("start", 0)
                best_diff, best_idx, best_text = 1500, -1, ""
                for idx, tl in enumerate(clean_t):
                    if idx in used:
                        continue
                    diff = abs(tl.get("start", 0) - k_time)
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = idx
                        best_text = tl.get("text", "").strip()
                if best_idx >= 0:
                    line["translation"] = best_text
                    used.add(best_idx)
    except Exception:
        pass
    return candidate

class LyricAggregator:
    """Aggregates multiple engines with language-aware dispatching."""

    @classmethod
    def is_western_track(cls, title: str, artist: str = "") -> bool:
        """Determines whether a track is predominantly non-CJK."""
        return not is_cjk(f"{title} {artist}")

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
        - Non-CJK/Global: Kugou (KRC word + native trans) -> AMLL (TTML word) -> NetEase (LRC + tlyric) -> LRCLIB
        If only line-level lyrics are found, automatically applies acoustic word synthesizer.
        """
        is_foreign = is_foreign_track(title, artist)
        candidate = None
        
        # 1. First probe Kugou (Provides both verbatim words and native 1:1 translation)
        try:
            res = KugouFetcher.fetch(title, artist, duration_ms)
            if res and res.get("lines"):
                if is_foreign and res.get("has_embedded_translation"):
                    candidate = res
                elif not is_foreign:
                    candidate = res
                else:
                    candidate = res
        except Exception:
            pass

        # 2. AMLL TTML (Apple Music word-level verbatim)
        if not candidate or (is_foreign and not candidate.get("has_embedded_translation")):
            try:
                amll_res = AMLLFetcher.fetch(title, artist, duration_ms)
                if amll_res and amll_res.get("lines"):
                    candidate = amll_res
            except Exception:
                pass

        # 3. NetEase (Cloud music synced & translation)
        if not candidate:
            try:
                ne_res = NetEaseFetcher.fetch(title, artist, duration_ms)
                if ne_res and ne_res.get("lines"):
                    candidate = ne_res
            except Exception:
                pass

        # 4. LRCLIB (Global open sync database)
        if not candidate:
            try:
                lrc_res = LRCLIBFetcher.fetch(title, artist, album, duration_ms)
                if lrc_res and lrc_res.get("lines"):
                    candidate = lrc_res
            except Exception:
                pass

        if not candidate:
            return None

        # For foreign tracks (Western, Korean, Japanese), ensure bilingual translation is enriched
        if is_foreign:
            candidate = enrich_with_netease_translation(candidate, title, artist)

        # Check if candidate has word-level timestamps; if not, synthesize via acoustic aligner
        has_words = any(line.get("words") for line in candidate.get("lines", []))
        if not has_words:
            candidate = enrich_track_with_acoustic_words(candidate)
            candidate["synthesized_words"] = True

        return candidate
