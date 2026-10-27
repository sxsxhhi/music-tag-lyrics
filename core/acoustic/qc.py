# -*- coding: utf-8 -*-
"""质检器：包络/起振/收音（Env）+ L1 单调 + L2 节奏 + L3 自适应窗口复检。

设计约束
--------
1. **只依赖 numpy**，模块顶层**不 import torch** —— 因此可在无 GPU、无模型的
   CI 上对现成 LRC 跑 L1/L2 与静态检查。
2. L3 需要真实对齐，用**注入回调**（`aligner(t0_s, t1_s, text) -> float|None`）
   实现，模块本身不持有模型。
3. 单位约定：**Env 一律秒；上层词/行时间一律毫秒**。跨层调用处显式换算。

实测依据（2026-10-27，双样本）
------------------------------
· 起振密度 ≈ 字数 2 倍（667 vs 363），所以起振只用于「附近是否存在」判定，
  绝不做 1:1 分配。
· 字内最长停顿能把「真实长音」（1456/1537/1455ms，停顿 60–160ms）与
  「吞停顿」（2306ms，停顿 600ms）完全分开 —— 阈值取 250ms。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# ---- 常量（唯一出处：gate / zh / canto 一律引用这里，避免第二套阈值）----
HOP_S = 0.02                 # 包络帧长 20ms（与既有 rms20 缓存一致）
MIN_WORD_MS = 50             # 单字下限
LINE_GAP_MS = 50             # 行间最小间隙
ONSET_THR = 0.012            # 起振上升沿最小幅度
ONSET_MIN_DIST_S = 0.10      # 起振最小间隔
ONSET_VOICED_RATIO = 0.80    # 起振后 4 帧能量须 ≥ 峰值的 80%
SILENT_MS = 180              # 收音判定：连续静音时长
DIP_REL = 0.35               # 停顿判定：低于峰值 35%
DIP_FLOOR = 0.010            # 停顿判定绝对地板
DIP_MIN_FRAMES = 10          # 区间过短（<200ms）不做停顿判定

# L2 只告警，不判死
L2_RATE_MIN = 80.0
L2_RATE_MAX = 430.0
L2_SHORT_LINE_MIN = 60.0
L2_HOLD_LINE_MAX = 600.0


class Env:
    """人声（或混音）RMS 包络。构造后所有接口**入参出参均为秒**。"""

    def __init__(self, rms: "np.ndarray", hop_s: float = HOP_S) -> None:
        self.rms = np.asarray(rms, dtype=np.float32)
        self.hop = float(hop_s)
        # 60ms 平滑（3 帧），抑制单帧毛刺
        self.smooth = (
            np.convolve(self.rms, np.ones(3) / 3.0, mode="same")
            if len(self.rms) >= 3 else self.rms.copy()
        )
        self.diff = np.diff(self.smooth, prepend=self.smooth[0]) if len(self.smooth) else self.smooth

    # -- 构造 ---------------------------------------------------------
    @classmethod
    def from_vocals(cls, v_mono: "np.ndarray", sr: int, hop_s: float = HOP_S) -> "Env":
        """从 44.1k/16k 单声道人声数组构造包络。"""
        v = np.asarray(v_mono, dtype=np.float32)
        hop = max(1, int(round(sr * hop_s)))
        n = len(v) // hop
        if n <= 0:
            return cls(np.zeros(1, dtype=np.float32), hop_s)
        rms = np.sqrt((v[: n * hop].reshape(n, hop) ** 2).mean(axis=1))
        return cls(rms, hop_s)

    @classmethod
    def from_wav(cls, audio_path: str, hop_s: float = HOP_S) -> "Env":
        """从音频文件直接构造（读混音，不做 Demucs；供 CLI 使用）。"""
        import soundfile as sf

        info = sf.info(audio_path)
        data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
        del info
        return cls.from_vocals(mono, sr, hop_s)

    # -- 查询 ---------------------------------------------------------
    @property
    def duration_s(self) -> float:
        return len(self.rms) * self.hop

    def frame_of(self, t_s: float) -> int:
        return int(max(0.0, t_s) / self.hop)

    def energy(self, t_s: float, win_s: float = 0.06) -> float:
        i = self.frame_of(t_s)
        j = min(len(self.rms), i + max(1, int(round(win_s / self.hop))))
        if j <= i:
            return 0.0
        return float(self.rms[i:j].mean())

    def max_dip_ms(self, t0_s: float, t1_s: float,
                   rel: float = DIP_REL, floor: float = DIP_FLOOR) -> int:
        """区间内最长「低于阈值」的连续段（ms）。判「吞停顿」用。"""
        i0, i1 = self.frame_of(t0_s), min(len(self.rms), self.frame_of(max(t0_s, t1_s)))
        seg = self.rms[i0:i1]
        if len(seg) < DIP_MIN_FRAMES:
            return 0
        thr = max(floor, rel * float(np.percentile(seg, 90)))
        run = best = 0
        for v in seg > thr:
            run = 0 if v else run + 1
            if run > best:
                best = run
        return int(best * self.hop * 1000)

    def onsets(self, t0_s: float = 0.0, t1_s: Optional[float] = None,
               thr: float = ONSET_THR, min_dist_s: float = ONSET_MIN_DIST_S) -> List[float]:
        """能量上升沿（起振）列表，秒。密度约为字数 2 倍，只用于「附近有/无」。"""
        i0 = self.frame_of(t0_s)
        i1 = len(self.rms) if t1_s is None else min(len(self.rms), self.frame_of(t1_s))
        out: List[float] = []
        if i1 - i0 < 4:
            return out
        for k in range(max(1, i0 + 1), i1 - 1):
            dk = float(self.diff[k])
            if dk <= 0 or dk < self.diff[k - 1] or dk < self.diff[k + 1] or dk < thr:
                continue
            nxt = self.smooth[k:k + 4]
            if len(nxt) == 0 or float(nxt.mean()) < max(DIP_FLOOR, float(self.smooth[k]) * ONSET_VOICED_RATIO):
                continue
            t = k * self.hop
            if out and t - out[-1] < min_dist_s:
                continue
            out.append(t)
        return out

    def near_onset(self, t_ms: float, span_ms: int = 150) -> bool:
        """t_ms 附近（±span_ms）是否存在起振。"""
        t = t_ms / 1000.0
        return bool(self.onsets(max(0.0, t - span_ms / 1000.0), t + span_ms / 1000.0))

    def release_end(self, t0_s: float, cap_s: float, silent_ms: int = SILENT_MS) -> float:
        """从 t0 起，返回「连续 silent_ms 静音」之前的真实收音点（秒）。

        兜底：找不到静音时返回 min(cap, t0+0.6)。下界保证 ≥ t0 + MIN_WORD_MS。
        """
        floor_s = t0_s + MIN_WORD_MS / 1000.0
        cap_s = max(floor_s, float(cap_s))
        i0 = self.frame_of(t0_s)
        i1 = min(len(self.smooth), self.frame_of(cap_s))
        if i1 - i0 < 3:
            return min(cap_s, max(floor_s, t0_s + 0.6))
        seg = self.smooth[i0:i1]
        peak = float(seg.max()) if len(seg) else 0.0
        thr = max(DIP_FLOOR, 0.35 * peak)
        silent = 0
        need = max(1, int(round(silent_ms / (self.hop * 1000))))
        for k, v in enumerate(seg):
            if v < thr:
                silent += 1
                if silent >= need:
                    return max(floor_s, (i0 + k - silent + 1) * self.hop)
            else:
                silent = 0
        return min(cap_s, max(floor_s, t0_s + 0.6))


# ---------------------------------------------------------------------------
# L1 单调性（自动修复）
# ---------------------------------------------------------------------------
def qc_l1_monotonic(data: Dict[str, Any], gap_ms: int = LINE_GAP_MS,
                    min_word_ms: int = MIN_WORD_MS) -> Dict[str, Any]:
    """就地修复字/行单调性，返回 data。违规数写入 data["l1_violations"]。

    规则（顺序不可换）：
      1. 行内字起点严格递增；重叠时**优先压前一词**（保后词起点），压到 50ms 下限为止
      2. 每个字宽 ≥ min_word_ms（不足则向后补，允许与后词短暂重叠，由 1 再收敛）
      3. 行首不早于上一行末字 + gap_ms（整行平移，保内部节奏）
      4. 恒等式：line.end == words[-1].end
    """
    lines = data.get("lines") or []
    violations = 0
    prev_end: Optional[int] = None

    for ln in lines:
        ws = [w for w in (ln.get("words") or []) if isinstance(w.get("start"), int)
              and isinstance(w.get("end"), int)]
        if not ws:
            continue

        # 1) 行内单调 + 最小字宽
        for k in range(1, len(ws)):
            if ws[k]["start"] < ws[k - 1]["end"]:
                violations += 1
                ws[k - 1]["end"] = max(ws[k - 1]["start"] + min_word_ms, ws[k]["start"])
                if ws[k - 1]["end"] > ws[k]["start"]:
                    ws[k]["start"] = ws[k - 1]["end"]
        for w in ws:
            if w["end"] - w["start"] < min_word_ms:
                violations += 1
                w["end"] = w["start"] + min_word_ms
        for k in range(1, len(ws)):
            if ws[k]["start"] < ws[k - 1]["end"]:
                ws[k]["start"] = ws[k - 1]["end"]
                if ws[k]["end"] < ws[k]["start"] + min_word_ms:
                    ws[k]["end"] = ws[k]["start"] + min_word_ms

        # 3) 行间间隙（整行平移）
        if prev_end is not None and ws[0]["start"] < prev_end + gap_ms:
            delta = prev_end + gap_ms - ws[0]["start"]
            violations += 1
            for w in ws:
                w["start"] += delta
                w["end"] += delta
            if isinstance(ln.get("start"), int):
                ln["start"] += delta

        # 4) 恒等式
        if isinstance(ln.get("start"), int) and ln["start"] > ws[0]["start"]:
            ln["start"] = ws[0]["start"]
        ln["end"] = ws[-1]["end"]
        if isinstance(ln.get("start"), int):
            ln["duration"] = max(min_word_ms, ln["end"] - ln["start"])
        prev_end = ws[-1]["end"]

    data["l1_violations"] = violations
    return data


# ---------------------------------------------------------------------------
# L2 节奏合理性（只告警）
# ---------------------------------------------------------------------------
def qc_l2_rhythm(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """逐行节奏体检。返回告警列表（不修改数据、不判死）。

    rate = (末字end − 首字start) / 字数
    硬区间会大量假阳性（真实快嘴 122ms/字、真实长音 1.4s），故仅用于**分诊**。
    """
    out: List[Dict[str, Any]] = []
    for i, ln in enumerate(data.get("lines") or []):
        ws = [w for w in (ln.get("words") or []) if not w.get("no_ctc")]
        if len(ws) < 1:
            continue
        n_chars = sum(len(str(w.get("text") or "").replace(" ", "")) for w in ws) or len(ws)
        span = ws[-1]["end"] - ws[0]["start"]
        if span <= 0:
            out.append({"line": i, "text": (ln.get("text") or "")[:16], "rate_ms": 0, "reason": "zero_span"})
            continue
        rate = span / n_chars
        has_hold = any((w["end"] - w["start"]) <= 0 for w in ws)
        if len(ws) <= 2 and rate < L2_SHORT_LINE_MIN:
            out.append({"line": i, "text": (ln.get("text") or "")[:16],
                        "rate_ms": round(rate, 1), "reason": "short_line"})
        elif rate < L2_RATE_MIN:
            out.append({"line": i, "text": (ln.get("text") or "")[:16],
                        "rate_ms": round(rate, 1), "reason": "too_fast"})
        elif rate > L2_RATE_MAX and not has_hold:
            out.append({"line": i, "text": (ln.get("text") or "")[:16],
                        "rate_ms": round(rate, 1), "reason": "too_slow"})
    return out


# ---------------------------------------------------------------------------
# L3 自适应窗口复检（注入回调）
# ---------------------------------------------------------------------------
def qc_l3_recheck(
    line: Dict[str, Any],
    anchor_s: float,
    aligner: Callable[[float, float, str], Optional[float]],
    spread_tol: int = 80,
    offsets_s: Sequence[float] = (0.35, 0.0, -0.35),
    win_s: float = 4.0,
) -> Dict[str, Any]:
    """以「新首字候选锚点」为中心跑三个窗口，看首字是否稳定。

    · 三窗首字离散度 ≤ spread_tol → 判定为真实演唱位置，保留
    · 否则取与 anchor 最接近者，并标记 unstable
    aligner 为注入回调：`aligner(t0_s, t1_s, text) -> 首字秒 | None`
    """
    text = (line.get("text") or "").strip()
    picked: List[Optional[float]] = []
    for off in offsets_s:
        st = max(0.0, anchor_s + off)
        try:
            got = aligner(st, min(st + win_s, st + win_s), text)
        except Exception:
            got = None
        picked.append(got)
    vals = [v for v in picked if v is not None]
    spread = int(round((max(vals) - min(vals)) * 1000)) if len(vals) >= 2 else 0
    keep = bool(vals) and spread <= spread_tol
    chosen = anchor_s
    if not keep and vals:
        chosen = min(vals, key=lambda v: abs(v - anchor_s))
    return {
        "anchor": round(anchor_s, 3),
        "picked": round(chosen, 3),
        "windows": [round(anchor_s + o, 3) for o in offsets_s],
        "spread_ms": spread,
        "verdict": "keep" if keep else "unstable",
    }


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def build_report(
    data: Dict[str, Any],
    *,
    engine: str = "",
    audio: str = "",
    duration_s: Optional[float] = None,
    l2: Optional[List[Dict[str, Any]]] = None,
    l3: Optional[List[Dict[str, Any]]] = None,
    gate: Optional[Dict[str, Any]] = None,
    axis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装 qc_report.json 结构（键名与方案 §4 一致）。"""
    lines = data.get("lines") or []
    words = [w for ln in lines for w in (ln.get("words") or [])]
    lyr = [ln for ln in lines if ln.get("words")]
    leads = [(ln["words"][0]["start"] - ln["start"]) for ln in lyr if isinstance(ln.get("start"), int)]
    g = gate or {}
    return {
        "audio": audio,
        "duration_s": round(float(duration_s), 3) if duration_s else None,
        "engine": engine or data.get("align_engine") or data.get("source") or "",
        "l1_violations": int(data.get("l1_violations", 0) or 0),
        "l2_warnings": l2 if l2 is not None else qc_l2_rhythm(data),
        "l3_checks": l3 or [],
        "gate_reverts": g.get("reverts", []),
        "line_axis": axis or {
            "from": data.get("line_axis"),
            "lead_p50_ms": int(np.median(leads)) if leads else None,
            "max_ms": int(max(leads)) if leads else None,
            "shifted": int(data.get("line_axis_shifted_lines", 0) or 0),
        },
        "summary": {
            "lines": len(lines),
            "chars": sum(len(str(w.get("text") or "")) for w in words),
            "reverted": len(g.get("reverts", [])),
            "kept": max(0, len(lyr) - len(g.get("reverts", []))),
        },
    }


