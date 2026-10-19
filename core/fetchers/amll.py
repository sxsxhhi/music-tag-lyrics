# -*- coding: utf-8 -*-
"""
AMLL (Apple Music Lossless Lyrics) Community Database Fetcher
Fetches high-precision word-level TTML lyrics from amll-dev/amll-ttml-db on GitHub / jsDelivr.
Zero external dependencies.
"""

import urllib.request
from typing import Dict, Any, Optional

from ..parsers.ttml_parser import parse_ttml
from .netease import NetEaseFetcher

USER_AGENT = "music-tag-lyrics/1.0"
BASE_URL = "https://cdn.jsdelivr.net/gh/amll-dev/amll-ttml-db@main"

class AMLLFetcher:
    """Fetcher for AMLL TTML lyrics"""

    @classmethod
    def fetch_by_ncm_id(cls, ncm_id: int) -> Optional[Dict[str, Any]]:
        url = f"{BASE_URL}/ncm-lyrics/{ncm_id}.ttml"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                content = resp.read().decode("utf-8")
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
        # First find NetEase song id as primary key
        ncm_id = NetEaseFetcher.search_song_id(title, artist)
        if ncm_id:
            res = cls.fetch_by_ncm_id(ncm_id)
            if res:
                return res
        return None
