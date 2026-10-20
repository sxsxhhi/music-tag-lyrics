# -*- coding: utf-8 -*-
"""
Kugou Music Lyric Fetcher (v1.1.2)
Searches and downloads word-level synchronized KRC lyrics from Kugou Music.
Includes Live vs Studio bidirectional strict mutual exclusion guard,
3-stage adaptive search ladder, and KRCS keyword-direct fallback engine.
Zero external dependencies.
"""

import urllib.request
import urllib.parse
import json
import base64
import re
from typing import List, Dict, Any, Optional, Set

from ..decryptors.krc_xor import decrypt_krc
from ..parsers.krc_parser import parse_krc

USER_AGENT = "KuGouMusic"

LIVE_KEYWORDS = ("live", "现场", "巡回", "巡演", "演唱会", "不插电", "acoustic")
DEMO_KEYWORDS = ("demo", "小样")
REMIX_KEYWORDS = ("remix", "混音")

def is_live_version(text: str) -> bool:
    """Checks whether the text explicitly designates a live performance edition."""
    if not text:
        return False
    t_lower = text.lower()
    return any(k in t_lower for k in LIVE_KEYWORDS)

def strip_subtitle_brackets(title: str) -> str:
    """
    Cleans non-version subtitle/foreign brackets while strictly preserving version tags.
    e.g. '不擅生长的树 (Isness)' -> '不擅生长的树'
         '一腔诗意喂了狗 (Live)' -> '一腔诗意喂了狗 (Live)' (Protected!)
    """
    if not title:
        return ""
    if is_live_version(title):
        return title.strip()
    # Strip trailing bracketed subtitles
    cleaned = re.sub(r"[\(（][^)）]+[\)）]$", "", title).strip()
    return cleaned if cleaned else title

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
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
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
            except Exception:
                pass
                
            # If primary query returned high-quality candidates, don't over-query
            if len(all_results) >= 5:
                break
                
        return all_results

    @staticmethod
    def download_krc_by_id(lyric_id: str, access_key: str) -> Optional[Dict[str, Any]]:
        """Downloads and decrypts KRC lyric file using candidate ID and accesskey."""
        dl_url = f"http://krcs.kugou.com/download?ver=1&client=mobi&id={lyric_id}&accesskey={access_key}&fmt=krc&charset=utf8"
        req_dl = urllib.request.Request(dl_url, headers={"User-Agent": USER_AGENT})
        try:
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
    def search_krc_by_keyword(cls, title: str, artist: str = "", duration_sec: int = 0) -> Optional[Dict[str, Any]]:
        """
        Directly queries krcs.kugou.com keyword search endpoint.
        Acts as secondary fallback when hash-based lookup yields empty candidate lists.
        Filters candidates by duration proximity to prevent studio/live mismatch.
        """
        keyword = f"{title} {artist}".strip()
        search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword={urllib.parse.quote(keyword)}&duration={duration_sec}&hash="
        req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if not candidates:
                    return None
                    
                def get_cand_dur_sec(c):
                    d = int(c.get("duration", 0))
                    if d > 10000:
                        return d // 1000
                    return d
                    
                if duration_sec > 0:
                    valid = []
                    for c in candidates:
                        c_dur = get_cand_dur_sec(c)
                        diff = abs(c_dur - duration_sec)
                        # Reject massive duration diffs (e.g. 131s studio vs 230s live)
                        if diff <= 35:
                            valid.append((diff, c))
                    if valid:
                        valid.sort(key=lambda x: x[0])
                        best_cand = valid[0][1]
                    else:
                        candidates.sort(key=lambda c: abs(get_cand_dur_sec(c) - duration_sec))
                        best_cand = candidates[0]
                else:
                    best_cand = candidates[0]
                    
                return cls.download_krc_by_id(best_cand.get("id"), best_cand.get("accesskey"))
        except Exception:
            return None

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
        """
        duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
        search_url = f"http://krcs.kugou.com/search?ver=1&man=yes&client=mobi&keyword=&duration={duration_sec}&hash={song_hash}"
        req = urllib.request.Request(search_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                candidates = data.get("candidates", [])
                if candidates:
                    best = candidates[0]
                    parsed = cls.download_krc_by_id(best.get("id"), best.get("accesskey"))
                    if parsed and parsed.get("lines"):
                        return parsed
        except Exception:
            pass
            
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
            cand_is_live = is_live_version(s_title) or is_live_version(s_album)
            
            if target_is_live:
                # Local audio is Live: 100% reject studio version
                if not cand_is_live:
                    continue
            else:
                # Local audio is Studio: 100% reject live version
                if cand_is_live:
                    continue
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
            # Title exact match score
            if s_t == t_c or strip_subtitle_brackets(s_t) == strip_subtitle_brackets(t_c):
                t_score = 100
            elif t_c in s_t or s_t in t_c:
                t_score = 50
            else:
                t_score = 0
                
            # Artist match score
            s_a = song.get("artist", "").strip().lower()
            a_c = artist.strip().lower()
            a_score = 20 if (a_c and (a_c in s_a or s_a in a_c)) else 0
            
            # Duration difference
            d_diff = abs(song.get("duration", 0) - duration_ms) if duration_ms > 0 else 0
            return (-t_score, -a_score, d_diff)
            
        if filtered_songs:
            filtered_songs.sort(key=candidate_rank)
            
        # Try candidates in order
        for song in filtered_songs:
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