def write_report(path: str, report: Dict[str, Any]) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# CLI（无模型：只跑 L1/L2 + 静态检查）
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.acoustic.qc",
                                 description="对现成 LRC 做只读质检（不需要 GPU/模型）")
    ap.add_argument("--lrc", required=True, help="待检 LRC 路径")
    ap.add_argument("--audio", default="", help="音频路径（可选，用于包络类检查）")
    ap.add_argument("--json", dest="json_path", default="", help="写出 qc_report.json")
    ap.add_argument("--gap-ms", type=int, default=LINE_GAP_MS)
    ap.add_argument("--min-word-ms", type=int, default=MIN_WORD_MS)
    args = ap.parse_args(argv)

    from core.parsers.lrc_parser import parse_lrc
    from core.utils.io_safe import read_text_guess_encoding

    data = parse_lrc(read_text_guess_encoding(args.lrc))
    if not isinstance(data, dict):
        data = {"lines": data or []}

    dur = None
    if args.audio:
        try:
            env = Env.from_wav(args.audio)
            dur = env.duration_s
        except Exception as e:      # 音频读不到不影响 L1/L2
            print(f"⚠️ 音频不可读，跳过包络检查：{e}")

    qc_l1_monotonic(data, gap_ms=args.gap_ms, min_word_ms=args.min_word_ms)
    rep = build_report(data, audio=args.audio, duration_s=dur, engine="static")
    if args.json_path:
        write_report(args.json_path, rep)
        print(f"📄 报告已写出：{args.json_path}")
    print(f"L1 违规 {rep['l1_violations']} · L2 告警 {len(rep['l2_warnings'])} · "
          f"行 {rep['summary']['lines']} · 字 {rep['summary']['chars']}")
    return 0 if rep["l1_violations"] == 0 else 1


if __name__ == "__main__":      # pragma: no cover
    raise SystemExit(main())
