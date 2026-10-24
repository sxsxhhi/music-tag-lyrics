# -*- coding: utf-8 -*-
"""Apple Music TTML 歌词源：多镜像按 origin 攒票回退、本地索引/iTunes 发现与严格门禁。"""

import os
import time
import logging
from typing import Optional, Dict, Any, Tuple, List, Set, NamedTuple

from ..parsers.ttml_parser import parse_ttml
from ..utils import http
http_get_text_ex = http.http_get_text_ex
from ..utils.http import HttpResult
from ..utils.text_utils import is_foreign_track, is_instrumental_placeholder
from ..utils.text_match import (
    norm_title, artist_tokens, version_tokens, norm_album, GENERIC_ALBUMS
)
from .amll_index import AmllIndex, Entry
from .itunes_client import ITunesClient, itunes_durations

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
    def fetch_by_apple_id(
        cls,
        apple_id: str,
        timeout: float = SONG_DEADLINE_S
    ) -> Optional[Dict[str, Any]]:
        """直给 Apple ID 抓取 TTML 并解析为增强歌词结构。"""
        if not apple_id:
            return None
        verdict, text, meta = fetch_ttml_raw(str(apple_id).strip(), deadline_s=timeout)
        if verdict != "OK" or not text:
            return None

        data = parse_ttml(text)
        if not data or not data.get("lines"):
            return None

        data["source"] = "apple-ttml"
        data["lyric_type"] = "word"
        data["time_precision"] = 3
        data["apple_music_id"] = str(apple_id).strip()
        data["meta_fetch"] = meta
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
                        elif len(chosen_entry.ids) > 1:
                            # 其余最多 3 个 id 各打 1 次，不计票，直接尝试首个镜像
                            gbcl = MIRRORS[0]
                            for alt_id in chosen_entry.ids[1:4]:
                                alt_url = f"{gbcl.base}{TTML_PATH.format(apple_id=alt_id)}"
                                alt_res = http.http_get_text_ex(alt_url, timeout=2.5)
                                if alt_res.status == 200 and "<tt" in (alt_res.text[:2000] if alt_res.text else "").lower():
                                    target_apple_id = alt_id
                                    ttml_text = alt_res.text
                                    resolved_mirror = gbcl.name
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
        # 结构门：只拒逐字行 0 行与占位词
        has_any_words = any(l.get("words") for l in lines)
        if not has_any_words:
            logger.info("   ℹ️ [apple-ttml] 门禁丢弃：0 行逐字")
            return None

        if is_instrumental_placeholder(lines, duration_ms):
            logger.info("   ℹ️ [apple-ttml] 门禁丢弃：纯音乐占位")
            return None

        ttml_tags = data.get("tags") or {}
        ttml_title = ttml_tags.get("ti") or ""
        ttml_artist = ttml_tags.get("ar") or ""
        ttml_album = ttml_tags.get("al") or ""

        # ① 标题门
        t_strict = norm_title(title, keep_version=True)
        t_loose = norm_title(title, keep_version=False)
        ttml_t_strict = norm_title(ttml_title, keep_version=True)
        ttml_t_loose = norm_title(ttml_title, keep_version=False)

        if t_loose != ttml_t_loose:
            logger.info("   ℹ️ [apple-ttml] 门禁丢弃：标题不匹配 (%s vs %s)", t_loose, ttml_t_loose)
            return None

        title_strict_match = (t_strict == ttml_t_strict)

        # ② 艺人门
        q_art = artist_tokens(artist)
        ttml_art = artist_tokens(ttml_artist)
        if q_art and ttml_art and not (q_art & ttml_art):
            logger.info("   ℹ️ [apple-ttml] 门禁丢弃：艺人无交集 (%s vs %s)", q_art, ttml_art)
            return None

        # ③ 版本门
        q_ver = version_tokens(title)
        ttml_ver = version_tokens(ttml_title)
        ver_diff = (q_ver - ttml_ver) | (ttml_ver - q_ver)
        if ver_diff and any(k in ver_diff for k in ("live", "现场", "演唱会", "remix", "混音", "demo")):
            logger.info("   ℹ️ [apple-ttml] 门禁丢弃：版本互斥冲突")
            return None

        # ④ 时长门禁与置信度判别 (4.4.1 & 4.4.2)
        last_end = lines[-1].get("end", 0) if lines else 0
        body = data.get("duration_ms") or 0
        axis = min(x for x in (body, last_end) if x > 0) if (body or last_end) else 0
        has_axis = duration_ms > 0 and axis > 0

        confidence = "loose"
        if not has_axis:
            logger.info("   ℹ️ [apple-ttml] 时长证据不可用（body_dur 缺失 / 音频时长 0），本首降级")
            if is_zh:
                return None  # 华语不接受无轴 loose，直接落酷狗
            confidence = "loose"
        else:
            # E3a: 上界与预览门
            is_preview = (duration_ms > 90000 and axis < 45000)
            if axis > duration_ms + 8000 or is_preview:
                logger.info("   ℹ️ [apple-ttml] 门禁丢弃：预览或冲轴 (axis=%d, duration=%d)", axis, duration_ms)
                return None

            # E3b_floor: 截断地板，始终必过
            if axis / duration_ms < 0.50:
                logger.info("   ℹ️ [apple-ttml] 门禁丢弃：截断地板未过 (ratio=%.3f)", axis / duration_ms)
                return None

            # E1: iTunes trackTimeMillis
            durs = itunes_durations([target_apple_id], is_zh=is_zh)
            itunes_track_ms = durs.get(target_apple_id, 0)
            e1_available = (itunes_track_ms > 0)
            e1_pass = e1_available and (abs(itunes_track_ms - duration_ms) <= 5000)

            if e1_available and not e1_pass:
                # 硬规则 1：E1 有值但不合格，不得回落 E2，直接拒！
                logger.info("   ℹ️ [apple-ttml] 门禁丢弃：iTunes 时长不对齐 (itunes=%d, local=%d)", itunes_track_ms, duration_ms)
                return None

            # E2: 专辑精确匹配
            local_al_n = norm_album(album)
            ttml_al_n = norm_album(ttml_album)
            e2_pass = bool(local_al_n and ttml_al_n and local_al_n == ttml_al_n and local_al_n not in GENERIC_ALBUMS)

            # E3b_strict: 尾差与比例
            e3b_strict_pass = ((duration_ms - axis) <= 90000) and (axis / duration_ms >= 0.70)

            # strict 判定
            strict_evidence = e1_pass or (e2_pass and e3b_strict_pass)
            is_strict = title_strict_match and strict_evidence

            if is_zh:
                if not is_strict:
                    logger.info("   ℹ️ [apple-ttml] 华语未达 strict 门槛，交由酷狗处理")
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
