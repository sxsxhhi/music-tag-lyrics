# -*- coding: utf-8 -*-
"""
LRCLIB Synced Lyric Fetcher (v1.1.3)
Fetches open-source synchronized lyrics from the LRCLIB global database (lrclib.net).
Particularly excellent for Western, J-Pop, K-Pop, and global indie releases.
Refactored to reuse shared http utility (DRY architecture).
Zero external dependencies.
"""

import urllib.parse
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_get_json, UA_DEFAULT
from ..utils.version_guard import is_live_version, matches_version_policy

class LRCLIBFetcher:
    """Fetcher for LRCLIB global synchronized lyrics."""

    @classmethod
    def fetch(cls, title: str, artist: str = "", album: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        query_params = {
            "track_name": title,
            "artist_name": artist
        }
        if album:
            query_params["album_name"] = album
        if duration_ms > 0:
            query_params["duration"] = int(duration_ms // 1000)

        url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(query_params)
        data = http_get_json(url, user_agent=UA_DEFAULT, timeout=6)
        if data and isinstance(data, dict):
            synced = data.get("syncedLyrics", "")
            if synced:
                parsed = parse_lrc(synced)
                parsed["source"] = "lrclib"
                parsed["lyric_type"] = "line"
                if not parsed.get("tags", {}).get("ti"):
                    parsed.setdefault("tags", {})["ti"] = data.get("trackName", title)
                if not parsed.get("tags", {}).get("ar"):
                    parsed.setdefault("tags", {})["ar"] = data.get("artistName", artist)
                return parsed

        # Fallback search if direct get fails
        return cls.search_and_fetch(title, artist, duration_ms)

    @classmethod
    def search_and_fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        query_params = {
            "q": f"{title} {artist}".strip()
        }
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(query_params)
        items = http_get_json(url, user_agent=UA_DEFAULT, timeout=6)
        if not items or not isinstance(items, list):
            return None

        target_is_live = is_live_version(title)
        for item in items:
            track_name = item.get("trackName", "")
            album_name = item.get("albumName", "")
            # Filter by version policy
            if not matches_version_policy(target_is_live, track_name, album_name):
                continue
                
            synced = item.get("syncedLyrics", "")
            if synced:
                parsed = parse_lrc(synced)
                parsed["source"] = "lrclib"
                parsed["lyric_type"] = "line"
                parsed.setdefault("tags", {})["ti"] = track_name or title
                parsed.setdefault("tags", {})["ar"] = item.get("artistName", artist)
                return parsed

        return None
