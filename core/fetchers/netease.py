# -*- coding: utf-8 -*-
"""网易云歌词：检索、Live 互斥、时长门禁、官方 tlyric。"""

import time
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_post_json, http_get_json, UA_DESKTOP
from ..utils.version_guard import (
    is_live_version,
    is_remix_version,
    matches_version_policy,
    is_duration_acceptable,
    is_title_fuzzy_match,
    album_match_score,
)

class NetEaseFetcher:
    """Fetcher for NetEase Cloud Music lyrics with Version Guard."""

    @classmethod
    def search_best_song(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = ""
    ) -> Optional[Dict[str, Any]]:
        """检索网易曲目，返回最匹配的一首（含 album.publishTime）。

        v1.3.5 O1：与酷狗对称——Remix 双向互斥 + 同名专辑加权优先于时长抖动。
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
        target_is_remix = is_remix_version(title) or is_remix_version(album)
        valid_candidates = []
        for s in songs:
            s_name = s.get("name", "")
            s_album = s.get("album", {}).get("name", "")
            s_dur = s.get("duration", 0)

            if not is_title_fuzzy_match(s_name, title):
                continue
            if not matches_version_policy(target_is_live, s_name, s_album, target_is_remix=target_is_remix):
                continue
            if not is_duration_acceptable(s_dur, duration_ms, tolerance_sec=35):
                continue
            d_diff = abs(s_dur - duration_ms) if duration_ms > 0 else 0
            valid_candidates.append((d_diff, s))

        if not valid_candidates:
            return None
        valid_candidates.sort(
            key=lambda x: (-album_match_score(album, x[1].get("album", {}).get("name", "")), x[0])
        )
        return valid_candidates[0][1]

    @classmethod
    def search_song_id(cls, title: str = "", artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[int]:
        """
        Searches NetEase song catalog and returns the best matching song ID.
        Applies version mutual exclusion (Live vs Studio) and duration filtering.
        """
        song = cls.search_best_song(title, artist, duration_ms, album=album)
        if not song:
            return None
        return song.get("id")

    @classmethod
    def lookup_year(cls, title: str, artist: str = "", duration_ms: int = 0) -> str:
        """从网易专辑 publishTime 取四位年份。"""
        song = cls.search_best_song(title, artist, duration_ms)
        if not song:
            return ""
        ts = (song.get("album") or {}).get("publishTime") or 0
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            return ""
        if ts_i <= 0:
            return ""
        if ts_i > 10_000_000_000:
            ts_i //= 1000
        year = time.gmtime(ts_i).tm_year
        if 1900 <= year <= 2099:
            return str(year)
        return ""

    @classmethod
    def fetch_raw_lyrics(cls, title: str, artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[Dict[str, str]]:
        """同一首歌的 lrc / tlyric 原文（可无时间戳）。供同步解析与 GPU 纯文本共用。"""
        song_id = cls.search_song_id(title, artist, duration_ms, album=album)
        if not song_id:
            return None

        lyric_url = f"https://music.163.com/api/song/lyric?id={song_id}&lv=1&kv=1&tv=-1"
        headers = {"Referer": "https://music.163.com"}
        data = http_get_json(lyric_url, headers=headers, user_agent=UA_DESKTOP, timeout=6)
        if not data or not isinstance(data, dict):
            return None
        lrc_text = (data.get("lrc") or {}).get("lyric") or ""
        tlyric_text = (data.get("tlyric") or {}).get("lyric") or ""
        if not lrc_text and not tlyric_text:
            return None
        return {"lrc": lrc_text, "tlyric": tlyric_text}

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[Dict[str, Any]]:
        """Retrieves and parses LRC lyrics for the specified song."""
        raw = cls.fetch_raw_lyrics(title, artist, duration_ms, album=album)
        if not raw or not raw.get("lrc"):
            return None

        parsed = parse_lrc(raw["lrc"])
        parsed["source"] = "netease"
        parsed["lyric_type"] = "line"
        parsed.setdefault("tags", {})["ti"] = title
        parsed.setdefault("tags", {})["ar"] = artist

        tlyric_text = raw.get("tlyric") or ""
        if tlyric_text:
            parsed["tlyric"] = tlyric_text

        return parsed
