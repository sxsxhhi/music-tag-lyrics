# -*- coding: utf-8 -*-
"""
LRCLIB Synced Lyric Fetcher
Fetches open-source synchronized lyrics from the LRCLIB global database (lrclib.net).
Particularly excellent for Western, J-Pop, K-Pop, and global indie releases.
Zero external dependencies.
"""

import urllib.request
import urllib.parse
import json
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc

USER_AGENT = "music-tag-lyrics/1.0"

class LRCLIBFetcher:
    """Fetcher for LRCLIB global synchronized lyrics"""

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
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                synced = data.get("syncedLyrics", "")
                if not synced:
                    return None
                parsed = parse_lrc(synced)
                parsed["source"] = "lrclib"
                parsed["lyric_type"] = "line"
                if not parsed.get("tags", {}).get("ti"):
                    parsed.setdefault("tags", {})["ti"] = data.get("trackName", title)
                if not parsed.get("tags", {}).get("ar"):
                    parsed.setdefault("tags", {})["ar"] = data.get("artistName", artist)
                return parsed
        except Exception:
            # Fallback search if direct get fails
            return cls.search_and_fetch(title, artist)

    @classmethod
    def search_and_fetch(cls, title: str, artist: str = "") -> Optional[Dict[str, Any]]:
        query_params = {
            "q": f"{title} {artist}".strip()
        }
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode(query_params)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                items = json.loads(resp.read().decode("utf-8"))
                for item in items:
                    synced = item.get("syncedLyrics", "")
                    if synced:
                        parsed = parse_lrc(synced)
                        parsed["source"] = "lrclib"
                        parsed["lyric_type"] = "line"
                        parsed.setdefault("tags", {})["ti"] = item.get("trackName", title)
                        parsed.setdefault("tags", {})["ar"] = item.get("artistName", artist)
                        return parsed
        except Exception:
            pass
        return None
