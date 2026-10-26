# -*- coding: utf-8 -*-
"""Apple Music TTML 歌词源：多镜像按 origin 攒票回退、本地索引/iTunes 发现与严格门禁。"""

import os
import re
import time
import logging
from typing import Optional, Dict, Any, Tuple, List, Set, NamedTuple

from ..parsers.ttml_parser import parse_ttml
from ..utils import http
from ..utils.time_utils import compute_duration_axis
from ..utils.text_utils import is_foreign_track, is_instrumental_placeholder
from ..utils.text_match import (
    norm_title, artist_tokens, version_tokens, norm_album, GENERIC_ALBUMS,
    VERSION_EXCLUSION_TOKENS,
)
from .amll_index import AmllIndex, Entry
from .itunes_client import ITunesClient, itunes_durations
from .kugou import PREVIEW_MIN_TRACK_MS, PREVIEW_MAX_LAST_END_MS, OVERRUN_TOLERANCE_MS

logger = logging.getLogger(__name__)

class MirrorConfig(NamedTuple):
    name: str
    base: str
    origin: str
    vote404: int
    timeout: float
    retry403: int

MIRRORS: List[MirrorConfig] = [
    MirrorConfig("gbclstudio", "https://amll-ttml-db.gbclstudio.cn",                           "gbcl",     1, 3.0, 1),
    MirrorConfig("testingcf",  "https://testingcf.jsdelivr.net/gh/amll-dev/amll-ttml-db@main", "jsdelivr", 1, 3.0, 1),
    MirrorConfig("gcore",      "https://gcore.jsdelivr.net/gh/amll-dev/amll-ttml-db@main",     "jsdelivr", 1, 3.0, 1),
    MirrorConfig("fastly",     "https://fastly.jsdelivr.net/gh/amll-dev/amll-ttml-db@main",    "jsdelivr", 1, 4.0, 0),
    MirrorConfig("bikonoo",    "https://amlldb.bikonoo.com",                                   "bikonoo",  1, 5.0, 0),
    MirrorConfig("dimeta",     "https://amll.mirror.dimeta.top/api/db",                        "dimeta",   0, 3.0, 1),
]

TTML_PATH = "/am-lyrics/{apple_id}.ttml"
QUORUM = 2             # 不同 origin 数
SONG_DEADLINE_S = 6.0  # 单曲 TTML 抓取硬顶

# v1.5.1-D1: Apple Music ID 必须是纯数字。入口收口，防索引/CLI/搜索结果
# 里的非法值被拼进 URL（如 `1&country=us` / `../`）。索引侧 amll_index.ID_RE
# 已做同样校验，这里是第二道防线，覆盖 --apple-id 直取路径。
_APPLE_ID_RE = re.compile(r"^\d{1,16}$")


def _valid_apple_id(value: Any) -> Optional[str]:
    s = str(value or "").strip()
    return s if _APPLE_ID_RE.match(s) else None


def _has_verbatim_words(lines: List[Dict[str, Any]]) -> bool:
    """是否存在真 span 逐字词（整句兜底的 synthetic word 不算）。A5 去重：原先两处内联。"""
    return any(
        w.get("start") is not None and not w.get("synthetic")
        for l in lines for w in (l.get("words") or [])
    )


