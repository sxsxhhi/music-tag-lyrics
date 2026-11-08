# -*- coding: utf-8 -*-
"""多源歌词调度：Apple TTML → 网易 → QQ → 酷狗 → AMLL → LRCSHARE → LRCLIB。外语原文与译文必须同一源整包。"""

import logging
from typing import Dict, List, Any, Optional, Callable, Union

from .fetchers.apple_ttml import AppleTTMLFetcher
from .fetchers.amll import AMLLFetcher
from .fetchers.kugou import KugouFetcher, parsed_duration_reject_reason
from .fetchers.lrcshare import LrcShareFetcher
from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
from .fetchers.qq import QQFetcher
from .parsers.lrc_parser import parse_lrc
from .utils.time_utils import compute_duration_axis
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
    lines = cand.get("lines") or []
    if not lines:
        return bool(cand.get("has_embedded_translation") or cand.get("has_translation"))
    # P1-20: 译文 60% 完整率门禁（有效歌词行中至少 60% 带有非空译文才认定为自带译文）
    non_empty_lines = [l for l in lines if (l.get("text") or "").strip()]
    if not non_empty_lines:
        return bool(cand.get("has_embedded_translation") or cand.get("has_translation"))
    trans_lines = sum(1 for l in non_empty_lines if (l.get("translation") or "").strip())
    return (trans_lines / len(non_empty_lines)) >= 0.60


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


def _is_word_level(cand: Optional[Dict[str, Any]]) -> bool:
    """候选是否逐字：声明 word/verbatim，或任一行真的带 words。"""
    if not cand:
        return False
    if cand.get("lyric_type") in ("word", "verbatim"):
        return True
    return any(bool(l.get("words")) for l in cand.get("lines") or [])


def _confidence_level(cand: Optional[Dict[str, Any]]) -> str:
    """提取候选的身份/逐字置信度：strict / loose / insufficient / none。"""
    if not cand:
        return "none"
    match = cand.get("match") or {}
    conf = match.get("confidence")
    if conf:
        return str(conf).strip().lower()
    # 兼容原生国内源（网易 YRC / QQ QRC / 酷狗 KRC / AMLL）：未带 match.confidence 但声明 word 的按 strict
    if _is_word_level(cand):
        return "strict"
    return "none"


def _is_strict_word(cand: Optional[Dict[str, Any]]) -> bool:
    return _is_word_level(cand) and _confidence_level(cand) == "strict"


def _rank(cand: Optional[Dict[str, Any]], is_foreign: bool) -> Union[int, float]:
    """质量等级真值表 (R03, R16):
    保持与既有基准向后兼容（QQ/NetEase 默认 strict 逐字与行级保持 3 / 2 / 1 / 0），
    同时引入有理数级精度严格裁决 loose 与 insufficient：

    华语 (not is_foreign):
      -1: 无候选
       2: 行级 (基准)
       2.2: insufficient 逐字
       2.5: loose 逐字
       3: strict 逐字 (基准)

    外语 (is_foreign):
      -1: 无候选
       0: 无译文行级 (基准)
       0.5: 无译文 insufficient 逐字
       0.8: 无译文 loose 逐字
       1: 无译文 strict 逐字 (基准)
       2: 有译文行级 (基准)
       2.5: 有译文 insufficient 逐字
       2.8: 有译文 loose 逐字
       3: 有译文 strict 逐字 (基准)
    """
    if not cand:
        return -1
    is_word = _is_word_level(cand)
    conf = _confidence_level(cand) if is_word else "none"
    has_trans = candidate_has_translation(cand)

    if not is_foreign:
        if not is_word:
            return 2
        if conf == "strict":
            return 3
        if conf == "loose":
            return 2.5
        return 2.2  # insufficient or others

    # 外语
    if not has_trans:
        if not is_word:
            return 0
        if conf == "strict":
            return 1
        if conf == "loose":
            return 0.8
        return 0.5
    else:
        if not is_word:
            return 2
        if conf == "strict":
            return 3
        if conf == "loose":
            return 2.8
        return 2.5


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
        if cur.get("source") in ("apple-ttml", "lrc-red"):
            src_tag = cur.get("source")
            print(f"ℹ️ [{src_tag}] 已交出原文逐字，因等级提升整包换 {new.get('source')}")
            if trace is not None:
                trace.append("replaced_by_higher_rank")
        return new

    if cur.get("source") in ("apple-ttml", "lrc-red"):
        src_tag = cur.get("source")
        print(f"ℹ️ [{src_tag}] 拒绝被 {new.get('source')} 顶替（等级 {new_rank} ≤ {cur_rank}）")
        if trace is not None:
            trace.append("downgrade_blocked")
    return cur


