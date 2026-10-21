# -*- coding: utf-8 -*-
"""
Kugou Music Lyric Fetcher (v1.1.3)
Searches and downloads word-level synchronized KRC lyrics from Kugou Music.
Enforces bidirectional Live vs Studio mutual exclusion, multi-stage query ladder,
KRCS keyword direct fallback, and server-side adjust offset application.
Refactored to reuse shared http and version_guard utilities (DRY architecture).
Zero external dependencies.
"""

import urllib.parse
import base64
from typing import List, Dict, Any, Optional

from ..decryptors.krc_xor import decrypt_krc
from ..parsers.krc_parser import parse_krc
from ..utils.http import http_get_json, UA_KUGOU
from ..utils.version_guard import (
    is_live_version,
    strip_subtitle_brackets,
    matches_version_policy,
    is_duration_acceptable
)

class KugouFetcher:
    """Fetcher for Kugou Music KRC lyrics with Live-guard and KRCS fallback."""
    
    @staticmethod
    def search_song(title: str, artist: str = "") -> List[Dict[str, Any]]:
        """
        Executes search with multi-stage query ladder:
        1. Full title (including version brackets) + artist
        2. Cleaned title (stripped subtitles, keeping version) + artist
        """
        queries = []
        q1 = f"{title} {artist}".strip()
        queries.append(q1)
        
        clean_t = strip_subtitle_brackets(title)
        if clean_t != title:
            q2 = f"{clean_t} {artist}".strip()
            if q2 not in queries:
                queries.append(q2)
                
        all_results = []
        seen_hashes = set()
        
        for q in queries:
            url = "http://mobilecdn.kugou.com/api/v3/search/song?" + urllib.parse.urlencode({
                "format": "json",
                "keyword": q,
                "page": 1,
                "pagesize": 8
            })
            data = http_get_json(url, user_agent=UA_KUGOU, timeout=6)
            if data and isinstance(data, dict):
                songs = data.get("data", {}).get("info", [])
                for s in songs:
                    h = s.get("hash", "")
                    if h and h not in seen_hashes:
                        seen_hashes.add(h)
                        all_results.append({
                            "source": "kugou",
                            "title": s.get("songname", ""),
                            "artist": s.get("singername", ""),
                            "album": s.get("album_name", ""),
                            "duration": int(s.get("duration", 0)) * 1000,
                            "hash": h
                        })
                        
            if len(all_results) >= 5:
                break
                
        return all_results

    @staticmethod
    def download_krc_by_id(lyric_id: str, access_key: str, adjust: int = 0) -> Optional[Dict[str, Any]]:
        """Downloads, decrypts and parses KRC lyric file with server-side adjust offset."""
        dl_url = f"http://krcs.kugou.com/download?ver=1&client=mobi&id={lyric_id}&accesskey={access_key}&fmt=krc&charset=utf8"
        dl_data = http_get_json(dl_url, user_agent=UA_KUGOU, timeout=6)
        if not dl_data or not isinstance(dl_data, dict):
            return None
            
        content_b64 = dl_data.get("content", "")
        if not content_b64:
            return None
            
        try:
            raw_bytes = base64.b64decode(content_b64)
            krc_text = decrypt_krc(raw_bytes)
            parsed = parse_krc(krc_text, adjust=adjust)
            parsed["source"] = "kugou"
            parsed["lyric_type"] = "word"  # Word-level verbatim
            return parsed
        except Exception:
            return None

    @classmethod
    def search_krc_by_keyword(cls, title: str, artist: str = "", duration_sec: int = 0) -> Optional[Dict[str, Any]]:
        """
        Directly queries krcs.kugou.com keyword search endpoint.
        Acts as secondary fallback when hash-based lookup yields empty candidate lists.
        Filters candidates by duration proximity to prevent studio/live mismatch.
        """
        keyword = f"{title} {artist}".strip()
        search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword={urllib.parse.quote(keyword)}&duration={duration_sec}&hash="
        data = http_get_json(search_url, user_agent=UA_KUGOU, timeout=6)
        if not data or not isinstance(data, dict):
            return None
            
        candidates = data.get("candidates", [])
        if not candidates:
            return None
            
        def get_cand_dur_sec(c):
            d = int(c.get("duration", 0))
            return d // 1000 if d > 10000 else d
            
        def is_title_match(cand_title: str) -> bool:
            c_t = cand_title.strip().lower()
            t_c = title.strip().lower()
            if not c_t or not t_c:
                return False
            return (
                c_t == t_c or
                strip_subtitle_brackets(c_t) == strip_subtitle_brackets(t_c) or
                t_c in c_t or
                c_t in t_c
            )

        if duration_sec > 0:
            valid = []
            for c in candidates:
                if not is_title_match(c.get("song", "")):
                    continue
                c_dur = get_cand_dur_sec(c)
                diff = abs(c_dur - duration_sec)
                if diff <= 35:
                    valid.append((diff, c))
            if valid:
                valid.sort(key=lambda x: x[0])
                ranked_cands = [x[1] for x in valid]
            else:
                title_matching = [c for c in candidates if is_title_match(c.get("song", ""))]
                if not title_matching:
                    return None
                title_matching.sort(key=lambda c: abs(get_cand_dur_sec(c) - duration_sec))
                ranked_cands = title_matching
        else:
            title_matching = [c for c in candidates if is_title_match(c.get("song", ""))]
            if not title_matching:
                return None
            ranked_cands = title_matching
            
        best_parsed = None
        for c in ranked_cands[:6]:
            adjust = int(c.get("adjust", 0))
            parsed = cls.download_krc_by_id(c.get("id"), c.get("accesskey"), adjust=adjust)
            if parsed and parsed.get("lines"):
                if parsed.get("has_embedded_translation"):
                    return parsed
                if not best_parsed:
                    best_parsed = parsed
        return best_parsed

    @classmethod
    def fetch_lyrics_by_hash(
        cls,
        song_hash: str,
        duration_ms: int = 0,
        title: str = "",
        artist: str = ""
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves KRC lyrics by song hash.
        If hash lookup returns no candidates, automatically falls back to KRCS keyword search.
        Applies adjust offset from the best candidate.
        """
        duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
        search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword=&duration={duration_sec}&hash={song_hash}"
        data = http_get_json(search_url, user_agent=UA_KUGOU, timeout=6)
        if data and isinstance(data, dict):
            candidates = data.get("candidates", [])
            if candidates:
                # Sort candidates by score descending
                candidates.sort(key=lambda c: int(c.get("score", 0)), reverse=True)
                best_parsed = None
                for c in candidates[:6]:
                    adjust = int(c.get("adjust", 0))
                    parsed = cls.download_krc_by_id(c.get("id"), c.get("accesskey"), adjust=adjust)
                    if parsed and parsed.get("lines"):
                        # Prioritize candidates with native embedded translation
                        if parsed.get("has_embedded_translation"):
                            return parsed
                        if not best_parsed:
                            best_parsed = parsed
                if best_parsed:
                    return best_parsed
                    
        # Fallback to keyword direct query if title is provided
        if title:
            return cls.search_krc_by_keyword(title, artist, duration_sec)
            
        return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        """
        Main entry point for fetching KRC lyrics.
        Enforces bidirectional strict version exclusion (Live vs Studio).
        """
        songs = cls.search_song(title, artist)
        target_is_live = is_live_version(title)
        
        # Apply strict version mutual exclusion
        filtered_songs = []
        for song in songs:
            s_title = song.get("title", "")
            s_album = song.get("album", "")
            if matches_version_policy(target_is_live, s_title, s_album):
                filtered_songs.append(song)
            
        # If strict filter left no songs and target is live, search krcs directly
        if not filtered_songs and target_is_live:
            duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
            direct_krc = cls.search_krc_by_keyword(title, artist, duration_sec)
            if direct_krc and direct_krc.get("lines"):
                return direct_krc
                
        def candidate_rank(song):
            s_t = song.get("title", "").strip().lower()
            t_c = title.strip().lower()
            if s_t == t_c or strip_subtitle_brackets(s_t) == strip_subtitle_brackets(t_c):
                t_score = 100
            elif t_c in s_t or s_t in t_c:
                t_score = 50
            else:
                t_score = 0
                
            s_a = song.get("artist", "").strip().lower()
            a_c = artist.strip().lower()
            a_score = 20 if (a_c and (a_c in s_a or s_a in a_c)) else 0
            
            d_diff = abs(song.get("duration", 0) - duration_ms) if duration_ms > 0 else 0
            return (-t_score, -a_score, d_diff)
            
        # Hard gate: only retain songs whose title actually matches
        title_matched_songs = []
        for song in filtered_songs:
            s_t = song.get("title", "").strip().lower()
            t_c = title.strip().lower()
            if s_t == t_c or strip_subtitle_brackets(s_t) == strip_subtitle_brackets(t_c) or t_c in s_t or s_t in t_c:
                title_matched_songs.append(song)

        if title_matched_songs:
            title_matched_songs.sort(key=candidate_rank)
            
        # Try candidates in order
        for song in title_matched_songs:
            h = song.get("hash")
            d = song.get("duration", duration_ms)
            if h:
                res = cls.fetch_lyrics_by_hash(h, d, title, artist)
                if res and res.get("lines"):
                    lines = res["lines"]
                    # Protect instrumental outro: only reject genuine preview clips (e.g. <45s for a long track)
                    if duration_ms > 90000 and lines:
                        last_t_ms = lines[-1].get("end", lines[-1].get("start", 0))
                        if last_t_ms < 45000:
                            continue
                    return res
                    
        # Final safety net: if still not found, try krcs keyword search
        if title:
            duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
            return cls.search_krc_by_keyword(title, artist, duration_sec)
            
        return None