def fetch_ttml_raw(
    apple_id: str,
    deadline_s: float = SONG_DEADLINE_S,
    mirrors: Optional[List[MirrorConfig]] = None
) -> Tuple[str, str, Dict[str, Any]]:
    """
    按镜像表与 origin 攒票机制拉取 TTML 文本。
    
    返回 (verdict, text, meta):
      verdict in ("OK", "NOT_FOUND", "SUSPECT_MISSING", "UNREACHABLE")
    """
    # v1.5.1-D1: 非法 ID 直接判未收录，绝不进 URL
    safe_id = _valid_apple_id(apple_id)
    if not safe_id:
        logger.warning("   ⚠️ [apple-ttml] 丢弃非法 Apple ID: %r", apple_id)
        return "NOT_FOUND", "", {"apple_id": str(apple_id), "invalid_id": True}
    apple_id = safe_id

    mirror_list = mirrors if mirrors is not None else MIRRORS
    t_start = time.perf_counter()
    origins_404: Set[str] = set()
    shell_count = 0
    attempts: List[Dict[str, Any]] = []

    for m in mirror_list:
        elapsed = time.perf_counter() - t_start
        rem = deadline_s - elapsed
        if rem <= 0.1:
            break
        req_timeout = min(m.timeout, rem)
        url = f"{m.base}{TTML_PATH.format(apple_id=apple_id)}"

        tries = 1 + (m.retry403 if m.retry403 > 0 else 0)
        res = None
        for attempt_idx in range(tries):
            if attempt_idx > 0:
                time.sleep(0.25)
                elapsed = time.perf_counter() - t_start
                if deadline_s - elapsed <= 0.1:
                    break
            res = http.http_get_text_ex(url, timeout=req_timeout)
            if res.status != 403:
                break

        if res is None:
            continue

        attempts.append({
            "mirror": m.name,
            "origin": m.origin,
            "status": res.status,
            "elapsed_ms": res.elapsed_ms,
            "error": res.error,
        })

        if res.status == 200:
            if "<tt" in (res.text[:2000] if res.text else "").lower():
                meta = {
                    "mirror": m.name,
                    "origin": m.origin,
                    "attempts": attempts,
                    "total_ms": int((time.perf_counter() - t_start) * 1000),
                }
                return "OK", res.text, meta
            else:
                shell_count += 1
                if shell_count >= 3:
                    meta = {"attempts": attempts, "reason": "html_shell_exceeded"}
                    return "UNREACHABLE", "", meta
                continue

        if res.status == 404:
            if m.vote404:
                origins_404.add(m.origin)
                if len(origins_404) >= QUORUM:
                    meta = {
                        "origins_404": list(origins_404),
                        "attempts": attempts,
                        "total_ms": int((time.perf_counter() - t_start) * 1000),
                    }
                    return "NOT_FOUND", "", meta
            else:
                logger.debug("dimeta 404: 不计票，记 stale_hint 继续下一镜像")
            continue

    total_elapsed = time.perf_counter() - t_start
    meta = {
        "origins_404": list(origins_404),
        "attempts": attempts,
        "total_ms": int(total_elapsed * 1000),
    }

    if len(origins_404) >= QUORUM:
        return "NOT_FOUND", "", meta
    if len(origins_404) == 1:
        return "SUSPECT_MISSING", "", meta
    return "UNREACHABLE", "", meta


def disambiguate_entry(
    entries: List[Entry],
    local_album: str,
    duration_ms: int,
    is_zh: bool
) -> Optional[Entry]:
    """33 个同名歧义键消歧：① 组内专辑全量精确匹配；② 批量 lookup 取 |Δ| 最小；③ 仍并列则全弃。"""
    al = norm_album(local_album)
    if al and al not in GENERIC_ALBUMS:
        hit = [e for e in entries if al in e.album_norms]
        if len(hit) == 1:
            return hit[0]

    # ② 批量查询主 ID 时长
    if duration_ms > 0 and len(entries) >= 2:
        top_ids = [e.ids[0] for e in entries[:2] if e.ids]
        durs = itunes_durations(top_ids, is_zh=is_zh)
        ok = []
        for e in entries[:2]:
            if not e.ids:
                continue
            d = durs.get(e.ids[0])
            if d and d > 0:
                diff = abs(d - duration_ms)
                if diff <= 5000:
                    ok.append((diff, e))
        if len(ok) == 1:
            return ok[0][1]

    # ③ 仍并列或无时长 -> 全弃
    logger.info("   ⚠️ [apple-ttml] 同名多文件歧义未解，放弃本首（33 键之一）")
    return None


