# -*- coding: utf-8 -*-
"""lrc.red 全球歌词开放同步网络客户端 (v1.8.0)。

功能与特性：
1. 优先通过 ISRC 编码实现毫秒级绝对精准直查（/s/{isrc}.song.json）；
2. 支持 /match.json 结构化元数据匹配与 /search.json 文本模糊搜索兜底；
3. 严格候选门禁：结合音轨时长差（±5s 窗口）、标题与歌手交集及版本互斥，杜绝翻唱与串烧串歌；
4. 严格超时与流量预算：单次 3.0s 超时、单曲 5.0s 总预算、响应体积限制（JSON 1MB，TTML 2MB）；
5. 速率合规：最小请求间隔 1.0s，合规携带 User-Agent。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from ..parsers.lrc_red_ttml import parse_lrc_red_ttml, normalize_lrc_red_namespaces
from ..utils.http import http_get_text_ex
from ..utils.isrc import normalize_isrc
from ..utils.text_match import (
    artist_tokens,
    norm_title,
    norm_album,
    version_tokens,
    VERSION_EXCLUSION_TOKENS,
)
from ..version import __version__

logger = logging.getLogger(__name__)

LRC_RED_BASE = "https://lrc.red"
UA_LRC_RED = f"AntigravityLyricsTool/{__version__} (+https://github.com/sxsxhhi/music-tag-lyrics)"

REQ_TIMEOUT_S = 3.0
SONG_BUDGET_S = 5.0
MAX_JSON_BYTES = 1024 * 1024        # 1 MiB
MAX_TTML_BYTES = 2 * 1024 * 1024    # 2 MiB
MIN_REQ_INTERVAL_S = 1.0            # 保守速率限制 1 req/s


class LrcRedFetcher:
    """lrc.red 歌词抓取与候选仲裁服务类。"""

    _last_req_time: float = 0.0
    _cache: Dict[str, Optional[Dict[str, Any]]] = {}

    @classmethod
    def _rate_limit(cls) -> None:
        """限制请求速率不高于 1 req/s。"""
        now = time.perf_counter()
        elapsed = now - cls._last_req_time
        if elapsed < MIN_REQ_INTERVAL_S:
            time.sleep(MIN_REQ_INTERVAL_S - elapsed)
        cls._last_req_time = time.perf_counter()

    @classmethod
    def _http_get_json(cls, url: str, timeout: float = REQ_TIMEOUT_S) -> Tuple[int, Optional[Dict[str, Any]]]:
        """请求 JSON 数据，做体积上限与状态码拦截。"""
        cls._rate_limit()
        res = http_get_text_ex(
            url,
            headers={"Accept": "application/json"},
            timeout=timeout,
            user_agent=UA_LRC_RED,
        )
        if res.status != 200 or not res.text:
            return res.status, None
        if res.bytes_len > MAX_JSON_BYTES:
            logger.warning("   ⚠️ [lrc-red] 响应超限 (%d > %d bytes): %s", res.bytes_len, MAX_JSON_BYTES, url)
            return res.status, None
        try:
            data = json.loads(res.text)
            return 200, data if isinstance(data, dict) else None
        except Exception:
            return res.status, None

    @classmethod
    def _http_get_text(cls, url: str, timeout: float = REQ_TIMEOUT_S) -> Tuple[int, Optional[str]]:
        """请求文本/TTML 数据，做体积上限拦截。"""
        cls._rate_limit()
        res = http_get_text_ex(
            url,
            headers={"Accept": "application/xml, text/xml, */*"},
            timeout=timeout,
            user_agent=UA_LRC_RED,
        )
        if res.status != 200 or not res.text:
            return res.status, None
        if res.bytes_len > MAX_TTML_BYTES:
            logger.warning("   ⚠️ [lrc-red] TTML 超限 (%d > %d bytes): %s", res.bytes_len, MAX_TTML_BYTES, url)
            return res.status, None
        return 200, res.text

    @classmethod
    def fetch_by_isrc(
        cls,
        isrc: str,
        duration_ms: int = 0,
        title: str = "",
        artist: str = "",
        album: str = "",
        match_origin: str = "embedded",
        deadline: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """基于 ISRC 编码直查歌词。"""
        clean_isrc = normalize_isrc(isrc)
        if not clean_isrc:
            return None

        cache_key = f"isrc:{clean_isrc}"
        if cache_key in cls._cache:
            return cls._cache[cache_key]

        rem_timeout = REQ_TIMEOUT_S
        if deadline:
            now = time.perf_counter()
            if now >= deadline:
                return None
            rem_timeout = min(REQ_TIMEOUT_S, max(0.5, deadline - now))

        # 1. 请求 /s/{isrc}.song.json
        url = f"{LRC_RED_BASE}/s/{clean_isrc}.song.json"
        status, data = cls._http_get_json(url, timeout=rem_timeout)

        raw_ttml = ""
        meta: Dict[str, Any] = {}
        if status == 200 and data:
            raw_ttml = data.get("source") or ""
            meta = {
                "title": data.get("title") or title,
                "artist": data.get("artist") or artist,
                "album": data.get("album") or album,
                "duration": data.get("duration") or 0,
                "songwriters": data.get("songwriters") or [],
                "isrc": clean_isrc,
                "by_model": data.get("byModel") or False,
                "sync": data.get("sync") or "",
            }
        elif status == 404:
            cls._cache[cache_key] = None
            return None
        else:
            # 尝试回落请求 /s/{isrc}.ttml 与 /s/{isrc}.json
            meta_url = f"{LRC_RED_BASE}/s/{clean_isrc}.json"
            m_status, m_data = cls._http_get_json(meta_url, timeout=rem_timeout)
            if m_status == 200 and m_data:
                meta = {
                    "title": m_data.get("title") or title,
                    "artist": m_data.get("artist") or artist,
                    "album": m_data.get("album") or album,
                    "duration": m_data.get("duration") or 0,
                    "songwriters": m_data.get("songwriters") or [],
                    "isrc": clean_isrc,
                    "by_model": m_data.get("byModel") or False,
                    "sync": m_data.get("sync") or "",
                }
            ttml_url = f"{LRC_RED_BASE}/s/{clean_isrc}.ttml"
            t_status, t_text = cls._http_get_text(ttml_url, timeout=rem_timeout)
            if t_status == 200 and t_text:
                raw_ttml = t_text

        if not raw_ttml:
            cls._cache[cache_key] = None
            return None

        # 2. 时长门禁校验
        remote_dur_ms = 0
        if meta.get("duration"):
            try:
                remote_dur_ms = int(float(meta["duration"]) * 1000)
            except Exception:
                pass

        if duration_ms > 0 and remote_dur_ms > 0:
            dur_diff = abs(duration_ms - remote_dur_ms)
            if dur_diff > 5000:
                logger.warning(
                    "   ⚠️ [lrc-red] 时长不对齐（差 %dms > 5000ms），丢弃候选: %s",
                    dur_diff, clean_isrc
                )
                cls._cache[cache_key] = None
                return None

        # 3. 标题与歌手基础门禁（若调用方提供了信息）
        if title:
            local_norm_t = norm_title(title, keep_version=False)
            remote_norm_t = norm_title(meta.get("title") or "", keep_version=False)
            if local_norm_t and remote_norm_t and local_norm_t != remote_norm_t:
                logger.warning(
                    "   ⚠️ [lrc-red] 标题不匹配 (%s vs %s)，丢弃候选",
                    local_norm_t, remote_norm_t
                )
                cls._cache[cache_key] = None
                return None

        if artist and meta.get("artist"):
            q_art = artist_tokens(artist)
            r_art = artist_tokens(meta["artist"])
            if q_art and r_art and not (q_art & r_art):
                logger.warning("   ⚠️ [lrc-red] 艺人无交集 (%s vs %s)，丢弃候选", q_art, r_art)
                cls._cache[cache_key] = None
                return None

        # 4. 解析 TTML 并规范化命名空间
        norm_ttml = normalize_lrc_red_namespaces(raw_ttml)
        parsed = parse_lrc_red_ttml(norm_ttml, metadata=meta)
        lines = parsed.get("lines") or []
        if not lines or not parsed.get("has_verbatim"):
            logger.warning("   ⚠️ [lrc-red] 无有效逐字行，丢弃候选: %s", clean_isrc)
            cls._cache[cache_key] = None
            return None

        # 5. 组装标准交付候选字典
        by_model = bool(meta.get("by_model"))
        candidate: Dict[str, Any] = {
            "source": "lrc-red",
            "lyric_type": "word",
            "time_precision": 3,
            "isrc": clean_isrc,
            "lines": lines,
            "tags": parsed.get("tags") or {},
            "songwriters": parsed.get("songwriters") or [],
            "duration_ms": parsed.get("duration_ms") or remote_dur_ms,
            "has_translation": parsed.get("has_translation", False),
            "match": {
                "confidence": "strict",
                "isrc_origin": match_origin,
                "by_model": by_model,
                "title_strict": True,
            },
            "provenance": {
                "provider": "lrc.red",
                "by_model": by_model,
                "endpoint": url,
            },
        }

        cls._cache[cache_key] = candidate
        return candidate

    @classmethod
    def _rank_and_filter_hits(
        cls,
        hits: List[Dict[str, Any]],
        title: str,
        artist: str,
        duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """对 /match.json 或 /search.json 返回的候选集进行安全排序与严格过滤。"""
        valid_candidates = []
        local_norm_t_loose = norm_title(title, keep_version=False)
        local_norm_t_strict = norm_title(title, keep_version=True)
        local_art = artist_tokens(artist)
        local_ver = version_tokens(title)

        for h in hits:
            cand_isrc = normalize_isrc(h.get("isrc"))
            if not cand_isrc:
                continue

            cand_title = h.get("title") or ""
            cand_artist = h.get("artist") or ""
            cand_dur_s = float(h.get("duration") or 0)
            cand_dur_ms = int(cand_dur_s * 1000)

            # 时长门禁：±5000ms
            dur_diff = abs(duration_ms - cand_dur_ms) if (duration_ms > 0 and cand_dur_ms > 0) else 0
            if duration_ms > 0 and cand_dur_ms > 0 and dur_diff > 5000:
                continue

            # 标题门禁
            cand_t_loose = norm_title(cand_title, keep_version=False)
            if local_norm_t_loose and cand_t_loose and local_norm_t_loose != cand_t_loose:
                continue

            # 艺人门禁
            cand_art = artist_tokens(cand_artist)
            if local_art and cand_art and not (local_art & cand_art):
                continue

            # 版本互斥门禁
            cand_ver = version_tokens(cand_title)
            ver_diff = (local_ver - cand_ver) | (cand_ver - local_ver)
            if ver_diff and any(k in ver_diff for k in VERSION_EXCLUSION_TOKENS):
                continue

            # 综合评分：时长差越小越好，严格标题匹配优先
            cand_t_strict = norm_title(cand_title, keep_version=True)
            score = 0
            if cand_t_strict == local_norm_t_strict:
                score += 100
            score -= dur_diff // 100

            valid_candidates.append((score, h))

        valid_candidates.sort(key=lambda x: x[0], reverse=True)
        return [item[1] for item in valid_candidates]

    @classmethod
    def fetch_by_match(
        cls,
        title: str,
        artist: str,
        duration_ms: int = 0,
        album: str = "",
        deadline: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """通过 /match.json 结构化匹配接口检索。"""
        if not title:
            return None
        q_params = {"track": title}
        if artist:
            q_params["artist"] = artist
        if album:
            q_params["album"] = album
        if duration_ms > 0:
            q_params["duration"] = str(duration_ms // 1000)

        url = f"{LRC_RED_BASE}/match.json?{urllib.parse.urlencode(q_params)}"
        rem_timeout = REQ_TIMEOUT_S
        if deadline:
            now = time.perf_counter()
            if now >= deadline:
                return None
            rem_timeout = min(REQ_TIMEOUT_S, max(0.5, deadline - now))

        status, data = cls._http_get_json(url, timeout=rem_timeout)
        if status != 200 or not data or not isinstance(data.get("hits"), list):
            return None

        best_hits = cls._rank_and_filter_hits(data["hits"], title, artist, duration_ms)
        for cand in best_hits[:3]:
            isrc = cand.get("isrc")
            res = cls.fetch_by_isrc(
                isrc=isrc,
                duration_ms=duration_ms,
                title=title,
                artist=artist,
                album=album,
                match_origin="match",
                deadline=deadline,
            )
            if res:
                return res
        return None

    @classmethod
    def fetch_by_search(
        cls,
        title: str,
        artist: str,
        duration_ms: int = 0,
        album: str = "",
        deadline: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """通过 /search.json 文本模糊搜索接口检索。"""
        if not title:
            return None
        query = f"{title} {artist}".strip()
        url = f"{LRC_RED_BASE}/search.json?q={urllib.parse.quote(query)}"

        rem_timeout = REQ_TIMEOUT_S
        if deadline:
            now = time.perf_counter()
            if now >= deadline:
                return None
            rem_timeout = min(REQ_TIMEOUT_S, max(0.5, deadline - now))

        status, data = cls._http_get_json(url, timeout=rem_timeout)
        if status != 200 or not data or not isinstance(data.get("hits"), list):
            return None

        best_hits = cls._rank_and_filter_hits(data["hits"][:5], title, artist, duration_ms)
        for cand in best_hits[:3]:
            isrc = cand.get("isrc")
            res = cls.fetch_by_isrc(
                isrc=isrc,
                duration_ms=duration_ms,
                title=title,
                artist=artist,
                album=album,
                match_origin="search",
                deadline=deadline,
            )
            if res:
                return res
        return None

    @classmethod
    def fetch(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = "",
        isrc: str = "",
    ) -> Optional[Dict[str, Any]]:
        """lrc.red 统一调度入口。
        
        梯级顺序：
        1. 已有 ISRC 直查；
        2. /match.json 结构化匹配；
        3. /search.json 文本搜索。
        """
        if os.environ.get("MTL_LRC_RED") == "off":
            return None

        start_time = time.perf_counter()
        deadline = start_time + SONG_BUDGET_S

        # 梯级 1：若有合法 ISRC，优先直查
        clean_isrc = normalize_isrc(isrc)
        if clean_isrc:
            cand = cls.fetch_by_isrc(
                isrc=clean_isrc,
                duration_ms=duration_ms,
                title=title,
                artist=artist,
                album=album,
                match_origin="embedded",
                deadline=deadline,
            )
            if cand:
                return cand

        # 梯级 2：结构化元数据匹配
        if time.perf_counter() < deadline:
            cand = cls.fetch_by_match(
                title=title,
                artist=artist,
                duration_ms=duration_ms,
                album=album,
                deadline=deadline,
            )
            if cand:
                return cand

        # 梯级 3：文本模糊搜索
        if time.perf_counter() < deadline:
            cand = cls.fetch_by_search(
                title=title,
                artist=artist,
                duration_ms=duration_ms,
                album=album,
                deadline=deadline,
            )
            if cand:
                return cand

        return None
