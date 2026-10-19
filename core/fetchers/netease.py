# -*- coding: utf-8 -*-
"""
NetEase Cloud Music Lyric Fetcher
Searches and retrieves synchronized lyrics and translations from NetEase Music.
Zero external dependencies.
"""

import urllib.request
import urllib.parse
import json
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

class NetEaseFetcher:
    """Fetcher for NetEase Cloud Music lyrics"""

    @classmethod
    def search_song_id(cls, title: str, artist: str = "") -> Optional[int]:
        keyword = f"{title} {artist}".strip()
        search_url = "https://music.163.com/api/search/get/web?csrf_token="
        post_data = urllib.parse.urlencode({
            "s": keyword,
            "type": 1,
            "offset": 0,
            "total": "true",
            "limit": 3
        }).encode("utf-8")
        
        req = urllib.request.Request(search_url, data=post_data, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://music.163.com"
        })
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                songs = data.get("result", {}).get("songs", [])
                if songs:
                    return songs[0].get("id")
        except Exception:
            pass
        return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        song_id = cls.search_song_id(title, artist)
        if not song_id:
            return None

        lyric_url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
        req = urllib.request.Request(lyric_url, headers={
            "User-Agent": USER_AGENT,
            "Referer": "https://music.163.com"
        })
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                lrc_text = data.get("lrc", {}).get("lyric", "")
                if not lrc_text:
                    return None
                parsed = parse_lrc(lrc_text)
                parsed["source"] = "netease"
                parsed["lyric_type"] = "line"
                parsed.setdefault("tags", {})["ti"] = title
                parsed.setdefault("tags", {})["ar"] = artist
                return parsed
        except Exception:
            pass
        return None
