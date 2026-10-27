# -*- coding: utf-8 -*-
"""行标签语义统一（v1.6.0）。

背景（实测）
------------
行级 LRC 的行标签是「**整行开始显示**」的时间，不是「开口」的时间：实测
《皇后大道东》60 行，标签比真实起唱**早 p50 880ms**。整行一起亮时看不出来，
一旦变成逐字，就只能二选一——空档（字等声音）或抢跑（字铺满行区间）。

本模块把行标签从「显示轴」下沉到「起唱轴」：`tag = 首字 − 60ms`，
并做三档冲突裁决，**保证不制造行间重叠**（红线 2）。

三档仲裁（顺序不可换）
----------------------
① 无冲突：`tag = max(first − lead, prev_end + gap)`
② 冲突且上一行末字还能收：收到 `max(prev_start + min_word, first − gap)`，
   仍不够则收到 `first`
③ 上一行末字已到 50ms 下限仍压不下来：**整行后推** `shift = lower − first`
   （上界 `MIN_WORD + GAP − 1 = 99ms`，有硬上界，不会级联放大）

为什么不是 `min(tag, first)` 了事：那会抹掉第①步算出的防重叠下界，
实测可造 +20ms / +10ms 的行间重叠，并让 validator 裁出 30ms 假字。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

LEAD_MS = 60
GAP_MS = 50
MIN_WORD_MS = 50

TOL_MS = 150
SING_RATIO = 0.9
DISPLAY_MEDIAN_MS = 250

# 行轴语义取值
AXIS_DISPLAY = "display"      # 整行显示时间（需要校准）
AXIS_SING = "sing"            # 已对齐到起唱点（幂等：不再改）
AXIS_OFFICIAL = "official"    # TTML / KRC 官方逐字（行首天然 == 首字）
AXIS_UNKNOWN = "unknown"      # 判不准 → 不改写，只告警

# 官方逐字源（aggregator 实况取值）：kugou 命中 KRC 时返回 verbatim
_WORD_LEVEL_SOURCES = {"apple-ttml", "amll", "kugou-krc", "krc", "kugou"}


def infer_from_words(
    data: Dict[str, Any],
    tol_ms: int = TOL_MS,
    sing_ratio: float = SING_RATIO,
    display_median_ms: int = DISPLAY_MEDIAN_MS,
) -> str:
    """带词载荷判轴：已校准/官方逐字 → sing；典型显示轴 → display；判不准 → unknown。"""
    leads = [
        int(ln["words"][0]["start"]) - int(ln["start"])
        for ln in (data.get("lines") or [])
        if ln.get("words") and isinstance(ln.get("start"), int)
    ]
    if not leads:
        return AXIS_UNKNOWN
    ok = sum(1 for d in leads if -tol_ms <= d <= 0)
    if ok >= sing_ratio * len(leads):
        return AXIS_SING
    s = sorted(leads)
    return AXIS_DISPLAY if s[len(s) // 2] >= display_median_ms else AXIS_UNKNOWN


def mark_line_axis(data: Dict[str, Any], source: Optional[str] = None) -> str:
    """【对齐前调用】给载荷打行轴语义。这是 `line_axis` 的唯一生产者。

    必须在对齐**前**调用：zh 路径会把 `line.start` 改写成首字起点（lead=0），
    对齐后再推断会恒得 `sing`，校准永远空转。
    """
    src = str(source if source is not None else (data.get("source") or "")).lower().strip()
    has_words = any(ln.get("words") for ln in (data.get("lines") or []))

    if src == "apple-ttml" or src == "amll":
        axis = AXIS_OFFICIAL
    elif has_words:
        axis = infer_from_words(data)
        if axis == AXIS_SING and src in _WORD_LEVEL_SOURCES:
            axis = AXIS_OFFICIAL
    else:
        axis = AXIS_DISPLAY          # 纯行级 LRC：内嵌/本地/网易/LRCLIB/LRCSHARE/纯文本

    data["line_axis"] = axis
    logger.debug("  [axis] source=%s has_words=%s → %s", src or "-", has_words, axis)
    return axis


def calibrate_line_axis(
    data: Dict[str, Any],
    lead_ms: int = LEAD_MS,
    gap_ms: int = GAP_MS,
    min_word_ms: int = MIN_WORD_MS,
) -> Dict[str, Any]:
    """行标签下沉到起唱点。只处理 display；其余（sing/official/unknown）原样返回。"""
    axis = data.get("line_axis")
    if axis != AXIS_DISPLAY:
        return data

    prev_ln: Optional[Dict[str, Any]] = None
    prev_ws: Optional[List[Dict[str, Any]]] = None
    prev_end: Optional[int] = None
    delta = 0
    shifted = 0
    warns: List[Dict[str, Any]] = []

    for idx, ln in enumerate(data.get("lines") or []):
        ws = [w for w in (ln.get("words") or []) if isinstance(w.get("start"), int)]
        if not ws:
            # 纯译文条目（同时间戳独立行）：跟随上一主行位移，否则会比原文早 0.88s
            if isinstance(ln.get("start"), int):
                ln["start"] += delta
            if isinstance(ln.get("end"), int):
                ln["end"] += delta
            continue

        old_start = int(ln["start"]) if isinstance(ln.get("start"), int) else int(ws[0]["start"])
        first = int(ws[0]["start"])
        lower = (prev_end + gap_ms) if prev_end is not None else 0

        # ② 冲突：先收缩上一行末字（先保 50ms 间隙，再退到 0 间隙）
        if prev_ws is not None and lower > first:
            for floor_end in (first - gap_ms, first):
                cand = max(int(prev_ws[-1]["start"]) + min_word_ms, floor_end)
                if cand < int(prev_ws[-1]["end"]):
                    prev_ws[-1]["end"] = cand
                    prev_ws[-1]["duration"] = cand - int(prev_ws[-1]["start"])
                    if prev_ln is not None:
                        prev_ln["end"] = cand
                        if isinstance(prev_ln.get("start"), int):
                            prev_ln["duration"] = cand - int(prev_ln["start"])
                    prev_end = cand
                    lower = cand + gap_ms
                if lower <= first:
                    break

        # ③ 仍压不下来：整行后推（≤99ms），绝不 min(tag, first) 硬压
        shift = 0
        if lower > first and prev_ws is not None:
            shift = lower - first
            for w in ws:
                w["start"] = int(w["start"]) + shift
                w["end"] = int(w["end"]) + shift
            first += shift
            shifted += 1
            warns.append({"line": idx, "shift_ms": shift, "text": (ln.get("text") or "")[:16]})

        tag = max(first - lead_ms, lower)
        tag = min(tag, first)           # 到这里 lower <= first 恒成立，此 min 不再破坏间隙
        ln["start"] = tag
        ln["end"] = int(ws[-1]["end"])
        ln["duration"] = max(min_word_ms, int(ln["end"]) - tag)
        delta = tag - old_start

        prev_ln, prev_ws, prev_end = ln, ws, int(ws[-1]["end"])

    data["line_axis"] = AXIS_SING
    data["line_axis_shifted_lines"] = shifted
    data["line_axis_warns"] = warns

    leads = [int(ln["words"][0]["start"]) - int(ln["start"])
             for ln in (data.get("lines") or []) if ln.get("words")]
    p50 = sorted(leads)[len(leads) // 2] if leads else 0
    msg = f"行轴已校准到起唱点：{len(leads)} 行，lead p50={p50}ms，后推 {shifted} 行"
    try:
        print(f"   📐 {msg}")
    except UnicodeEncodeError:
        print(f"   [axis] {msg}")
    return data


def check_line_axis(data: Dict[str, Any], tol_ms: int = TOL_MS):
    """输出期断言：(是否全部合规, 违规行)。合规：`-tol ≤ 行标签 − 首字 ≤ 0`。"""
    bad = []
    for ln in (data.get("lines") or []):
        ws = ln.get("words") or []
        if not ws or not isinstance(ln.get("start"), int):
            continue
        d = int(ln["start"]) - int(ws[0]["start"])
        if not (-tol_ms <= d <= 0):
            bad.append(ln)
    return (not bad), bad


__all__ = [
    "AXIS_DISPLAY", "AXIS_SING", "AXIS_OFFICIAL", "AXIS_UNKNOWN",
    "mark_line_axis", "infer_from_words", "calibrate_line_axis", "check_line_axis",
]
