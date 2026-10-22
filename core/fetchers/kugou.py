# -*- coding: utf-8 -*-
"""酷狗 KRC 抓取：Live 互斥、KRCS 降级、adjust 叠加、时长门禁。"""

import urllib.parse
import base64
from typing import List, Dict, Any, Optional

from ..decryptors.krc_xor import decrypt_krc
from ..parsers.krc_parser import parse_krc
from ..utils.http import http_get_json, UA_KUGOU
from ..utils.text_utils import is_instrumental_placeholder
from ..utils.version_guard import (
    is_live_version,
    strip_subtitle_brackets,
    matches_version_policy,
    is_title_fuzzy_match,
)


def _reject_parsed_duration(last_end_ms: int, target_duration_ms: int) -> bool:
    """长曲预览截断或末行偏差过大则拒绝。阈值 90000/45000/8000 原样保留。"""
    if target_duration_ms <= 0:
        return False
    if target_duration_ms > 90000 and last_end_ms < 45000:
        return True
    return abs(last_end_ms - target_duration_ms) > 8000

def select_best_krc_candidate(
    candidates: List[Dict[str, Any]],
    download_fn,
    max_probes: int = 6,
    target_duration_ms: int = 0
) -> Optional[Dict[str, Any]]:
    """
    Centralized candidate selection logic:
    Prioritizes complete candidates with native embedded translation matching target duration.
    Rejects collapsed instrumental placeholders and preview clips.
    """
    scored_candidates = []
    for c in candidates[:max_probes]:
        cid = c.get("id")
        ack = c.get("accesskey")
        if not cid or not ack:
            continue
        adjust = int(c.get("adjust", 0))
        parsed = download_fn(cid, ack, adjust=adjust)
        if not parsed or not parsed.get("lines"):
            continue
            
        lines = parsed["lines"]
        
        # Hard Gate: Reject collapsed instrumental placeholders (G7: 统一走 text_utils)
        if is_instrumental_placeholder(lines, target_duration_ms):
            continue
                
        cand_last_end = lines[-1].get("end", lines[-1].get("start", 0))
        if _reject_parsed_duration(cand_last_end, target_duration_ms):
            continue
            
        # Scoring
        score = 0
        has_trans = parsed.get("has_embedded_translation", False)
        if has_trans:
            score += 1000
            
        # Iron Rule 17: Duration Proximity Gate
        if target_duration_ms > 0:
            dur_diff = abs(cand_last_end - target_duration_ms)
            if dur_diff <= 3000:
                score += 500
            elif dur_diff <= 5000:
                score += 300
            else:
                score += 100
                
        score += min(len(lines), 100)
        scored_candidates.append((score, parsed))
        
    if not scored_candidates:
        return None
        
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    return scored_candidates[0][1]

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

        # P1-1: 这条兜底路径以前完全没有版本门禁 —— 只比标题和时长，
        # Live 目标可能拿到录音室版本、反之亦然。三个调用点都经过这里，所以在入口统一拦截。
        target_is_live = is_live_version(title)

        def _version_ok(c) -> bool:
            return matches_version_policy(
                target_is_live,
                c.get("song", "") or "",
                c.get("album", "") or ""
            )

        if duration_sec > 0:
            valid = []
            for c in candidates:
                if not is_title_fuzzy_match(c.get("song", ""), title):
                    continue
                if not _version_ok(c):
                    continue
                c_dur = get_cand_dur_sec(c)
                diff = abs(c_dur - duration_sec)
                # Iron Rule 17: Candidate duration within 8s proximity
                if diff <= 8:
                    valid.append((diff, c))
            if valid:
                valid.sort(key=lambda x: x[0])
                ranked_cands = [x[1] for x in valid]
            else:
                return None
        else:
            title_matching = [c for c in candidates
                              if is_title_fuzzy_match(c.get("song", ""), title) and _version_ok(c)]
            if not title_matching:
                return None
            ranked_cands = title_matching
            
        return select_best_krc_candidate(ranked_cands, cls.download_krc_by_id, target_duration_ms=duration_sec*1000)

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
                candidates.sort(key=lambda c: int(c.get("score", 0)), reverse=True)
                res = select_best_krc_candidate(candidates, cls.download_krc_by_id, target_duration_ms=duration_ms)
                if res and res.get("lines"):
                    return res
                    
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
        title_matched_songs = [s for s in filtered_songs if is_title_fuzzy_match(s.get("title", ""), title)]
        if title_matched_songs:
            title_matched_songs.sort(key=candidate_rank)
            
        # Try candidates in order
        for song in title_matched_songs:
            h = song.get("hash")
            s_dur = song.get("duration", 0)
            # Iron Rule 17: Pre-filter candidate song metadata duration
            if duration_ms > 0 and s_dur > 0 and abs(s_dur - duration_ms) > 10000:
                continue
            if h:
                res = cls.fetch_lyrics_by_hash(h, duration_ms, title, artist)
                if res and res.get("lines"):
                    lines = res["lines"]
                    # Iron Rule 17: Enforce duration proximity on actual parsed lines
                    if duration_ms > 0 and lines:
                        last_t_ms = lines[-1].get("end", lines[-1].get("start", 0))
                        if _reject_parsed_duration(last_t_ms, duration_ms):
                            continue
                    return res
                    
        # Final safety net: if still not found, try krcs keyword search
        if title:
            duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
            return cls.search_krc_by_keyword(title, artist, duration_sec)
            
        return None
