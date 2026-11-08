# -*- coding: utf-8 -*-
"""lrc.red 全球歌词开放同步网络客户端 (v1.8.1)。

功能与特性：
1. 优先通过 ISRC 编码实现毫秒级绝对精准直查（/s/{isrc}.song.json）；
2. 缓存按「远端原始记录」与「本地歌曲独立仲裁」彻底解耦（P0-1 杜绝缓存串歌污染与负缓存锁死）；
3. 杜绝本地数据回填远端凭证，远端缺失元数据显式标记 missing，严防自我认证（P0-2）；
4. 统一直查与搜索候选的版本互斥与时长门禁，严防 Live/Remix/Medley 串歌（P0-3）；
5. 明确区分 byModel 模型生成、确证官方与 unknown 状态，严控交付与写回资格（P0-4, P0-5）；
6. 统一降级与网络总预算控制，支持公开 .json / .ttml 端点兜底与体积防护（P1-2, P1-3）；
7. 速率合规：最小请求间隔 1.0s，合规携带 User-Agent。
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
from ..utils import http
http_get_text_ex = http.http_get_text_ex
_orig_http_get_text_ex = http_get_text_ex


def _get_http_func():
    if http_get_text_ex is not _orig_http_get_text_ex:
        return http_get_text_ex
    return http.http_get_text_ex
from ..utils.isrc import normalize_isrc, is_valid_isrc
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
UA_LRC_RED = f"AntigravityLyricsTool/{__version__} (contact: sxhxu@qq.com)"

REQ_TIMEOUT_S = 3.0
SONG_BUDGET_S = 5.0
MAX_JSON_BYTES = 1024 * 1024        # 1 MiB
MAX_TTML_BYTES = 2 * 1024 * 1024    # 2 MiB
MIN_REQ_INTERVAL_S = 1.0            # 保守速率限制 1 req/s


class LrcRedFetcher:
    """lrc.red 歌词抓取与候选仲裁服务类。"""

    _last_req_time: float = 0.0
    _raw_cache: Dict[str, Dict[str, Any]] = {}
    _cache = _raw_cache  # 兼容旧代码与测试调用 _cache.clear()
    MAX_CACHE_ENTRIES: int = 500

    @classmethod
    def _rate_limit(cls, deadline: Optional[float] = None) -> bool:
        """限制请求速率不高于 1 req/s，且尊重全局 deadline。若剩余时间不足则返回 False。"""
        now = time.perf_counter()
        if deadline is not None and now >= deadline:
            return False
        elapsed = now - cls._last_req_time
        if elapsed < MIN_REQ_INTERVAL_S:
            wait = MIN_REQ_INTERVAL_S - elapsed
            if deadline is not None and (now + wait >= deadline):
                return False
            time.sleep(wait)
        cls._last_req_time = time.perf_counter()
        return True

    @classmethod
    def _http_get_json(
        cls,
        url: str,
        timeout: float = REQ_TIMEOUT_S,
        deadline: Optional[float] = None
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """请求 JSON 数据，做体积上限与状态码拦截。"""
        if not cls._rate_limit(deadline):
            return 0, None
        rem_timeout = timeout
        if deadline is not None:
            rem = deadline - time.perf_counter()
            if rem <= 0.1:
                return 0, None
            rem_timeout = min(timeout, rem)

        res = _get_http_func()(
            url,
            headers={"Accept": "application/json"},
            timeout=rem_timeout,
            user_agent=UA_LRC_RED,
            max_bytes=MAX_JSON_BYTES,
        )
        if res.status != 200 or not res.text:
            return res.status, None
        try:
            data = json.loads(res.text)
            return 200, data if isinstance(data, dict) else None
        except Exception:
            return res.status, None

    @classmethod
    def _http_get_text(
        cls,
        url: str,
        timeout: float = REQ_TIMEOUT_S,
        deadline: Optional[float] = None
    ) -> Tuple[int, Optional[str]]:
        """请求文本/TTML 数据，做体积上限拦截。"""
        if not cls._rate_limit(deadline):
            return 0, None
        rem_timeout = timeout
        if deadline is not None:
            rem = deadline - time.perf_counter()
            if rem <= 0.1:
                return 0, None
            rem_timeout = min(timeout, rem)

        res = _get_http_func()(
            url,
            headers={"Accept": "application/xml, text/xml, */*"},
            timeout=rem_timeout,
            user_agent=UA_LRC_RED,
            max_bytes=MAX_TTML_BYTES,
        )
        if res.status != 200 or not res.text:
            return res.status, None
        return 200, res.text

    @classmethod
    def _fetch_raw_record(
        cls,
        clean_isrc: str,
        deadline: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """获取并缓存远端原始响应记录（不绑定具体本地歌曲上下文，P0-1）。"""
        if clean_isrc in cls._raw_cache:
            return cls._raw_cache[clean_isrc]

        if deadline is not None and time.perf_counter() >= deadline:
            return None

        # 1. 尝试 /s/{isrc}.song.json
        song_url = f"{LRC_RED_BASE}/s/{clean_isrc}.song.json"
        status, data = cls._http_get_json(song_url, deadline=deadline)

        raw_ttml = ""
        meta: Dict[str, Any] = {}
        endpoint = song_url
        endpoint_kind = "song.json"

        if status == 200 and data and data.get("source"):
            raw_ttml = data["source"]
            meta = {
                "title": data.get("title") or "",
                "artist": data.get("artist") or "",
                "album": data.get("album") or "",
                "duration": data.get("duration") or 0,
                "songwriters": data.get("songwriters") or [],
                "isrc": clean_isrc,
                "by_model": data.get("byModel"),  # None / True / False
                "sync": data.get("sync") or "",
            }
        elif status == 429:
            # 遭遇 429 退避，不追加请求施压
            logger.warning("   ⚠️ [lrc-red] 遭遇 429 限流: %s", clean_isrc)
            return None
        else:
            # P1-2: 降级回落请求 /s/{isrc}.ttml 与 /s/{isrc}.json
            if deadline is not None and (deadline - time.perf_counter()) < 0.2:
                return None

            meta_url = f"{LRC_RED_BASE}/s/{clean_isrc}.json"
            m_status, m_data = cls._http_get_json(meta_url, deadline=deadline)
            if m_status == 200 and m_data:
                meta = {
                    "title": m_data.get("title") or "",
                    "artist": m_data.get("artist") or "",
                    "album": m_data.get("album") or "",
                    "duration": m_data.get("duration") or 0,
                    "songwriters": m_data.get("songwriters") or [],
                    "isrc": clean_isrc,
                    "by_model": m_data.get("byModel"),
                    "sync": m_data.get("sync") or "",
                }
            ttml_url = f"{LRC_RED_BASE}/s/{clean_isrc}.ttml"
            t_status, t_text = cls._http_get_text(ttml_url, deadline=deadline)
            if t_status == 200 and t_text:
                raw_ttml = t_text
                endpoint = ttml_url
                endpoint_kind = "ttml"

        if not raw_ttml:
            # 绝不写入永久负缓存！保证后续正常请求不被锁死（P0-1）
            return None

        if len(cls._raw_cache) >= cls.MAX_CACHE_ENTRIES:
            try:
                cls._raw_cache.pop(next(iter(cls._raw_cache)))
            except Exception:
                pass

        record = {
            "meta": meta,
            "raw_ttml": raw_ttml,
            "endpoint": endpoint,
            "endpoint_kind": endpoint_kind,
            "retrieved_at": time.time(),
        }
        cls._raw_cache[clean_isrc] = record
        return record

    @classmethod
    def _validate_and_build_candidate(
        cls,
        raw_record: Dict[str, Any],
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
        album: str = "",
        match_origin: str = "embedded",
    ) -> Optional[Dict[str, Any]]:
        """基于当前本地歌曲上下文，对远端原始记录执行独立的身份、版本与质量仲裁。"""
        meta = raw_record["meta"]
        raw_ttml = raw_record["raw_ttml"]
        clean_isrc = meta.get("isrc") or ""

        r_title = meta.get("title") or ""
        r_artist = meta.get("artist") or ""
        r_album = meta.get("album") or ""
        by_model_raw = meta.get("by_model")

        # 1. 远端音轨时长门禁（±5000ms）
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
                    "   ⚠️ [lrc-red] 音轨时长不对齐（本地 %dms vs 远端 %dms，差 %dms > 5000ms），丢弃候选: %s",
                    duration_ms, remote_dur_ms, dur_diff, clean_isrc
                )
                return None

        # 2. 标题门禁与版本互斥门禁（P0-3 统一规则）
        local_norm_t_loose = norm_title(title, keep_version=False) if title else ""
        local_norm_t_strict = norm_title(title, keep_version=True) if title else ""
        remote_norm_t_loose = norm_title(r_title, keep_version=False) if r_title else ""
        remote_norm_t_strict = norm_title(r_title, keep_version=True) if r_title else ""

        if local_norm_t_loose and remote_norm_t_loose and local_norm_t_loose != remote_norm_t_loose:
            logger.warning(
                "   ⚠️ [lrc-red] 标题不匹配 (%s vs %s)，丢弃候选",
                local_norm_t_loose, remote_norm_t_loose
            )
            return None

        if title and r_title:
            local_ver = version_tokens(title)
            remote_ver = version_tokens(r_title)
            ver_diff = (local_ver - remote_ver) | (remote_ver - local_ver)
            if ver_diff and any(k in ver_diff for k in VERSION_EXCLUSION_TOKENS):
                logger.warning(
                    "   ⚠️ [lrc-red] 版本互斥冲突 (%s vs %s)，丢弃候选",
                    local_ver, remote_ver
                )
                return None

        # 3. 艺人门禁（P0-3）
        if artist and r_artist:
            q_art = artist_tokens(artist)
            r_art = artist_tokens(r_artist)
            if q_art and r_art and not (q_art & r_art):
                logger.warning("   ⚠️ [lrc-red] 艺人无交集 (%s vs %s)，丢弃候选", q_art, r_art)
                return None

        # 4. 解析 TTML 与逐字质量门禁（P1-4, P1-5, P1-6）
        norm_ttml = normalize_lrc_red_namespaces(raw_ttml)
        parsed = parse_lrc_red_ttml(norm_ttml, metadata=meta)
        lines = parsed.get("lines") or []
        if not lines or not parsed.get("has_verbatim"):
            logger.warning("   ⚠️ [lrc-red] 无有效逐字行，丢弃候选: %s", clean_isrc)
            return None

        # 逐字行覆盖度与时间戳有效性审计（P1-6）
        cov_ratio = parsed.get("word_coverage_ratio", 0.0)
        invalid_lines = parsed.get("invalid_word_lines", 0)
        singing_lines = parsed.get("singing_lines", 0)
        if singing_lines > 0 and (cov_ratio < 0.95 or invalid_lines > 0):
            logger.warning(
                "   ⚠️ [lrc-red] 逐字行质量不达标 (覆盖率=%.2f < 0.95, 无效行=%d)，丢弃候选: %s",
                cov_ratio, invalid_lines, clean_isrc
            )
            return None

        # 5. byModel 状态约束（P0-4）
        if by_model_raw is True:
            logger.warning("   ⚠️ [lrc-red] byModel=True 为模型/AI生成，不作为原生逐字候选交付")
            return None

        by_model_state = "false" if by_model_raw is False else "unknown"

        # 6. 置信度判定（P0-2: 严禁自我认证）
        has_full_identity = bool(r_title and r_artist)
        title_strict_match = bool(local_norm_t_strict and remote_norm_t_strict and local_norm_t_strict == remote_norm_t_strict)

        # 远端若缺失标题/艺人关键凭证，绝不判定 strict
        if not has_full_identity:
            confidence = "insufficient"
            logger.info("   ℹ️ [lrc-red] 远端元数据缺失标题/歌手，标记 insufficient_evidence")
            if title or artist:
                # 调用方提供了标题或歌手进行查询，但远端缺失对应信息，不能自我认证通过
                return None
        elif title_strict_match:
            confidence = "strict"
        else:
            confidence = "loose"

        # 7. ISRC 回写资格判定（P0-5 封闭门禁）
        dur_diff_ok = (
            duration_ms > 0
            and remote_dur_ms > 0
            and abs(duration_ms - remote_dur_ms) <= 3000
        )
        isrc_write_eligible = (
            confidence == "strict"
            and by_model_raw is False
            and dur_diff_ok
            and title_strict_match
            and has_full_identity
            and is_valid_isrc(clean_isrc)
        )

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
            "isrc_write_eligible": isrc_write_eligible,
            "match": {
                "confidence": confidence,
                "isrc_origin": match_origin,
                "by_model": by_model_state,
                "title_strict": title_strict_match,
                "word_coverage_ratio": cov_ratio,
            },
            "provenance": {
                "provider": "lrc.red",
                "by_model": by_model_state,
                "origin_status": "official" if by_model_raw is False else "unverified_origin",
                "endpoint": raw_record["endpoint"],
                "endpoint_kind": raw_record["endpoint_kind"],
            },
        }
        return candidate

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

        raw_record = cls._fetch_raw_record(clean_isrc, deadline=deadline)
        if not raw_record:
            return None

        # 每次本地查询独立执行当前歌曲的仲裁校验（P0-1）
        return cls._validate_and_build_candidate(
            raw_record=raw_record,
            title=title,
            artist=artist,
            duration_ms=duration_ms,
            album=album,
            match_origin=match_origin,
        )

    @classmethod
    def _rank_and_filter_hits(
        cls,
        hits: List[Dict[str, Any]],
        title: str,
        artist: str,
        duration_ms: int,
        album: str = "",
    ) -> List[Dict[str, Any]]:
        """对 /match.json 或 /search.json 返回的候选集进行安全排序与严格过滤。"""
        valid_candidates = []
        local_norm_t_loose = norm_title(title, keep_version=False) if title else ""
        local_norm_t_strict = norm_title(title, keep_version=True) if title else ""
        local_art = artist_tokens(artist) if artist else set()
        local_ver = version_tokens(title) if title else set()
        local_al = norm_album(album) if album else ""

        for h in hits:
            cand_isrc = normalize_isrc(h.get("isrc"))
            if not cand_isrc:
                continue

            cand_title = h.get("title") or ""
            cand_artist = h.get("artist") or ""
            try:
                cand_dur_s = float(h.get("duration") or 0)
                cand_dur_ms = int(cand_dur_s * 1000)
            except Exception:
                cand_dur_ms = 0

            # 时长门禁：±5000ms
            if duration_ms > 0 and cand_dur_ms > 0:
                dur_diff = abs(duration_ms - cand_dur_ms)
                if dur_diff > 5000:
                    continue
            else:
                dur_diff = 0

            # 标题门禁
            cand_t_loose = norm_title(cand_title, keep_version=False) if cand_title else ""
            if local_norm_t_loose and cand_t_loose and local_norm_t_loose != cand_t_loose:
                continue

            # 版本互斥门禁 (P0-3)
            cand_ver = version_tokens(cand_title) if cand_title else set()
            ver_diff = (local_ver - cand_ver) | (cand_ver - local_ver)
            if ver_diff and any(k in ver_diff for k in VERSION_EXCLUSION_TOKENS):
                continue

            # 艺人门禁
            cand_art = artist_tokens(cand_artist) if cand_artist else set()
            if local_art and cand_art and not (local_art & cand_art):
                continue

            # 综合评分：时长差越小越好，严格标题匹配优先，专辑匹配加分
            cand_t_strict = norm_title(cand_title, keep_version=True) if cand_title else ""
            score = 0
            if cand_t_strict and local_norm_t_strict and cand_t_strict == local_norm_t_strict:
                score += 100
            if local_al and h.get("album"):
                cand_al = norm_album(h.get("album"))
                if cand_al and cand_al == local_al:
                    score += 20
            score -= dur_diff // 100

            valid_candidates.append((score, h))

        valid_candidates.sort(key=lambda x: x[0], reverse=True)

        # M06: 多个等分且无法消歧候选，不强制选一首
        if len(valid_candidates) >= 2:
            top_score = valid_candidates[0][0]
            top_isrc = normalize_isrc(valid_candidates[0][1].get("isrc"))
            conflicting = [
                item[1] for item in valid_candidates
                if item[0] == top_score and normalize_isrc(item[1].get("isrc")) != top_isrc
            ]
            if conflicting:
                logger.warning(
                    "   ⚠️ [lrc-red] 存在等分且无法消歧的候选 (%s vs %s)，放弃选择以策安全 (M06)",
                    top_isrc, normalize_isrc(conflicting[0].get("isrc"))
                )
                return []

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
        status, data = cls._http_get_json(url, deadline=deadline)
        if status != 200 or not data or not isinstance(data.get("hits"), list):
            return None

        best_hits = cls._rank_and_filter_hits(data["hits"], title, artist, duration_ms, album=album)
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

        status, data = cls._http_get_json(url, deadline=deadline)
        if status != 200 or not data or not isinstance(data.get("hits"), list):
            return None

        # 先验证并排序全部返回候选，再尝试前 3 名（P0-3）
        best_hits = cls._rank_and_filter_hits(data["hits"], title, artist, duration_ms, album=album)
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
        deadline: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """lrc.red 统一调度入口。
        
        梯级顺序：
        1. 已有 ISRC 直查；
        2. /match.json 结构化匹配；
        3. /search.json 文本搜索。
        """
        if os.environ.get("MTL_LRC_RED") == "off":
            return None

        if deadline is None:
            start_time = time.perf_counter()
            deadline = start_time + SONG_BUDGET_S

        # 梯级 1：若有合法 ISRC，优先直查
        clean_isrc = normalize_isrc(isrc)
        if clean_isrc and time.perf_counter() < deadline:
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
