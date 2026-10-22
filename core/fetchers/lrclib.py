# -*- coding: utf-8 -*-
"""LRCLIB 全球同步歌词：精确 get 失败后按标题搜索，时长容差 ±35s。"""

import urllib.parse
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_get_json, UA_DEFAULT
from ..utils.version_guard import is_live_version, matches_version_policy


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
                return _stamp_lrclib(
                    parsed, title, artist,
                    track_name=data.get("trackName", title),
                    artist_name=data.get("artistName", artist),
                    fill_missing_only=True,
                )

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
        valid_items = []
        for item in items:
            track_name = item.get("trackName", "")
            album_name = item.get("albumName", "")
            # Filter by version policy
            if not matches_version_policy(target_is_live, track_name, album_name):
                continue
            if not item.get("syncedLyrics"):
                continue

            item_dur_sec = float(item.get("duration", 0))
            if duration_ms > 0 and item_dur_sec > 0:
                diff_sec = abs(item_dur_sec - (duration_ms / 1000.0))
                # Skip candidates that deviate excessively from target track
                if diff_sec > 35:
                    continue
                valid_items.append((diff_sec, item))
            else:
                valid_items.append((0, item))

        if valid_items:
            valid_items.sort(key=lambda x: x[0])
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