class AppleTTMLFetcher:
    """Apple Music TTML 抓取与调度适配器。"""

    @classmethod
    def fetch_by_apple_id_ex(
        cls,
        apple_id: str,
        timeout: float = SONG_DEADLINE_S
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """直给 Apple ID 抓取，返回 (歌词数据或 None, verdict)。

        verdict: OK / NOT_FOUND(AMLL 未收录) / UNREACHABLE / SUSPECT_MISSING(不可达类)
                 / PARSE_EMPTY / NO_VERBATIM(内容不合格) / INVALID(空 ID)。
        """
        if not apple_id:
            return None, "INVALID"
        verdict, text, meta = fetch_ttml_raw(str(apple_id).strip(), deadline_s=timeout)
        if verdict != "OK" or not text:
            return None, verdict

        data = parse_ttml(text)
        if not data or not data.get("lines"):
            return None, "PARSE_EMPTY"
        if not _has_verbatim_words(data["lines"]):
            return None, "NO_VERBATIM"

        data["source"] = "apple-ttml"
        data["lyric_type"] = "word"
        data["time_precision"] = 3
        data["apple_music_id"] = str(apple_id).strip()
        data["meta_fetch"] = meta
        return data, "OK"

    @classmethod
    def fetch_by_apple_id(
        cls,
        apple_id: str,
        timeout: float = SONG_DEADLINE_S
    ) -> Optional[Dict[str, Any]]:
        """兼容旧签名：只要数据，丢弃 verdict。"""
        data, _ = cls.fetch_by_apple_id_ex(apple_id, timeout=timeout)
        return data

    @classmethod
    def fetch(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = "",
        enable_itunes: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """全流程抓取：ID 发现 → 镜像攒票拉取 → 门禁判定。"""
        if os.environ.get("MTL_APPLE_TIER") == "off":
            return None

        # v1.5.1-D2: 全流程共用一个 deadline。旧版 alt-id 探测另起 6.0s，
        # 与主链叠加后单曲最坏 ≈30.5s，远超文档承诺的 8.5s。
        t_song_start = time.perf_counter()
        song_deadline = t_song_start + SONG_DEADLINE_S + 2.5  # ID 2.5s + TTML 6.0s

        is_foreign = is_foreign_track(title, artist)
        is_zh = not is_foreign

        target_apple_id: Optional[str] = None
        ttml_text: Optional[str] = None
        resolved_mirror: str = ""

        # -------------------------------------------------------------
        # 1. ID 发现：本地索引优先
        # -------------------------------------------------------------
        use_index = os.environ.get("MTL_APPLE_INDEX") != "off"
        if use_index:
            idx = AmllIndex.get_instance()
            if idx:
                entries = idx.lookup(title, artist=artist, album=album)
                if entries:
                    chosen_entry = None
                    if len(entries) > 1 and entries[0].ambiguous:
                        chosen_entry = disambiguate_entry(entries, album, duration_ms, is_zh)
                    else:
                        chosen_entry = entries[0]

                    if chosen_entry and chosen_entry.ids:
                        # 4.3.2 组内 id 廉价探测
                        # ids[0] 走完整 origin 攒票
                        first_id = chosen_entry.ids[0]
                        v, txt, meta = fetch_ttml_raw(first_id)
                        if v == "OK":
                            target_apple_id = first_id
                            ttml_text = txt
                            resolved_mirror = meta.get("mirror", "")
                        elif v != "UNREACHABLE" and len(chosen_entry.ids) > 1:
                            # v1.5.1-D2: alt-id 与主链**共用** song_deadline，不再另起 6.0s
                            for alt_id in chosen_entry.ids[1:4]:
                                # v1.5.1-D1: 索引里的 id 也必须过格式校验才进 URL
                                if not _valid_apple_id(alt_id):
                                    logger.warning(
                                        "   ⚠️ [apple-ttml] 跳过非法 alt ID: %r", alt_id
                                    )
                                    continue
                                if time.perf_counter() >= song_deadline:
                                    break
                                found_alt = False
                                for m in MIRRORS[:2]:
                                    rem_timeout = min(2.5, max(0.3, song_deadline - time.perf_counter()))
                                    if rem_timeout <= 0.3 or time.perf_counter() >= song_deadline:
                                        break
                                    alt_url = f"{m.base}{TTML_PATH.format(apple_id=alt_id)}"
                                    alt_res = http.http_get_text_ex(alt_url, timeout=rem_timeout)
                                    if alt_res.status == 200 and "<tt" in (alt_res.text[:2000] if alt_res.text else "").lower():
                                        target_apple_id = alt_id
                                        ttml_text = alt_res.text
                                        resolved_mirror = m.name
                                        found_alt = True
                                        break
                                    if alt_res.status == 403 or alt_res.status == 0:
                                        continue
                                    break
                                if found_alt:
                                    break

        # -------------------------------------------------------------
        # 2. ID 发现：iTunes 实时检索兜底
        # -------------------------------------------------------------
        use_itunes = enable_itunes and os.environ.get("MTL_APPLE_ITUNES") != "off"
        if not ttml_text and use_itunes:
            itunes_cand = ITunesClient.get_instance().search_track(title, artist, duration_ms)
            if itunes_cand:
                cand_id = itunes_cand.get("apple_id")
                if cand_id:
                    v, txt, meta = fetch_ttml_raw(cand_id)
                    if v == "OK":
                        target_apple_id = cand_id
                        ttml_text = txt
                        resolved_mirror = meta.get("mirror", "")

        if not ttml_text or not target_apple_id:
            return None

        # -------------------------------------------------------------
        # 3. 解析 TTML
        # -------------------------------------------------------------
        data = parse_ttml(ttml_text)
        lines = data.get("lines") or []
        if not lines:
            return None

        # -------------------------------------------------------------
        # 4. 门禁验证 (Gate)
        # -------------------------------------------------------------
        # 结构门：只认真 span 词，整句兜底的 synthetic word 不算逐字
        if not _has_verbatim_words(lines):
            logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：0 行逐字")
            return None

        if is_instrumental_placeholder(lines, duration_ms):
            logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：纯音乐占位")
            return None

        ttml_tags = data.get("tags") or {}
        ttml_title = ttml_tags.get("ti") or ""
        ttml_artist = ttml_tags.get("ar") or ""
        ttml_album = ttml_tags.get("al") or ""

        # ① 标题门 (P1-1: 补严空对空)
        t_strict = norm_title(title, keep_version=True)
        t_loose = norm_title(title, keep_version=False)
        ttml_t_strict = norm_title(ttml_title, keep_version=True)
        ttml_t_loose = norm_title(ttml_title, keep_version=False)

        if not t_loose or not ttml_t_loose or t_loose != ttml_t_loose:
            logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：标题不匹配 (%s vs %s)", t_loose, ttml_t_loose)
            return None

        title_strict_match = (t_strict == ttml_t_strict)

        # ② 艺人门 (P1-1: 双向交集守护)
        q_art = artist_tokens(artist)
        ttml_art = artist_tokens(ttml_artist)
        if not (q_art & ttml_art):
            logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：艺人无交集 (%s vs %s)", q_art, ttml_art)
            return None

        # ③ 版本门
        q_ver = version_tokens(title)
        ttml_ver = version_tokens(ttml_title)
        ver_diff = (q_ver - ttml_ver) | (ttml_ver - q_ver)
        if ver_diff and any(k in ver_diff for k in VERSION_EXCLUSION_TOKENS):
            logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：版本互斥冲突")
            return None

        # ④ 时长门禁与置信度判别 (P1-3: 统一 axis 算法)
        body = data.get("duration_ms") or 0
        axis = compute_duration_axis(lines, body)
        has_axis = duration_ms > 0 and axis > 0

        confidence = "loose"
        if not has_axis:
            logger.warning("   ⚠️ [apple-ttml] 时长证据不可用（body_dur 缺失 / 音频时长 0），本首降级")
            if is_zh:
                return None  # 华语不接受无轴 loose，直接落酷狗
            confidence = "loose"
        else:
            # E3a: 上界与预览门 (P2-10: 抽离统一常量)
            is_preview = (duration_ms > PREVIEW_MIN_TRACK_MS and axis < PREVIEW_MAX_LAST_END_MS)
            if axis > duration_ms + OVERRUN_TOLERANCE_MS or is_preview:
                logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：预览或冲轴 (axis=%d, duration=%d)", axis, duration_ms)
                return None

            # E3b_floor: 截断地板，始终必过
            if axis / duration_ms < 0.50:
                logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：截断地板未过 (ratio=%.3f)", axis / duration_ms)
                return None

            # E1: iTunes trackTimeMillis
            durs = itunes_durations([target_apple_id], is_zh=is_zh)
            itunes_track_ms = durs.get(target_apple_id, 0)
            e1_available = (itunes_track_ms > 0)
            e1_pass = e1_available and (abs(itunes_track_ms - duration_ms) <= 5000)

            if e1_available and not e1_pass:
                # 硬规则 1：E1 有值但不合格，不得回落 E2，直接拒！
                logger.warning("   ⚠️ [apple-ttml] 门禁丢弃：iTunes 时长不对齐 (itunes=%d, local=%d)", itunes_track_ms, duration_ms)
                return None

            # E2: 专辑精确匹配
            local_al_n = norm_album(album)
            ttml_al_n = norm_album(ttml_album)
            e2_pass = bool(local_al_n and ttml_al_n and local_al_n == ttml_al_n and local_al_n not in GENERIC_ALBUMS)

            # E3b_strict: 尾差与比例
            e3b_strict_pass = ((duration_ms - axis) <= PREVIEW_MIN_TRACK_MS) and (axis / duration_ms >= 0.70)

            # strict 判定
            strict_evidence = e1_pass or (e2_pass and e3b_strict_pass)
            is_strict = title_strict_match and strict_evidence

            if is_zh:
                if not is_strict:
                    logger.warning("   ⚠️ [apple-ttml] 华语未达 strict 门槛，交由酷狗处理")
                    return None
                confidence = "strict"
            else:
                confidence = "strict" if is_strict else "loose"

        # 构造交付候选
        data["source"] = "apple-ttml"
        data["lyric_type"] = "word"
        data["time_precision"] = 3
        data["apple_music_id"] = str(target_apple_id)
        data["match"] = {
            "confidence": confidence,
            "apple_music_id": target_apple_id,
            "mirror": resolved_mirror,
            "title_strict": title_strict_match,
            "has_axis": has_axis,
        }
        return data
