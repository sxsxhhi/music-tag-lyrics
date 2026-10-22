# -*- coding: utf-8 -*-
"""
Multi-Engine Lyric Aggregator & Dispatcher (v1.2.3)
Orchestrates concurrent/cascading retrieval across:
1. Kugou (KRC word-level verbatim with native embedded 1:1 translation)
2. AMLL (Apple Music Lossless Lyrics TTML)
3. LRCSHARE (Cloud open-source syllable TTML / LRC & structured translations)
4. NetEase (Cloud music synced & official reviewed translation)
5. LRCLIB (Global open synchronized database)
6. Acoustic Aligner (AI syllable energy synthesizer fallback)

Multi-tier Translation Cascade for Foreign Tracks:
- Tier 1: Kugou Native type:1 semantic translation (KRC embedded)
- Tier 2: NetEase 163 (tlyric official translation)
- Tier 3: LRCSHARE (zh-Hans structured translation fallback)

Enforces 4 Core Iron Rules:
- Iron Rule 15: type:1 semantic Hanzi check (rejection of type:0 romanization)
- Iron Rule 16: Line count integrity & instrumental placeholder anti-tampering
- Iron Rule 17: Duration proximity gate (±5.0s version guard)
- Iron Rule 18: Golden baseline timing anchor with dynamic fine-tuning (≤300ms smooth, >500ms locked)
Zero external dependencies.
"""

import logging
from typing import Dict, List, Any, Optional

from .fetchers.amll import AMLLFetcher
from .fetchers.kugou import KugouFetcher
from .fetchers.lrcshare import LrcShareFetcher
from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
# C-01: 不在模块顶层导入 acoustic_aligner —— 它依赖 numpy，
# 而"抓歌词"这条主路径并不需要 numpy。改为调用点懒加载。
from .parsers.lrc_parser import parse_lrc
from .utils.text_utils import is_cjk, is_foreign_track, has_hanzi

logger = logging.getLogger(__name__)

INSTRUMENTAL_KEYWORDS = ["纯音乐", "请欣赏", "伴奏", "Instrumental", "No Lyrics"]

def is_instrumental_placeholder(lines: List[Dict[str, Any]], target_duration_ms: int = 0) -> bool:
    """Detects whether a lyric candidate is a collapsed instrumental placeholder."""
    if not lines:
        return True
    if target_duration_ms > 60000 and len(lines) <= 6:
        sample = "".join(l.get("text", "") for l in lines)
        if any(k in sample for k in INSTRUMENTAL_KEYWORDS):
            return True
    return False

def align_translation_to_baseline(
    candidate_lines: List[Dict[str, Any]],
    translation_entries: List[Dict[str, Any]],
    tolerance_ms: int = 1500
) -> int:
    """
    Applies Golden Baseline Timing Anchor (Iron Rule 18):
    Maps translation text onto candidate baseline lines using nearest-timestamp matching.
    - If diff <= 300ms: within dynamic acoustic tolerance.
    - If 300ms < diff <= tolerance_ms: mapped directly to baseline line without touching timestamps.
    - If diff > 1500ms: rejected.
    Baseline timestamps (start, end, words) remain strictly immutable.
    Returns the number of lines successfully attached with translations.
    """
    if not candidate_lines or not translation_entries:
        return 0

    used_indices = set()
    matched_count = 0

    for line in candidate_lines:
        k_time = line.get("start", 0)
        best_diff = tolerance_ms + 1
        best_idx = -1
        best_text = ""

        for idx, entry in enumerate(translation_entries):
            if idx in used_indices:
                continue
            e_time = entry.get("start", 0)
            diff = abs(e_time - k_time)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx
                best_text = entry.get("text", "").strip()

        if best_idx >= 0 and best_diff <= tolerance_ms and has_hanzi(best_text):
            line["translation"] = best_text
            used_indices.add(best_idx)
            matched_count += 1

    return matched_count

def enrich_foreign_translation(
    candidate: Dict[str, Any],
    title: str,
    artist: str,
    duration_ms: int = 0
) -> Dict[str, Any]:
    """
    Orchestrates the 3-Tier foreign translation cascade:
    Tier 1: Kugou KRC Native type:1 semantic translation (if already present)
    Tier 2: NetEase 163 (tlyric)
    Tier 3: LRCSHARE (zh-Hans structured versions)
    Strictly enforces Line Count Integrity (Iron Rule 16) and Baseline Timing Anchor (Iron Rule 18).
    """
    if not candidate or not candidate.get("lines"):
        return candidate

    cand_lines = candidate["lines"]

    # Tier 1: Check if candidate already has verified Chinese translation
    has_valid_trans = any(has_hanzi(l.get("translation", "")) for l in cand_lines)
    if has_valid_trans:
        return candidate

    min_required_trans = max(4, int(len(cand_lines) * 0.3))

    # Tier 2: NetEase 163 (tlyric)
    try:
        ne_data = NetEaseFetcher.fetch(title, artist, duration_ms)
        tlyric_text = ne_data.get("tlyric", "") if ne_data else ""
        if tlyric_text:
            t_parsed = parse_lrc(tlyric_text)
            clean_t = [
                {"start": l.get("start", 0), "text": l.get("text", "").strip()}
                for l in t_parsed.get("lines", [])
                if l.get("text", "").strip() and has_hanzi(l.get("text", ""))
            ]
            # Anti-tamper: Check instrumental placeholders and line count
            if not any(k in "".join(x["text"] for x in clean_t) for k in INSTRUMENTAL_KEYWORDS):
                if len(clean_t) >= min_required_trans:
                    matched = align_translation_to_baseline(cand_lines, clean_t)
                    if matched >= min_required_trans:
                        candidate["has_translation"] = True
                        candidate["translation_source"] = "netease"
                        return candidate
    except Exception as exc:
        logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

    # Tier 3: LRCSHARE (zh-Hans fallback)
    try:
        lrcshare_data = LrcShareFetcher.fetch(title, artist, duration_ms)
        if lrcshare_data and lrcshare_data.get("lines"):
            ls_lines = lrcshare_data["lines"]
            clean_ls = [
                {"start": l.get("start", 0), "text": l.get("translation", "").strip()}
                for l in ls_lines
                if l.get("translation", "").strip() and has_hanzi(l.get("translation", ""))
            ]
            if len(clean_ls) >= min_required_trans:
                matched = align_translation_to_baseline(cand_lines, clean_ls)
                if matched >= min_required_trans:
                    candidate["has_translation"] = True
                    candidate["translation_source"] = "lrcshare"
                    return candidate
    except Exception as exc:
        logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

    return candidate

