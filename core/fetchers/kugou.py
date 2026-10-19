# -*- coding: utf-8 -*-
"""
Kugou Music Lyric Fetcher
Searches and downloads word-level synchronized KRC lyrics from Kugou Music.
Zero external dependencies.
"""

import urllib.request
import urllib.parse
import json
import base64
from typing import List, Dict, Any, Optional

from ..decryptors.krc_xor import decrypt_krc
from ..parsers.krc_parser import parse_krc

USER_AGENT = "KuGouMusic"

class KugouFetcher:
    """Fetcher for Kugou Music KRC lyrics"""
    
    @staticmethod
    def search_song(title: str, artist: str = "") -> List[Dict[str, Any]]:
        keyword = f"{title} {artist}".strip()
        url = "http://mobilecdn.kugou.com/api/v3/search/song?" + urllib.parse.urlencode({
            "format": "json",
            "keyword": keyword,
            "page": 1,
            "pagesize": 5
        })
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        results = []
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                songs = data.get("data", {}).get("info", [])
                for s in songs:
                    results.append({
                        "source": "kugou",
                        "title": s.get("songname", ""),
                        "artist": s.get("singername", ""),
                        "album": s.get("album_name", ""),
                        "duration": int(s.get("duration", 0)) * 1000,
                        "hash": s.get("hash", "")
                    })
        except Exception as e:
            # Silently handle network errors
            pass
        return results

    @staticmethod
    def fetch_lyrics_by_hash(song_hash: str, duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
        search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword=&duration={duration_sec}&hash={song_hash}"
        req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                best = candidates[0]
                lyric_id = best.get("id")
                access_key = best.get("accesskey")
                
                # Download lyric
                dl_url = f"http://krcs.kugou.com/download?ver=1&client=mobi&id={lyric_id}&accesskey={access_key}&fmt=krc&charset=utf8"
                req_dl = urllib.request.Request(dl_url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req_dl, timeout=6) as resp_dl:
                    dl_data = json.loads(resp_dl.read().decode("utf-8"))
                    content_b64 = dl_data.get("content", "")
                    if not content_b64:
                        return None
                    raw_bytes = base64.b64decode(content_b64)
                    krc_text = decrypt_krc(raw_bytes)
                    parsed = parse_krc(krc_text)
                    parsed["source"] = "kugou"
                    parsed["lyric_type"] = "word"  # Word-level verbatim
                    return parsed
        except Exception:
            return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        songs = cls.search_song(title, artist)
        if not songs:
            return None
        
        if duration_ms > 0:
            songs.sort(key=lambda s: abs(s.get("duration", 0) - duration_ms))
        
        # Try candidates in order
        for song in songs:
            h = song.get("hash")
            d = song.get("duration", duration_ms)
            if h:
                res = cls.fetch_lyrics_by_hash(h, d)
                if res and res.get("lines"):
                    lines = res["lines"]
                    # If target duration is substantial, avoid severely truncated candidates
                    if duration_ms > 120000 and lines:
                        last_t_ms = lines[-1].get("end", lines[-1].get("start", 0))
                        if last_t_ms < duration_ms - 60000:
                            continue
                    return res
        return None
