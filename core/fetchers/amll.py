# -*- coding: utf-8 -*-
"""
AMLL (Apple Music Lossless Lyrics) Community Database Fetcher (v1.2.4)
Fetches high-precision word-level TTML lyrics from amll-dev/amll-ttml-db on GitHub / jsDelivr.
Links via NetEase song ID resolved with Version Guard.
Zero external dependencies.
"""

from typing import Dict, Any, Optional

from ..parsers.ttml_parser import parse_ttml
from .netease import NetEaseFetcher
from ..utils.http import http_get_text, UA_DEFAULT

BASE_URL = "https://cdn.jsdelivr.net/gh/amll-dev/amll-ttml-db@main"

class AMLLFetcher:
    """Fetcher for AMLL TTML lyrics."""

    @classmethod
    def fetch_by_ncm_id(cls, ncm_id: int) -> Optional[Dict[str, Any]]:
        url = f"{BASE_URL}/ncm-lyrics/{ncm_id}.ttml"
        content = http_get_text(url, timeout=5, user_agent=UA_DEFAULT)
        if not content:
            return None
            
        try:
            parsed = parse_ttml(content)
            if parsed and parsed.get("lines"):
                parsed["source"] = "amll"
                parsed["lyric_type"] = "word"
                return parsed
        except Exception:
            pass
        return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        # Use NetEaseFetcher with Version Guard and duration filtering
        ncm_id = NetEaseFetcher.search_song_id(title, artist, duration_ms)
        if ncm_id:
            res = cls.fetch_by_ncm_id(ncm_id)
            if res:
                return res
        return None
