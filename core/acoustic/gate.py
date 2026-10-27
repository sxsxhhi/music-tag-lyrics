# -*- coding: utf-8 -*-
"""逐行合理性闸 + 整行回退（v1.6.0）。

为什么必需
----------
任何对齐引擎都有崩行。实测不加闸时出现过 **-303ms** 与 **27.8s** 的畸形字；
而"长字吞停顿"（>700ms 且字内含 ≥250ms 静音）是抢跑/卡字最稳定的物理指纹。

判据（v2.2 定稿）
-----------------
1. 字宽：`WIDEST_HARD_MS=1600` 无条件拦；>1200ms 只有「字内停顿 ≥250ms」才拦。
   —— 实测真实长音 1456/1537/1455ms（停顿 60–160ms）必须放行，
      而 2306ms「好」（停顿 600ms）必须拦住；取 1500 会误杀 1537。
2. 行跨度：`max(2000ms, 字数 × 800ms)`。
3. 位移类：与旧值/锚点差 >600ms 时，才要求「分数不低」且「新起点确有起振」。
   短句（≤2 字）放行；旧值缺失（行级源）用 anchor 兜底，两者都缺则跳过。

单位：本模块内部一律**毫秒**；与 `qc.Env` 交界处显式 ÷1000。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

from .qc import MIN_WORD_MS, LINE_GAP_MS

WIDEST_SOFT_MS = 1200
WIDEST_HARD_MS = 1600
LONG_DIP_MS = 250
DELTA_GATE_MS = 600
SCORE_GATE = -9.0
NEAR_ONSET_MS = 150
SHORT_LINE_CHARS = 2


def _words_chars(words: Sequence[Dict[str, Any]]) -> int:
    return sum(len(str(w.get("text") or "").replace(" ", "")) for w in words)


def judge_line(
    new_words: Sequence[Dict[str, Any]],
    old_words: Sequence[Dict[str, Any]],
    score: Optional[float],
    n_chars: int,
    onsets: Optional[Sequence[float]] = None,
    env: Any = None,
    anchor_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    """逐行合理性闸。返回 (是否合法, 原因)。任何一路判死 → 整行回退。"""
    if not new_words:
        return False, "空行"

    # ① 字数守恒（丢词/多词，含非汉字挂接失败）
    if _words_chars(new_words) != n_chars:
        return False, "字数不符"

    span = new_words[-1]["end"] - new_words[0]["start"]
    pool = [w for w in new_words if not w.get("no_ctc")] or list(new_words)
    widest = max(pool, key=lambda w: w["end"] - w["start"])
    wid = int(widest["end"] - widest["start"])

    # ② 字宽：先看「吞停顿」（更有诊断价值：2306ms/停顿 600ms → 明示吞停顿），
    #    再看硬上限（2306ms/连续能量 → 字宽>1.6s）。两路都会拦，只是理由不同。
    #    真实长音 1456/1537/1455ms（停顿 60–160ms）→ 放行，这正是 1600 而非 1500 的原因。
    if wid > WIDEST_SOFT_MS:
        if env is None:
            return False, "字宽>%.1fs(无包络无法豁免)" % (WIDEST_SOFT_MS / 1000.0)
        dip = env.max_dip_ms(widest["start"] / 1000.0, widest["end"] / 1000.0)
        if dip >= LONG_DIP_MS:
            return False, "长字吞停顿(%dms)" % wid
    if wid > WIDEST_HARD_MS:
        return False, "字宽>%.1fs" % (WIDEST_HARD_MS / 1000.0)

    if span > max(2000, n_chars * 800):
        return False, "行跨度异常"

    # ③ 位移类：只在「有参照 + 位移大 + 非短句」时才判
    ref = old_words[0]["start"] if old_words else anchor_ms
    if ref is not None and n_chars > SHORT_LINE_CHARS:
        delta = abs(int(new_words[0]["start"]) - int(ref))
        if delta > DELTA_GATE_MS:
            if score is not None and score < SCORE_GATE:
                return False, "分数低"
            if onsets is not None:
                hit = any(abs(int(o * 1000) - new_words[0]["start"]) <= NEAR_ONSET_MS for o in onsets)
                if not hit:
                    return False, "无起振"
            elif env is not None and not env.near_onset(new_words[0]["start"], NEAR_ONSET_MS):
                return False, "无起振"
    return True, "ok"


# ---------------------------------------------------------------------------
def snapshot_words(line: Dict[str, Any]) -> Dict[str, Any]:
    """保存行的时间轴快照（回退用）。"""
    return {
        "start": line.get("start"),
        "end": line.get("end"),
        "duration": line.get("duration"),
        "words": [dict(w) for w in (line.get("words") or [])],
    }


def restore_words(line: Dict[str, Any], snap: Dict[str, Any]) -> Dict[str, Any]:
    """整行还原（不是局部缝补）——避免"改一半"造成新的不单调。"""
    if not snap:
        return line
    for key in ("start", "end", "duration"):
        if snap.get(key) is not None:
            line[key] = snap[key]
    line["words"] = [dict(w) for w in (snap.get("words") or [])]
    return line


def split_display_units(text: str) -> List[str]:
    """把一行切成展示单元（汉字逐字、拉丁词整体、标点并入前一词）。"""
    from core.acoustic_aligner import split_orig_tokens  # 延迟导入，避免循环依赖

    if not (text or "").strip():
        return []
    _match, out_toks = split_orig_tokens(text)
    units: List[str] = []
    for tok in out_toks:
        if not tok:
            continue
        pure_punct = all((not ch.isalnum()) for ch in tok)
        if pure_punct and units:
            units[-1] = units[-1] + tok          # 标点并入前一词（与既有产物一致）
        else:
            units.append(tok)
    # 兜底：整行都是标点时也要有输出
    if not units and text.strip():
        units = [text.strip()]
    return units


def linear_split_words(text: str, start_ms: int, end_ms: int,
                       min_word_ms: int = MIN_WORD_MS) -> List[Dict[str, Any]]:
    """按行区间均分合成逐字（保底用；不是声学）。"""
    units = split_display_units(text)
    if not units:
        return []
    st, ed = int(start_ms), int(max(start_ms + min_word_ms, end_ms))
    n = len(units)
    step = max(min_word_ms, (ed - st) // max(1, n))
    words: List[Dict[str, Any]] = []
    for i, u in enumerate(units):
        s = st + i * step
        e = ed if i == n - 1 else min(ed, s + step)
        e = max(e, s + min_word_ms)
        words.append({"text": u, "start": s, "end": e, "duration": e - s})
    # 末字回收到区间内
    if words:
        words[-1]["end"] = max(words[-1]["start"] + min_word_ms, min(ed, words[-1]["end"]))
        words[-1]["duration"] = words[-1]["end"] - words[-1]["start"]
    return words


def apply_gate(
    lines: List[Dict[str, Any]],
    *,
    env: Any = None,
    scores: Optional[Sequence[Optional[float]]] = None,
    anchors: Optional[Sequence[Optional[int]]] = None,
    old_words: Optional[Sequence[Sequence[Dict[str, Any]]]] = None,
    next_starts: Optional[Sequence[Optional[int]]] = None,
    min_word_ms: int = MIN_WORD_MS,
    gap_ms: int = LINE_GAP_MS,
) -> Dict[str, Any]:
    """对已写入 words 的行逐行过闸；不合格整行回退（旧值优先，否则线性保底）。

    返回 {"reverts": [...], "fallback_lines": [...], "kept": n}
    """
    onsets: Optional[List[float]] = None
    if env is not None:
        try:
            onsets = env.onsets(0.0, env.duration_s)
        except Exception:
            onsets = None

    reverts: List[Dict[str, Any]] = []
    fallback_lines: List[int] = []
    kept = 0

    for i, ln in enumerate(lines):
        words = ln.get("words") or []
        if not words:
            continue
        n_chars = _words_chars(words)
        score = scores[i] if scores and i < len(scores) else None
        anchor = anchors[i] if anchors and i < len(anchors) else None
        old = (old_words[i] if old_words and i < len(old_words) else []) or []

        ok, reason = judge_line(words, old, score, n_chars, onsets=onsets, env=env, anchor_ms=anchor)
        if ok:
            kept += 1
            continue

        # 回退：旧值优先
        if old:
            ln["words"] = [dict(w) for w in old]
            ln["start"] = ln["words"][0]["start"]
            ln["end"] = ln["words"][-1]["end"]
            ln["duration"] = max(min_word_ms, ln["end"] - ln["start"])
        else:
            st = int(ln.get("start") or (anchor or 0))
            nxt = next_starts[i] if next_starts and i < len(next_starts) else None
            cur_end = ln.get("end")
            if nxt:
                ed = int(nxt - gap_ms)
            elif isinstance(cur_end, int) and cur_end > st:
                ed = cur_end        # 沿用行当前跨度（通常已是合理区间），只重排字
            else:
                ed = st + max(min_word_ms * len(split_display_units(ln.get("text") or "")),
                              min_word_ms * 2)
            ln["words"] = linear_split_words(ln.get("text") or "", st, ed, min_word_ms=min_word_ms)
            if ln["words"]:
                ln["end"] = ln["words"][-1]["end"]
                ln["duration"] = max(min_word_ms, ln["end"] - ln["start"])
            fallback_lines.append(i)

        reverts.append({
            "line": i,
            "text": (ln.get("text") or "")[:20],
            "reason": reason,
            "score": round(score, 2) if isinstance(score, (int, float)) else None,
            "fallback": not bool(old),
        })

    return {"reverts": reverts, "fallback_lines": fallback_lines, "kept": kept}
