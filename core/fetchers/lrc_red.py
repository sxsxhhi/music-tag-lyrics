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
    GENERIC_ARTISTS,
)
from ..version import __version__

logger = logging.getLogger(__name__)

LRC_RED_BASE = "https://lrc.red"
LRC_RED_ATTRIBUTION = "https://lrc.red"
# H18：lrc.red 官方 API 条款（https://lrc.red/api，2026-11-09 核验）明确要求
#   1) User-Agent 必须写明站点/应用名，并附**链接与可用于联系的邮箱**；
#   2) 展示数据时必须清晰署名并回链 lrc.red。
# 公开源码不硬编码任何私人邮箱：联系方式由维护者通过 MTL_LRC_RED_CONTACT 注入。
LRC_RED_CONTACT_ENV = "MTL_LRC_RED_CONTACT"
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _lrc_red_contact() -> tuple[bool, str]:
    """校验并返回 (是否为合规联系邮箱, 邮箱地址)。

    根据 lrc.red 条款要求，User-Agent 必须包含有效联系邮箱。
    若未设置或格式非法，返回 (False, "")。
    """
    val = os.environ.get(LRC_RED_CONTACT_ENV, "").strip()
    if val and _EMAIL_RE.match(val):
        return True, val
    return False, ""


def build_lrc_red_ua() -> str:
    """按条款构造 User-Agent（应用名 + 链接 + 联系邮箱）。"""
    valid, contact = _lrc_red_contact()
    contact_str = contact if valid else "unconfigured"
    return (
        f"music-tag-lyrics/{__version__} "
        f"(+https://github.com/sxsxhhi/music-tag-lyrics; contact: {contact_str})"
    )


UA_LRC_RED = build_lrc_red_ua()  # 兼容旧外部引用；实际请求实时构造，读取最新配置


REQ_TIMEOUT_S = 3.0
SONG_BUDGET_S = 5.0
MAX_JSON_BYTES = 1024 * 1024        # 1 MiB
MAX_TTML_BYTES = 2 * 1024 * 1024    # 2 MiB
MIN_REQ_INTERVAL_S = 1.0            # 保守速率限制 1 req/s
# H06：候选时长未知时的固定名次折损，避免「缺字段」被当成 `dur_diff == 0` 的精确匹配而占优
UNKNOWN_DUR_PENALTY = 10


