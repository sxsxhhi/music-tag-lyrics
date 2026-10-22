# -*- coding: utf-8 -*-
"""网易云歌词：检索、Live 互斥、时长门禁、官方 tlyric。"""

from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_post_json, http_get_json, UA_DESKTOP
from ..utils.version_guard import (
    is_live_version,
    matches_version_policy,
    is_duration_acceptable,
    is_title_fuzzy_match,
)

class NetEaseFetcher:
    """Fetcher for NetEase Cloud Music lyrics with Version Guard."""

    @classmethod
    def search_song_id(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[int]:
        """
        Searches NetEase song catalog and returns the best matching song ID.
        Applies version mutual exclusion (Live vs Studio) and duration filtering.
        """
        keyword = f"{title} {artist}".strip()
        search_url = "https://music.163.com/api/search/get/web?csrf_token="
        post_data = {
            "s": keyword,
            "type": 1,
            "offset": 0,
            "total": "true",
            "limit": 6
        }
        headers = {"Referer": "https://music.163.com"}
        data = http_post_json(search_url, data=post_data, headers=headers, user_agent=UA_DESKTOP, timeout=6)
        if not data or not isinstance(data, dict):
            return None

        songs = data.get("result", {}).get("songs", [])
        if not songs:
            return None

        target_is_live = is_live_version(title)
        
        # Filter candidates by version policy and duration
        valid_candidates = []
        for s in songs:
            s_name = s.get("name", "")
            s_album = s.get("album", {}).get("name", "")
            s_dur = s.get("duration", 0)  # ms
            
            if not is_title_fuzzy_match(s_name, title):
                continue

            # Version policy check
            if not matches_version_policy(target_is_live, s_name, s_album):
                continue
                
            # Duration acceptability check
            if not is_duration_acceptable(s_dur, duration_ms, tolerance_sec=35):
                continue
                
            d_diff = abs(s_dur - duration_ms) if duration_ms > 0 else 0
            valid_candidates.append((d_diff, s))

        if valid_candidates:
            valid_candidates.sort(key=lambda x: x[0])
            return valid_candidates[0][1].get("id")

        return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        """Retrieves and parses LRC lyrics for the specified song."""
        song_id = cls.search_song_id(title, artist, duration_ms)
        if not song_id:
            return None

        lyric_url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
        headers = {"Referer": "https://music.163.com"}
        data = http_get_json(lyric_url, headers=headers, user_agent=UA_DESKTOP, timeout=6)
        if not data or not isinstance(data, dict):
            return None

        lrc_text = data.get("lrc", {}).get("lyric", "")
        if not lrc_text:
            return None

        parsed = parse_lrc(lrc_text)
        parsed["source"] = "netease"
        parsed["lyric_type"] = "line"
        parsed.setdefault("tags", {})["ti"] = title
        parsed.setdefault("tags", {})["ar"] = artist

        # Official reviewed translation from NetEase (tlyric)
        tlyric_text = data.get("tlyric", {}).get("lyric", "")
        if tlyric_text:
            parsed["tlyric"] = tlyric_text

        return parsed
