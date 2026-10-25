# -*- coding: utf-8 -*-
"""LRCLIB 全球同步歌词：精确 get 失败后按标题搜索，时长容差 ±35s。"""

import urllib.parse
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_get_json, UA_DEFAULT
from ..utils.version_guard import (
    is_live_version,
    is_remix_version,
    matches_version_policy,
    album_match_score,
)


def _stamp_lrclib(parsed: Dict[str, Any], title: str, artist: str,
                  track_name: Optional[str] = None, artist_name: Optional[str] = None,
                  fill_missing_only: bool = False) -> Dict[str, Any]:
    parsed["source"] = "lrclib"
    parsed["lyric_type"] = "line"
    tags = parsed.setdefault("tags", {})
    ti = track_name or title
    ar = artist if artist_name is None else artist_name
    if fill_missing_only:
        if not tags.get("ti"):
            tags["ti"] = ti
        if not tags.get("ar"):
            tags["ar"] = ar
    else:
        tags["ti"] = ti
        tags["ar"] = ar
    return parsed


class LRCLIBFetcher:
    """Fetcher for LRCLIB global synchronized lyrics."""

    @classmethod
    def _direct_get(cls, title: str, artist: str = "", album: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
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
        return data if isinstance(data, dict) else None

    @classmethod
    def _search_list(cls, title: str, artist: str = "") -> list:
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({
            "q": f"{title} {artist}".strip()
        })
        items = http_get_json(url, user_agent=UA_DEFAULT, timeout=6)
        return items if isinstance(items, list) else []

    @classmethod
    def iter_api_items(cls, title: str, artist: str = "", album: str = "", duration_ms: int = 0):
        """精确 get 一条，再搜索若干条；调用方决定用 synced 还是 plain。"""
        data = cls._direct_get(title, artist, album, duration_ms)
        if data:
            yield data
        target_is_live = is_live_version(title)
        n = 0
        for item in cls._search_list(title, artist):
            if not isinstance(item, dict):
                continue
            if not matches_version_policy(target_is_live, item.get("trackName", ""), item.get("albumName", "")):
                continue
            yield item
            n += 1
            if n >= 8:
                break

    @classmethod
    def fetch(cls, title: str, artist: str = "", album: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        data = cls._direct_get(title, artist, album, duration_ms)
        if data:
            synced = data.get("syncedLyrics", "")
            if synced:
                parsed = parse_lrc(synced)
                return _stamp_lrclib(
                    parsed, title, artist,
                    track_name=data.get("trackName", title),
                    artist_name=data.get("artistName", artist),
                    fill_missing_only=True,
                )

        return cls.search_and_fetch(title, artist, duration_ms, album=album)

    @classmethod
    def search_and_fetch(cls, title: str, artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[Dict[str, Any]]:
        target_is_live = is_live_version(title)
        target_is_remix = is_remix_version(title) or is_remix_version(album)
        valid_items = []
        for item in cls._search_list(title, artist):
            track_name = item.get("trackName", "")
            album_name = item.get("albumName", "")
            if not matches_version_policy(target_is_live, track_name, album_name, target_is_remix=target_is_remix):
                continue
            if not item.get("syncedLyrics"):
                continue

            item_dur_sec = float(item.get("duration", 0) or 0)
            if duration_ms > 0 and item_dur_sec > 0:
                diff_sec = abs(item_dur_sec - (duration_ms / 1000.0))
                if diff_sec > 35:
                    continue
                valid_items.append((diff_sec, item))
            else:
                valid_items.append((0, item))

        if valid_items:
            # v1.3.5 O1：与酷狗/网易对称——同名专辑加权优先于时长抖动
            valid_items.sort(key=lambda x: (-album_match_score(album, x[1].get("albumName", "")), x[0]))
            for _, item in valid_items:
                synced = item.get("syncedLyrics", "")
                if synced:
                    parsed = parse_lrc(synced)
                    return _stamp_lrclib(
                        parsed, title, artist,
                        track_name=item.get("trackName") or title,
                        artist_name=item.get("artistName", artist),
                    )

        return None
