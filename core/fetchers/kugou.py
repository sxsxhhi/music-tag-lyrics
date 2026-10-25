# -*- coding: utf-8 -*-
"""酷狗 KRC 抓取：Live/Remix 互斥、专辑加权、KRCS 降级、adjust 叠加、时长门禁。"""

import urllib.parse
import base64
from typing import List, Dict, Any, Optional

from ..decryptors.krc_xor import decrypt_krc
from ..parsers.krc_parser import parse_krc
from ..utils.http import http_get_json, UA_KUGOU
from ..utils.text_utils import is_instrumental_placeholder
from ..utils.version_guard import (
    is_live_version,
    is_remix_version,
    strip_subtitle_brackets,
    matches_version_policy,
    is_title_fuzzy_match,
)

# 铁律 17：搜歌元数据 ±10s；解析末行只拒预览与冲轴。
KRCS_METADATA_WINDOW_SEC = 10
PREVIEW_MIN_TRACK_MS = 90000
PREVIEW_MAX_LAST_END_MS = 45000
OVERRUN_TOLERANCE_MS = 8000

# empty = 搜歌 0 条；gated = 有候选被门禁丢掉；ok = 交出歌词
_LAST_KUGOU_STATUS = "empty"


def last_kugou_status() -> str:
    return _LAST_KUGOU_STATUS


def _set_kugou_status(kind: str) -> None:
    global _LAST_KUGOU_STATUS
    _LAST_KUGOU_STATUS = kind


def parsed_duration_reject_reason(last_end_ms: int, target_duration_ms: int) -> Optional[str]:
    """预览 / 冲轴才拒；末行早于曲终是尾奏，返回 None。"""
    if target_duration_ms <= 0:
        return None
    if target_duration_ms > PREVIEW_MIN_TRACK_MS and last_end_ms < PREVIEW_MAX_LAST_END_MS:
        return "preview"
    if last_end_ms > target_duration_ms + OVERRUN_TOLERANCE_MS:
        return "overrun"
    return None


def _reject_parsed_duration(last_end_ms: int, target_duration_ms: int) -> bool:
    """只拦预览截断和末字冲过曲长；尾奏导致末行早于曲终不拒（铁律 1 / F-03）。"""
    return parsed_duration_reject_reason(last_end_ms, target_duration_ms) is not None


def album_match_score(target_album: str, cand_album: str) -> int:
    """同名专辑加权（v1.3.5 收敛）：去括号精确相等 60 / 互为子串 30 / 其余 0。

    原先 candidate_rank 与 _krc_cand_rank 各写一份（语义相同），合并为单一出处。
    """
    alb_c = (target_album or "").strip().lower()
    c_alb = (cand_album or "").strip().lower()
    if not alb_c or not c_alb:
        return 0
    if alb_c == c_alb or strip_subtitle_brackets(alb_c) == strip_subtitle_brackets(c_alb):
        return 60
    if alb_c in c_alb or c_alb in alb_c:
        return 30
    return 0


def krcs_version_ok(
    target_is_live: bool,
    cand_title: str,
    cand_album: str = "",
    cand_dur_sec: int = 0,
    target_dur_sec: int = 0,
    target_is_remix: Optional[bool] = None,
) -> bool:
    """KRCS 标题无 Live、专辑为空时，时长贴齐本地 Live ±10s 视为同版本。"""
    if matches_version_policy(target_is_live, cand_title or "", cand_album or "", target_is_remix=target_is_remix):
        return True
    if target_is_live and target_dur_sec > 0:
        return abs(int(cand_dur_sec) - int(target_dur_sec)) <= KRCS_METADATA_WINDOW_SEC
    return False


def _log_parsed_discard(reason: str, last_end_ms: int, target_duration_ms: int) -> None:
    last_s = last_end_ms / 1000.0
    dur_s = target_duration_ms / 1000.0
    if reason == "preview":
        print(f"   ⚠️ 酷狗丢弃：预览截断（末行 {last_s:.1f}s / 曲长 {dur_s:.1f}s）")
    elif reason == "overrun":
        print(f"   ⚠️ 酷狗丢弃：末行冲轴（末行 {last_s:.1f}s / 曲长 {dur_s:.1f}s）")
    else:
        print(f"   ⚠️ 酷狗丢弃：{reason}（末行 {last_s:.1f}s / 曲长 {dur_s:.1f}s）")
    _set_kugou_status("gated")

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
        reason = parsed_duration_reject_reason(cand_last_end, target_duration_ms)
        if reason:
            _log_parsed_discard(reason, cand_last_end, target_duration_ms)
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
        if candidates:
            _set_kugou_status("gated")
        return None
        
    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    return scored_candidates[0][1]

