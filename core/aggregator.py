# -*- coding: utf-8 -*-
"""多源歌词调度：Apple TTML → 酷狗 → AMLL → 网易 → LRCSHARE → LRCLIB。外语原文与译文必须同一源整包。"""

import logging
from typing import Dict, List, Any, Optional, Callable

from .fetchers.apple_ttml import AppleTTMLFetcher
from .fetchers.amll import AMLLFetcher
from .fetchers.kugou import KugouFetcher, parsed_duration_reject_reason
from .fetchers.lrcshare import LrcShareFetcher
from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
from .parsers.lrc_parser import parse_lrc
from .utils.lyric_attach import attach_translations_by_nearest
from .utils.text_utils import (
    is_foreign_track, has_hanzi, is_instrumental_placeholder,
    INSTRUMENTAL_KEYWORDS,
)

logger = logging.getLogger(__name__)


def candidate_has_translation(cand: Optional[Dict[str, Any]]) -> bool:
    """候选是否已自带译文（KRC 用 has_embedded_translation，LRCSHARE 用 has_translation）。"""
    if not cand:
        return False
    if cand.get("has_embedded_translation") or cand.get("has_translation"):
        return True
    return any((line.get("translation") or "").strip() for line in cand.get("lines") or [])


def align_translation_to_baseline(
    candidate_lines: List[Dict[str, Any]],
    translation_entries: List[Dict[str, Any]],
    tolerance_ms: int = 1500
) -> int:
    """
    同一源内：按最近邻把译文挂到主行。tolerance 默认 1500ms。
    主行 start/end/words 不可变。（B1：算法下沉 utils/lyric_attach，本函数是薄包装。）
    """
    return attach_translations_by_nearest(
        candidate_lines, translation_entries, tolerance_ms=tolerance_ms
    )


def _hanzi_tlyric_entries(tlyric_text: str) -> List[Dict[str, Any]]:
    if not tlyric_text:
        return []
    t_parsed = parse_lrc(tlyric_text)
    clean_t = [
        {"start": l.get("start", 0), "text": l.get("text", "").strip()}
        for l in t_parsed.get("lines", [])
        if l.get("text", "").strip() and has_hanzi(l.get("text", ""))
    ]
    joined = "".join(x["text"] for x in clean_t)
    if any(k in joined for k in INSTRUMENTAL_KEYWORDS):
        return []
    return clean_t


def attach_same_source_translation(
    candidate: Dict[str, Any],
    trans_entries: List[Dict[str, Any]],
    source: str,
) -> bool:
    """把译文挂到**同一候选**的主行上。行数门槛仍是 max(4, 30%)。"""
    cand_lines = candidate.get("lines") or []
    if not cand_lines or not trans_entries:
        return False
    min_required = max(4, int(len(cand_lines) * 0.3))
    if len(trans_entries) < min_required:
        return False
    matched = align_translation_to_baseline(cand_lines, trans_entries)
    if matched >= min_required:
        candidate["has_translation"] = True
        candidate["translation_source"] = source
        return True
    return False


def enrich_foreign_translation(
    candidate: Dict[str, Any],
    title: str = "",
    artist: str = "",
    duration_ms: int = 0
) -> Dict[str, Any]:
    """
    不再跨源挂载（禁止酷狗原文 + 网易 tlyric）。
    译文只在 fetch_best_lyrics 梯子里以同一源整包替换。
    """
    return candidate


def _rank(cand: Optional[Dict[str, Any]], is_foreign: bool) -> int:
    """外语：译文是硬前提；华语：只要逐字。返回 -1..3。"""
    if not cand:
        return -1
    # 优化 1: cand.get("lyric_type") in ("word", "verbatim")
    is_word = cand.get("lyric_type") in ("word", "verbatim") or any(
        l.get("words") for l in cand.get("lines") or []
    )
    word = 1 if is_word else 0
    trans = 1 if candidate_has_translation(cand) else 0
    return (trans * 2 + word) if is_foreign else (2 + word)