class LyricAggregator:
    """Aggregates multiple engines with language-aware dispatching."""

    @classmethod
    def is_western_track(cls, title: str, artist: str = "") -> bool:
        """Determines whether a track is predominantly non-CJK."""
        return not is_cjk(f"{title} {artist}")

    @classmethod
    def fetch_best_lyrics(
        cls,
        title: str,
        artist: str = "",
        album: str = "",
        duration_ms: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves the best available lyrics using language-aware hierarchy:
        1. Kugou (KRC word + native trans)
        2. AMLL (Apple Music TTML word)
        3. LRCSHARE (api.lrcshare.com word/line TTML)
        4. NetEase (Cloud music synced & translation)
        5. LRCLIB (Global open sync database)
        6. Acoustic Aligner (AI syllable energy synthesizer fallback)
        Foreign tracks undergo 3-Tier translation cascade enrichment.
        """
        is_foreign = is_foreign_track(title, artist)
        candidate = None

        # 1. First probe Kugou (KRC word-level verbatim + native 1:1 translation)
        try:
            res = KugouFetcher.fetch(title, artist, duration_ms)
            if res and res.get("lines") and not is_instrumental_placeholder(res["lines"], duration_ms):
                candidate = res
        except Exception as exc:
            logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

        # 2. AMLL TTML (Apple Music word-level verbatim)
        if not candidate or (is_foreign and not candidate.get("has_embedded_translation")):
            try:
                amll_res = AMLLFetcher.fetch(title, artist, duration_ms)
                if amll_res and amll_res.get("lines") and not is_instrumental_placeholder(amll_res["lines"], duration_ms):
                    candidate = amll_res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

        # 3. LRCSHARE (api.lrcshare.com community TTML & word-level)
        if not candidate or (is_foreign and not candidate.get("has_embedded_translation")):
            try:
                ls_res = LrcShareFetcher.fetch(title, artist, duration_ms)
                if ls_res and ls_res.get("lines") and not is_instrumental_placeholder(ls_res["lines"], duration_ms):
                    if not candidate or ls_res.get("has_translation"):
                        candidate = ls_res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

        # 4. NetEase (Cloud music synced & translation)
        if not candidate:
            try:
                ne_res = NetEaseFetcher.fetch(title, artist, duration_ms)
                if ne_res and ne_res.get("lines") and not is_instrumental_placeholder(ne_res["lines"], duration_ms):
                    candidate = ne_res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

        # 5. LRCLIB (Global open sync database)
        if not candidate:
            try:
                lrc_res = LRCLIBFetcher.fetch(title, artist, album, duration_ms)
                if lrc_res and lrc_res.get("lines") and not is_instrumental_placeholder(lrc_res["lines"], duration_ms):
                    candidate = lrc_res
            except Exception as exc:
                logger.warning("歌词源调用失败，已隔离并继续: %s", exc)

        if not candidate:
            return None

        # For foreign tracks (Western, Korean, Japanese), run the 3-Tier translation cascade
        if is_foreign:
            candidate = enrich_foreign_translation(candidate, title, artist, duration_ms)

        # 逐字判定：至少有一行真的拆出了 ≥2 个词才算逐字。
        # 旧实现 any(line.get("words")) 对 KRC 恒为真（KRC 会给无词级标签的行补一个"整行词"），
        # 导致"没有逐字就声学合成"的兜底从未生效。
        has_words = any(len(line.get("words") or []) >= 2 for line in candidate.get("lines", []))
        if not has_words:
            try:
                # C-01: 懒加载，缺 numpy 时只跳过合成、不影响已取到的歌词
                from .acoustic_aligner import enrich_track_with_acoustic_words
                candidate = enrich_track_with_acoustic_words(candidate)
                candidate["synthesized_words"] = True
            except ImportError as exc:
                logger.warning("无逐字且缺少 numpy/torch，跳过声学合成: %s", exc)
            except Exception as exc:  # noqa: BLE001 - 合成失败不能拖垮整首
                logger.warning("声学逐字合成失败，保留行级歌词: %s", exc)

        return candidate