class KugouFetcher:
    """Fetcher for Kugou Music KRC lyrics with Live/Remix guard, album weighting, and KRCS fallback."""
    
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
                            "album_id": s.get("album_id", ""),
                            "img": s.get("img", "") or s.get("imgurl", "") or "",
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
    def search_krc_by_keyword(
        cls,
        title: str,
        artist: str = "",
        duration_sec: int = 0,
        album: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Directly queries krcs.kugou.com keyword search endpoint.
        Acts as secondary fallback when hash-based lookup yields empty candidate lists.
        Filters candidates by version policy, duration proximity, and prioritizes matching album.
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

        target_is_live = is_live_version(title)
        target_is_remix = is_remix_version(title) or is_remix_version(album)

        def _version_ok(c) -> bool:
            return krcs_version_ok(
                target_is_live,
                c.get("song", "") or "",
                c.get("album", "") or "",
                get_cand_dur_sec(c),
                duration_sec,
                target_is_remix=target_is_remix,
            )

        def _krc_cand_rank(c):
            alb_score = album_match_score(album, c.get("album", "") or "")
            c_dur = get_cand_dur_sec(c)
            diff = abs(c_dur - duration_sec) if duration_sec > 0 else 0
            return (-alb_score, diff)

        if duration_sec > 0:
            valid = []
            skipped_version = 0
            skipped_duration = 0
            for c in candidates:
                if not is_title_fuzzy_match(c.get("song", ""), title):
                    continue
                if not _version_ok(c):
                    skipped_version += 1
                    continue
                c_dur = get_cand_dur_sec(c)
                diff = abs(c_dur - duration_sec)
                # Iron Rule 17: 搜歌元数据 ±10s
                if diff <= KRCS_METADATA_WINDOW_SEC:
                    valid.append((diff, c))
                else:
                    skipped_duration += 1
            if valid:
                valid.sort(key=lambda x: _krc_cand_rank(x[1]))
                ranked_cands = [x[1] for x in valid]
            else:
                if skipped_version or skipped_duration:
                    _set_kugou_status("gated")
                    print(
                        f"   ⚠️ 酷狗丢弃：版本互斥或时长未贴齐"
                        f"（版本 {skipped_version} / 时长 {skipped_duration}，窗口 ±{KRCS_METADATA_WINDOW_SEC}s）"
                    )
                return None
        else:
            title_matching = [c for c in candidates
                              if is_title_fuzzy_match(c.get("song", ""), title) and _version_ok(c)]
            if not title_matching:
                if candidates:
                    _set_kugou_status("gated")
                    print("   ⚠️ 酷狗丢弃：版本互斥（KRCS 候选标题/专辑未过门禁）")
                return None
            title_matching.sort(key=_krc_cand_rank)
            ranked_cands = title_matching
            
        return select_best_krc_candidate(ranked_cands, cls.download_krc_by_id, target_duration_ms=duration_sec*1000)

    @classmethod
    def fetch_lyrics_by_hash(
        cls,
        song_hash: str,
        duration_ms: int = 0,
        title: str = "",
        artist: str = "",
        album: str = "",
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
            return cls.search_krc_by_keyword(title, artist, duration_sec, album=album)
            
        return None

    @classmethod
    def fetch(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Main entry point for fetching KRC lyrics.
        Enforces bidirectional strict version exclusion (Live vs Studio, Remix vs Original).
        Prioritizes candidates matching target album.
        """
        _set_kugou_status("empty")
        songs = cls.search_song(title, artist)
        target_is_live = is_live_version(title)
        target_is_remix = is_remix_version(title) or is_remix_version(album)
        
        # Apply strict version mutual exclusion
        filtered_songs = []
        for song in songs:
            s_title = song.get("title", "")
            s_album = song.get("album", "")
            if matches_version_policy(target_is_live, s_title, s_album, target_is_remix=target_is_remix):
                filtered_songs.append(song)
            elif songs:
                _set_kugou_status("gated")
            
        # If strict filter left no songs and target is live, search krcs directly
        if not filtered_songs and target_is_live:
            duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
            direct_krc = cls.search_krc_by_keyword(title, artist, duration_sec, album=album)
            if direct_krc and direct_krc.get("lines"):
                _set_kugou_status("ok")
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
                
            alb_score = album_match_score(album, song.get("album", ""))

            s_a = song.get("artist", "").strip().lower()
            a_c = artist.strip().lower()
            a_score = 20 if (a_c and (a_c in s_a or s_a in a_c)) else 0
            
            d_diff = abs(song.get("duration", 0) - duration_ms) if duration_ms > 0 else 0
            return (-t_score, -alb_score, -a_score, d_diff)
            
        # Hard gate: only retain songs whose title actually matches
        title_matched_songs = [s for s in filtered_songs if is_title_fuzzy_match(s.get("title", ""), title)]
        if title_matched_songs:
            title_matched_songs.sort(key=candidate_rank)
            
        # Try candidates in order
        for song in title_matched_songs:
            h = song.get("hash")
            s_dur = song.get("duration", 0)
            # Iron Rule 17: Pre-filter candidate song metadata duration
            if duration_ms > 0 and s_dur > 0 and abs(s_dur - duration_ms) > KRCS_METADATA_WINDOW_SEC * 1000:
                _set_kugou_status("gated")
                print(
                    f"   ⚠️ 酷狗丢弃：搜歌时长未贴齐"
                    f"（候选 {s_dur // 1000}s / 本地 {duration_ms // 1000}s，窗口 ±{KRCS_METADATA_WINDOW_SEC}s）"
                )
                continue
            if h:
                res = cls.fetch_lyrics_by_hash(h, duration_ms, title, artist, album=album)
                if res and res.get("lines"):
                    lines = res["lines"]
                    # Iron Rule 17: 解析末行只拒预览/冲轴
                    if duration_ms > 0 and lines:
                        last_t_ms = lines[-1].get("end", lines[-1].get("start", 0))
                        reason = parsed_duration_reject_reason(last_t_ms, duration_ms)
                        if reason:
                            _log_parsed_discard(reason, last_t_ms, duration_ms)
                            continue
                    _set_kugou_status("ok")
                    return res
                    
        # Final safety net: if still not found, try krcs keyword search
        if title:
            duration_sec = int(duration_ms // 1000) if duration_ms > 0 else 0
            res = cls.search_krc_by_keyword(title, artist, duration_sec, album=album)
            if res and res.get("lines"):
                _set_kugou_status("ok")
                return res

        if songs or filtered_songs:
            _set_kugou_status("gated")
            print("   ⚠️ 酷狗有搜歌命中但歌词全被门禁丢掉（预览/冲轴/版本互斥），不是「酷狗没有」")
        return None