class LyricAggregator:
    """Apple TTML → NetEase → QQ → Kugou → AMLL → LRCSHARE → LRCLIB。Demucs 在 process_song 失败分支。"""

    @classmethod
    def fetch_best_lyrics(
        cls,
        title: str,
        artist: str = "",
        album: str = "",
        duration_ms: int = 0,
        *,
        enable_apple: bool = True,
        isrc: str = ""
    ) -> Optional[Dict[str, Any]]:
        is_foreign = is_foreign_track(title, artist)
        candidate: Optional[Dict[str, Any]] = None
        trace: List[str] = []

        def need_more(c: Optional[Dict[str, Any]]) -> bool:
            """R03 调度决策真值表：
            - 无候选：必须继续搜索
            - 华语：只有达到 strict 原生逐字才允许提前停止；loose/insufficient 必须继续探测后续高质量源
            - 外语：必须同时具备 strict 原生逐字且拥有有效中文译文，才允许提前停止
            """
            if not c:
                return True
            if not is_foreign:
                return not _is_strict_word(c)
            # 外语：不仅要 strict 逐字，还必须带有效译文
            return not (_is_strict_word(c) and candidate_has_translation(c))


        def _probe(fetch_fn: Callable[[], Optional[Dict[str, Any]]], source: str = "") -> Optional[Dict[str, Any]]:
            try:
                res = fetch_fn()
                if not res or not res.get("lines"):
                    return None
                if is_instrumental_placeholder(res["lines"], duration_ms):
                    return None
                # 七源统一时长门禁：只拒预览与冲轴
                if duration_ms > 0:
                    axis = compute_duration_axis(res.get("lines", []), res.get("duration_ms", 0))
                    reject = parsed_duration_reject_reason(axis, duration_ms)
                    if reject:
                        reason = "预览截断" if reject == "preview" else "冲轴"
                        print(f"   ℹ️ [{source or 'unknown'}] 丢弃候选（{reason}）：基准轴 {axis}ms / 曲长 {duration_ms}ms")
                        return None
                return res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)
            return None

        # T1 Apple TTML —— 第 1 档 (含 AMLL 与 lrc.red 兜底)
        if enable_apple:
            apple_cand = _probe(lambda: AppleTTMLFetcher.fetch(
                title, artist=artist, duration_ms=duration_ms, album=album, isrc=isrc), "apple-ttml")
            candidate = _adopt(candidate, apple_cand, is_foreign, trace)

        # T2 网易 —— 行级也能落地，但 need_more 会继续往下找逐字；整包替换交给 _adopt 判定
        if need_more(candidate):
            ne_res = _probe(lambda: NetEaseFetcher.fetch(title, artist, duration_ms, album=album), "netease")
            if ne_res:
                entries = _hanzi_tlyric_entries(ne_res.get("tlyric", ""))
                if entries:
                    attach_same_source_translation(ne_res, entries, "netease")
                candidate = _adopt(candidate, ne_res, is_foreign, trace)

        # T3 QQ —— 只交逐字（行级由 fetcher 直接返回 None）
        if need_more(candidate):
            qq_cand = _probe(lambda: QQFetcher.fetch(title, artist, duration_ms, album=album), "qq")
            candidate = _adopt(candidate, qq_cand, is_foreign, trace)

        # T4 酷狗
        if need_more(candidate):
            kg_cand = _probe(lambda: KugouFetcher.fetch(title, artist, duration_ms, album=album), "kugou")
            candidate = _adopt(candidate, kg_cand, is_foreign, trace)

        # T5 AMLL(ncm)
        if need_more(candidate):
            amll_cand = _probe(lambda: AMLLFetcher.fetch(title, artist, duration_ms), "amll")
            candidate = _adopt(candidate, amll_cand, is_foreign, trace)

        # T6 LRCSHARE —— 不再用 has_translation 守卫（会拒绝华语逐字），交给 _rank 裁决
        if need_more(candidate):
            ls_res = _probe(lambda: LrcShareFetcher.fetch(title, artist, duration_ms), "lrcshare")
            candidate = _adopt(candidate, ls_res, is_foreign, trace)

        # T7 LRCLIB —— 仅完全无候选
        if not candidate:
            candidate = _probe(lambda: LRCLIBFetcher.fetch(title, artist, album, duration_ms), "lrclib")

        return candidate