class LrcRedFetcher:
    """lrc.red 歌词抓取与候选仲裁服务类。"""

    _last_req_time: float = 0.0
    _raw_cache: Dict[str, Dict[str, Any]] = {}
    _cache = _raw_cache  # 兼容旧代码与测试调用 _cache.clear()
    MAX_CACHE_ENTRIES: int = 500
    MAX_CACHE_BYTES: int = 32 * 1024 * 1024  # 32 MiB 内存预算 (R13)
    _cache_bytes: int = 0
    _cooldown_until: float = 0.0             # 429 共享退避冷却 (R12)
    _consecutive_429: int = 0
    COOLDOWN_SEC: float = 60.0

    @classmethod
    def clear_cache(cls) -> None:
        """重置缓存与冷却状态。"""
        cls._raw_cache.clear()
        cls._cache_bytes = 0
        cls._cooldown_until = 0.0
        cls._consecutive_429 = 0

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
    def _cooldown_remaining(cls) -> float:
        """当前 429 冷却剩余秒数（0 表示未在冷却中）。"""
        return max(0.0, cls._cooldown_until - time.perf_counter())

    @classmethod
    def _http_get_json(
        cls,
        url: str,
        timeout: float = REQ_TIMEOUT_S,
        deadline: Optional[float] = None
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """请求 JSON 数据，做体积上限与状态码拦截。"""
        # H05：冷却检查下沉到共同请求入口，任何调用方（ISRC/match/search/备用端点）
        # 都无法绕过；返回 0 表示「本次未发出请求」，与真实 HTTP 状态严格区分。
        valid_email, _ = _lrc_red_contact()
        if not valid_email:
            logger.warning("   ⚠️ [lrc-red] 未配置合规联系邮箱（MTL_LRC_RED_CONTACT 须为有效邮箱），跳过该源")
            return 0, None
        if cls._cooldown_remaining() > 0:
            logger.debug("   [lrc-red] 429 冷却中，跳过 JSON 请求")
            return 0, None
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
            user_agent=build_lrc_red_ua(),
            max_bytes=MAX_JSON_BYTES,
        )
        if res.status == 429:
            cls._handle_429(res)
            return 429, None
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
        # H05：与 _http_get_json 共用同一冷却判定，杜绝备用端点绕过
        valid_email, _ = _lrc_red_contact()
        if not valid_email:
            logger.warning("   ⚠️ [lrc-red] 未配置合规联系邮箱（MTL_LRC_RED_CONTACT 须为有效邮箱），跳过该源")
            return 0, None
        if cls._cooldown_remaining() > 0:
            logger.debug("   [lrc-red] 429 冷却中，跳过文本请求")
            return 0, None
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
            user_agent=build_lrc_red_ua(),
            max_bytes=MAX_TTML_BYTES,
        )
        if res.status == 429:
            cls._handle_429(res)
            return 429, None
        if res.status != 200 or not res.text:
            return res.status, None
        return 200, res.text

    @classmethod
    def _handle_429(cls, res: Optional[Any] = None) -> None:
        """激活 429 冷却退避，支持解析 Retry-After 响应头（上限 300s，R12）。"""
        cls._consecutive_429 += 1
        n = cls._consecutive_429
        backoff = cls.COOLDOWN_SEC * (2 ** (n - 1))
        if res is not None and hasattr(res, "headers") and res.headers:
            val = res.headers.get("retry-after") or res.headers.get("Retry-After")
            if val:
                try:
                    retry_val = float(val)
                    if retry_val > 0:
                        backoff = max(backoff, retry_val)
                except (ValueError, TypeError):
                    pass
        backoff_sec = min(300.0, backoff)
        cls._cooldown_until = time.perf_counter() + backoff_sec
        logger.warning(
            "   ⚠️ [lrc-red] 遭遇 429 限流（第 %d 次），激活 %.0fs 退避冷却",
            n, backoff_sec,
        )

    @classmethod
    def _fetch_raw_record(
        cls,
        clean_isrc: str,
        deadline: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """获取并缓存远端原始响应记录（不绑定具体本地歌曲上下文，P0-1）。"""
        # 0. 内存缓存命中：移到末尾以实现真 LRU 访问淘汰 (R13)
        if clean_isrc in cls._raw_cache:
            record = cls._raw_cache.pop(clean_isrc)
            cls._raw_cache[clean_isrc] = record
            return record

        now = time.perf_counter()
        cooldown_left = cls._cooldown_remaining()
        if cooldown_left > 0:
            rem = int(cooldown_left)
            logger.warning("   ⚠️ [lrc-red] 处于 429 冷却中（剩余 %ds），跳过请求: %s", rem, clean_isrc)
            return None

        if deadline is not None and now >= deadline:
            return None

        # 1. 尝试 /s/{isrc}.song.json
        song_url = f"{LRC_RED_BASE}/s/{clean_isrc}.song.json"
        status, data = cls._http_get_json(song_url, deadline=deadline)

        raw_ttml = ""
        meta: Dict[str, Any] = {}
        endpoint = song_url
        endpoint_kind = "song.json"

        if status == 200 and data and data.get("source"):
            cls._consecutive_429 = 0
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
            # H05：429 已在 _http_get_json 最底层计过一次，此处只终止，绝不重复计数
            return None
        else:
            # P1-2: 降级回落请求 /s/{isrc}.ttml 与 /s/{isrc}.json
            if deadline is not None and (deadline - time.perf_counter()) < 0.2:
                return None

            meta_url = f"{LRC_RED_BASE}/s/{clean_isrc}.json"
            m_status, m_data = cls._http_get_json(meta_url, deadline=deadline)
            if m_status == 429:
                return None  # H05：底层已计数，避免同一次 429 被计两次
            if m_status == 200 and m_data:
                cls._consecutive_429 = 0
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
            if t_status == 429:
                return None  # H05：底层已计数，避免同一次 429 被计两次
            if t_status == 200 and t_text:
                cls._consecutive_429 = 0
                raw_ttml = t_text
                endpoint = ttml_url
                endpoint_kind = "ttml"

        if not raw_ttml:
            # 绝不写入永久负缓存！保证后续正常请求不被锁死（P0-1）
            return None

        # R13: 计算内存占用并按 32MB / 500 条 LRU 淘汰
        rec_size = len(raw_ttml.encode("utf-8")) + len(json.dumps(meta).encode("utf-8"))
        while cls._raw_cache and (
            len(cls._raw_cache) >= cls.MAX_CACHE_ENTRIES
            or (cls._cache_bytes + rec_size > cls.MAX_CACHE_BYTES)
        ):
            try:
                old_key = next(iter(cls._raw_cache))
                old_rec = cls._raw_cache.pop(old_key)
                old_size = old_rec.get("_size", 0)
                cls._cache_bytes = max(0, cls._cache_bytes - old_size)
            except Exception:
                break

        record = {
            "meta": meta,
            "raw_ttml": raw_ttml,
            "endpoint": endpoint,
            "endpoint_kind": endpoint_kind,
            "retrieved_at": time.time(),
            "_size": rec_size,
        }
        cls._raw_cache[clean_isrc] = record
        cls._cache_bytes += rec_size
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

        local_ver = (version_tokens(title) if title else set()) | (version_tokens(album) if album else set())
        remote_ver = (version_tokens(r_title) if r_title else set()) | (version_tokens(r_album) if r_album else set())
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

        # 逐字行覆盖度与时间戳有效性审计（P1-6 / H09 / H10）
        # H09：任何含非法词的残缺词列表都不得流入转换器；
        # H10：非法主唱段同样拒绝，不能只看幸存行的 invalid_word_lines。
        cov_ratio = parsed.get("word_coverage_ratio", 0.0)
        invalid_lines = parsed.get("invalid_word_lines", 0)
        singing_lines = parsed.get("singing_lines", 0)
        invalid_main_p = parsed.get("invalid_main_p_count", 0)
        raw_main_p = parsed.get("raw_main_p_count", singing_lines)
        if (singing_lines > 0 or raw_main_p > 0) and (
            cov_ratio < 0.95 or invalid_lines > 0 or invalid_main_p > 0
        ):
            logger.warning(
                "   ⚠️ [lrc-red] 逐字质量不达标 (覆盖率=%.2f < 0.95, 无效行=%d, 非法主唱段=%d)，丢弃候选: %s",
                cov_ratio, invalid_lines, invalid_main_p, clean_isrc
            )
            return None

        # 5. byModel 状态约束（P0-4）
        if by_model_raw is True:
            logger.warning("   ⚠️ [lrc-red] byModel=True 为模型/AI生成，不作为原生逐字候选交付")
            return None

        by_model_state = "false" if by_model_raw is False else "unknown"

        # 6. 置信度判定（P0-2: 严禁自我认证；R04: 本地艺人缺失时绝对不写 ISRC，且降级置信度）
        local_art = artist_tokens(artist) if artist else frozenset()
        remote_art = artist_tokens(r_artist) if r_artist else frozenset()
        local_art_valid = bool(local_art and not any(a in GENERIC_ARTISTS for a in local_art))
        remote_art_valid = bool(remote_art and not any(a in GENERIC_ARTISTS for a in remote_art))
        art_match = bool(local_art_valid and remote_art_valid and (local_art & remote_art))

        title_strict_match = bool(local_norm_t_strict and remote_norm_t_strict and local_norm_t_strict == remote_norm_t_strict)

        # 远端若缺失标题/艺人关键凭证，绝不判定 strict
        if not (r_title and r_artist):
            confidence = "insufficient"
            logger.info("   ℹ️ [lrc-red] 远端元数据缺失标题/歌手，标记 insufficient_evidence")
            if title or artist:
                # 调用方提供了标题或歌手进行查询，但远端缺失对应信息，不能自我认证通过
                return None
        elif not local_art_valid or not art_match:
            # R04: 本地无明确非泛称歌手或歌手无交集时，置信度降为 insufficient/loose，绝不 strict
            confidence = "insufficient" if not local_art_valid else "loose"
        elif title_strict_match:
            confidence = "strict"
        else:
            confidence = "loose"

        # 7. ISRC 回写资格判定（P0-5 封闭门禁 + R04/R05/R06 严密加固）
        dur_diff_ok = (
            duration_ms > 0
            and remote_dur_ms > 0
            and abs(duration_ms - remote_dur_ms) <= 3000
        )
        local_ver_all = (version_tokens(title) if title else set()) | (version_tokens(album) if album else set())
        remote_ver_all = (version_tokens(r_title) if r_title else set()) | (version_tokens(r_album) if r_album else set())
        version_identical = (local_ver_all == remote_ver_all)

        # H06：与预筛共用同一「已知硬版本冲突」判据；证据不足（unknown）时保留候选，
        # 但不授予元数据写回资格。
        version_evidence = cls._assess_version_evidence(title, album, r_title, r_album)
        if version_evidence == "conflict":
            logger.warning("   ⚠️ [lrc-red] 版本硬冲突（%s），丢弃候选: %s", version_evidence, clean_isrc)
            return None

        has_full_identity = bool(
            r_title and r_artist and local_art_valid and remote_art_valid and art_match
        )

        isrc_write_eligible = (
            confidence == "strict"
            and by_model_raw is False
            and dur_diff_ok
            and title_strict_match
            and has_full_identity
            and version_identical
            and version_evidence == "consistent"
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
            "version_evidence": version_evidence,
            "match": {
                "confidence": confidence,
                "isrc_origin": match_origin,
                "by_model": by_model_state,
                "title_strict": title_strict_match,
                "word_coverage_ratio": cov_ratio,
            },
            "provenance": {
                "provider": "lrc.red",
                "attribution": LRC_RED_ATTRIBUTION,
                "by_model": by_model_state,
                "origin_status": "unverified_origin",
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
    def _assess_version_evidence(
        cls,
        local_title: str,
        local_album: str,
        cand_title: str,
        cand_album: str,
    ) -> str:
        """最小「已知硬版本冲突」谓词（H06）。

        预筛与最终验收共用同一判据，避免两处口径不一致。

        返回：
          * ``conflict``   —— 存在对称的硬版本标识差异（Live/Studio、Remix、Cover…）
          * ``unknown``    —— 候选未提供专辑，而本地专辑自带版本标识，无法确认专辑侧版本
          * ``consistent`` —— 两侧版本标识一致或都不含版本标识

        ``unknown`` 的候选可以保留参与打分，但**不得授权元数据写回**。
        """
        from ..utils.text_match import VERSION_EXCLUSION_TOKENS

        local_title_tok = version_tokens(local_title or "")
        local_album_tok = version_tokens(local_album or "")
        cand_title_tok = version_tokens(cand_title or "")
        cand_album_tok = version_tokens(cand_album or "")

        local_all = local_title_tok | local_album_tok
        cand_all = cand_title_tok | cand_album_tok
        if ((local_all - cand_all) | (cand_all - local_all)) & VERSION_EXCLUSION_TOKENS:
            return "conflict"
        # 候选完全没有提供专辑，而本地有专辑信息可对照：专辑侧版本无从确认
        if local_album and not cand_album:
            return "unknown"
        return "consistent"

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

            # 时长门禁：±5000ms；缺时长记为「未知」，不享受精确匹配加分（H06）
            dur_known = duration_ms > 0 and cand_dur_ms > 0
            if dur_known:
                dur_diff = abs(duration_ms - cand_dur_ms)
                if dur_diff > 5000:
                    continue
            else:
                dur_diff = 0

            # 标题门禁
            cand_t_loose = norm_title(cand_title, keep_version=False) if cand_title else ""
            if local_norm_t_loose and cand_t_loose and local_norm_t_loose != cand_t_loose:
                continue

            # 版本互斥门禁（H06）：标题与专辑同域判定，已知硬冲突先在预筛剔除，
            # 不占用后续前三名的下载机会。
            cand_album = h.get("album") or ""
            version_evidence = cls._assess_version_evidence(
                title, album, cand_title, cand_album
            )
            if version_evidence == "conflict":
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
            # H06：缺失时长既不加分也不减分，改为固定折损，避免「未知」被当成精确匹配 0 差而占优
            if dur_known:
                score -= dur_diff // 100
            else:
                score -= UNKNOWN_DUR_PENALTY

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
        valid_email, _ = _lrc_red_contact()
        if not valid_email:
            logger.warning("   ⚠️ [lrc-red] 未配置合规联系邮箱（MTL_LRC_RED_CONTACT 须为有效邮箱），跳过该源")
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