def _adopt(
    cur: Optional[Dict[str, Any]],
    new: Optional[Dict[str, Any]],
    is_foreign: bool,
    trace: Optional[List[str]] = None
) -> Optional[Dict[str, Any]]:
    """单调等级采纳原则：只有严格更高等级才允许整包替换当前候选。"""
    if not new:
        return cur
    if not cur:
        return new  # 从无到有，永远允许

    cur_rank = _rank(cur, is_foreign)
    new_rank = _rank(new, is_foreign)

    if new_rank > cur_rank:
        if cur.get("source") == "apple-ttml":
            print(f"ℹ️ [apple-ttml] 已交出原文逐字，因等级提升整包换 {new.get('source')}")
            if trace is not None:
                trace.append("replaced_by_higher_rank")
        return new

    if cur.get("source") == "apple-ttml":
        print(f"ℹ️ [apple-ttml] 拒绝被 {new.get('source')} 顶替（等级 {new_rank} ≤ {cur_rank}）")
        if trace is not None:
            trace.append("downgrade_blocked")
    return cur


class LyricAggregator:
    """Apple TTML → Kugou → AMLL → NetEase → LRCSHARE → LRCLIB。Demucs 在 process_song 失败分支。"""

    @classmethod
    def fetch_best_lyrics(
        cls,
        title: str,
        artist: str = "",
        album: str = "",
        duration_ms: int = 0,
        *,
        enable_apple: bool = True
    ) -> Optional[Dict[str, Any]]:
        is_foreign = is_foreign_track(title, artist)
        candidate: Optional[Dict[str, Any]] = None
        trace: List[str] = []

        def need_more(c: Optional[Dict[str, Any]]) -> bool:
            return (not c) or (is_foreign and not candidate_has_translation(c))

        def _probe(fetch_fn: Callable[[], Optional[Dict[str, Any]]], source: str = "") -> Optional[Dict[str, Any]]:
            try:
                res = fetch_fn()
                if not res or not res.get("lines"):
                    return None
                if is_instrumental_placeholder(res["lines"], duration_ms):
                    return None
                # 六源统一时长门禁：只拒预览与冲轴
                if duration_ms > 0:
                    last_end = res["lines"][-1].get("end", 0)
                    reject = parsed_duration_reject_reason(last_end, duration_ms)
                    if reject:
                        reason = "预览截断" if reject == "preview" else "冲轴"
                        print(f"   ℹ️ [{source or 'unknown'}] 丢弃候选（{reason}）：末行 {last_end}ms / 曲长 {duration_ms}ms")
                        return None
                return res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)
            return None

        # T1 Apple TTML —— 第 1 档
        if enable_apple:
            apple_cand = _probe(lambda: AppleTTMLFetcher.fetch(
                title, artist=artist, duration_ms=duration_ms, album=album), "apple-ttml")
            candidate = _adopt(candidate, apple_cand, is_foreign, trace)

        # T2 酷狗 —— 无候选，或外语缺译文时探（华语 Apple 已命中则 need_more 为假）
        if need_more(candidate):
            kg_cand = _probe(lambda: KugouFetcher.fetch(title, artist, duration_ms, album=album), "kugou")
            candidate = _adopt(candidate, kg_cand, is_foreign, trace)

        # T3 AMLL(ncm) —— need_more 时进；仅 rank 提升才采纳
        if need_more(candidate):
            amll_cand = _probe(lambda: AMLLFetcher.fetch(title, artist, duration_ms), "amll")
            if amll_cand:
                candidate = _adopt(candidate, amll_cand, is_foreign, trace)

        # T4 网易 —— 整包替换必须自带可用 tlyric，且 rank 严格提升
        if need_more(candidate):
            ne_res = _probe(lambda: NetEaseFetcher.fetch(title, artist, duration_ms), "netease")
            if ne_res:
                entries = _hanzi_tlyric_entries(ne_res.get("tlyric", ""))
                attached = False
                if entries:
                    attached = attach_same_source_translation(ne_res, entries, "netease")
                if not candidate:
                    candidate = ne_res
                elif attached:
                    candidate = _adopt(candidate, ne_res, is_foreign, trace)

        # T5 LRCSHARE —— 缺主词，或外语缺译文且该源自身带译文整包，且 rank 提升
        if need_more(candidate):
            ls_res = _probe(lambda: LrcShareFetcher.fetch(title, artist, duration_ms), "lrcshare")
            if ls_res and (not candidate or ls_res.get("has_translation")):
                candidate = _adopt(candidate, ls_res, is_foreign, trace)

        # T6 LRCLIB —— 仅完全无候选
        if not candidate:
            candidate = _probe(lambda: LRCLIBFetcher.fetch(title, artist, album, duration_ms), "lrclib")

        return candidate
