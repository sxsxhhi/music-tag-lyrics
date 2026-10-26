# -*- coding: utf-8 -*-
"""声学对齐质量门禁与记账（v1.5.1-B）。

为什么单独成模块：
  · 门禁必须能被离线测试**正反双向**断言（无 torch 的 CI 也要能跑），
    所以不能被 `acoustic_canto → acoustic_aligner` 的模块级 import 链拖进 torch；
  · `_MIN_CHAR_S` / `_LINE_GAP_MS` 等常量原先在 acoustic_canto / acoustic_aligner /
    lyric_validator 三处各写一份，已经漂移，这里集中一处。

设计要点（对应审查结论）：
  · **双层门禁** —— 整曲宽门限防「整体不可用」，分行严门限把个别低质量行降级，
    一段说唱桥段不再拖垮整首；
  · **双分母** —— `scores` 只统计真实对齐分（避免伪时间稀释均值），
    同时用 `unaligned_ratio`（分母取 n_target）单独拦「大量音节无跨度」；
  · **熔断用异常而非返回值** —— 返回 dict 会重演「忘了检查」的老路；
  · 所有阈值可用 `MTL_GATE_*` 环境变量覆盖，`MTL_GATE_MODE=off` 一键回 v1.5.0 行为。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── 三处共用常量（其余模块以别名引用，保持既有 import 不破）──────────────────
LINE_GAP_MS = 50          # 行间最小间隙（铁律 19）
MIN_WORD_MS = 100         # 单字下限（铁律 4）
MIN_CHAR_S = 0.060        # 行内字间最小推进

# words_source 取值
SRC_GPU_ACOUSTIC = "gpu_acoustic"
SRC_GPU_LINEAR = "gpu_linear_fallback"
SRC_NUMPY = "numpy_synth"
SRC_GATE_DEMOTED = "gate_demoted"
SRC_INTERPOLATED = "interpolated"


# ── 三态 ───────────────────────────────────────────────────────────────────
class AlignStatus(Enum):
    """对齐结果三态。core 层抛异常，scripts 层转成这三态分流。"""

    OK = "ok"                        # 成功
    LOW_QUALITY = "low_quality"      # 熔断：可换引擎重试
    UNAVAILABLE = "unavailable"      # 环境/参数失败：重试无用


class AcousticQualityError(RuntimeError):
    """声学质量熔断（与环境错误 AcousticEnvironmentError 严格区分，不继承它）。"""

    def __init__(self, reasons: Sequence[str], stats: "TrackStats", engine: str = "") -> None:
        self.reasons = list(reasons)
        self.stats = stats
        self.engine = engine
        super().__init__(f"[{engine or 'acoustic'}] 质量熔断: {', '.join(self.reasons)}")


# ── 配置 ───────────────────────────────────────────────────────────────────
@dataclass
class GateConfig:
    """质量门禁阈值。全部可用 MTL_GATE_* 环境变量覆盖。

    v1.5.1-R5 实测标定（本机 GPU，两曲双样本）：

    | 维度      | 焚情(正) | 那么骄傲强入yue(负) | 定值   |
    |-----------|---------|--------------------|--------|
    | mean      | -1.936  | -8.279             | -4.00  |
    | median    | -0.582  | -8.308             | -3.50  |
    | p10       | -5.394  | -14.404            | -7.00  |
    | unaligned |  0.000  |  0.000             |  0.35  |
    | span      |  0.772  |  0.875             |  0.35  |
    | demote    |  0.156  |  0.704             |  0.60  |

    初稿把 p10 定在 -5.00，实测直接把《焚情》误杀（p10=-5.394），
    正是「凭单样本猜阈值」会踩的坑。定值原则：距正样本 ≥1.6、距负样本 ≥2.0。
    """

    mode: str = "strict"                 # strict | warn | off
    # 整曲（宽）
    track_mean_min: float = -4.00
    track_median_min: float = -3.50
    track_p10_min: float = -7.00
    track_unaligned_max: float = 0.35
    track_span_min: float = 0.35
    track_min_scored: int = 30
    # 分行（严于整曲：一行要差到远低于曲目均值才整行降级）
    # R5 实测：-3.50 会把《焚情》32 行里 5 行（15.6%）降级成行级，超验收线 10%；
    # 放宽到 -5.00 —— 分行门限管的是「这一行彻底废了」（说唱桥段/念白），
    # 不是「这一行比平均差一点」。-5.00 仍严于 track_mean_min 的 -4.00 一档。
    line_mean_min: float = -5.00
    line_coverage_min: float = 0.50
    line_min_chars: int = 6
    # 分行降级比例超过此值 → 升级为整曲熔断（防「全部降级」了事）
    demote_abort: float = 0.60

    @property
    def enabled(self) -> bool:
        return self.mode.lower() != "off"

    @property
    def enforce(self) -> bool:
        return self.mode.lower() == "strict"


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key, "").strip()
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        logger.warning("  [gate] %s=%r 不是合法数字，回退默认 %s", key, v, default)
        return default


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key, "").strip()
    if not v:
        return default
    try:
        return int(v)
    except ValueError:
        logger.warning("  [gate] %s=%r 不是合法整数，回退默认 %s", key, v, default)
        return default


def load_gate_config() -> GateConfig:
    """读取 MTL_GATE_* 覆盖项。每次调用都重新读，便于测试 monkeypatch。"""
    cfg = GateConfig()
    cfg.mode = os.environ.get("MTL_GATE_MODE", cfg.mode).strip() or cfg.mode
    cfg.track_mean_min = _env_float("MTL_GATE_TRACK_MEAN", cfg.track_mean_min)
    cfg.track_median_min = _env_float("MTL_GATE_TRACK_MEDIAN", cfg.track_median_min)
    cfg.track_p10_min = _env_float("MTL_GATE_TRACK_P10", cfg.track_p10_min)
    cfg.track_unaligned_max = _env_float("MTL_GATE_TRACK_UNALIGNED", cfg.track_unaligned_max)
    cfg.track_span_min = _env_float("MTL_GATE_TRACK_SPAN", cfg.track_span_min)
    cfg.track_min_scored = _env_int("MTL_GATE_TRACK_MIN_SCORED", cfg.track_min_scored)
    cfg.line_mean_min = _env_float("MTL_GATE_LINE_MEAN", cfg.line_mean_min)
    cfg.line_coverage_min = _env_float("MTL_GATE_LINE_COVERAGE", cfg.line_coverage_min)
    cfg.line_min_chars = _env_int("MTL_GATE_LINE_MIN_CHARS", cfg.line_min_chars)
    cfg.demote_abort = _env_float("MTL_GATE_DEMOTE_ABORT", cfg.demote_abort)
    return cfg


# ── 统计量 ─────────────────────────────────────────────────────────────────
@dataclass
class TrackStats:
    n_target: int = 0
    n_target_unaligned: int = 0
    n_scored: int = 0
    mean: float = -99.0
    med: float = -99.0
    p10: float = -99.0
    unaligned_ratio: float = 0.0
    span_ratio: float = 0.0
    demote_ratio: float = 0.0
    n_lines_evaluable: int = 0
    n_lines_demoted: int = 0
    first_start_s: float = 0.0
    last_end_s: float = 0.0
    hints_span_s: Optional[float] = None

    def summary(self) -> str:
        return (
            f"mean={self.mean:.3f} median={self.med:.3f} p10={self.p10:.3f} "
            f"unaligned={self.unaligned_ratio:.3f} span={self.span_ratio:.3f} "
            f"demote={self.demote_ratio:.3f} ({self.n_lines_demoted}/{self.n_lines_evaluable} 行) "
            f"scored={self.n_scored}/{self.n_target}"
        )


def _percentile(values: List[float], q: float) -> float:
    """最小实现的分位数（避免依赖 numpy）。q ∈ [0, 1]。"""
    if not values:
        return -99.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    idx = q * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def compute_track_stats(
    lines: List[Dict[str, Any]],
    total_audio_s: float,
    n_target: Optional[int] = None,
    n_target_unaligned: Optional[int] = None,
    demote_ratio: float = 0.0,
    n_lines_evaluable: int = 0,
    n_lines_demoted: int = 0,
    hints_span_s: Optional[float] = None,
) -> TrackStats:
    """汇总整曲统计。

    分母口径：`n_target` 是 CTC target 音节数（拉丁词不是 target，不进分母）；
    未传时退化为「所有词数」。
    """
    all_words = [w for ln in lines for w in (ln.get("words") or [])]

    # scores：只收真实对齐的负分。旧版把 `score is None` 的伪时间排除在外，
    # 结果「大面积伪时间」反而最不容易触发熔断——现在用 unaligned_ratio 单独拦。
    scores = [
        float(w["score"]) for w in all_words
        if w.get("score") is not None and float(w["score"]) < 0
    ]

    if n_target is None:
        n_target = len(all_words)
    if n_target_unaligned is None:
        n_target_unaligned = sum(1 for w in all_words if w.get("score") is None)

    first_start_s = 0.0
    last_end_s = 0.0
    for ln in lines:
        st = ln.get("start")
        if isinstance(st, (int, float)):
            first_start_s = st / 1000.0
            break
    for ln in reversed(lines):
        ed = ln.get("end")
        if isinstance(ed, (int, float)):
            last_end_s = ed / 1000.0
            break

    return TrackStats(
        n_target=int(n_target),
        n_target_unaligned=int(n_target_unaligned),
        n_scored=len(scores),
        mean=(sum(scores) / len(scores)) if scores else -99.0,
        med=median(scores) if scores else -99.0,
        p10=_percentile(scores, 0.10) if scores else -99.0,
        unaligned_ratio=(n_target_unaligned / n_target) if n_target else 0.0,
        span_ratio=(last_end_s - first_start_s) / max(1e-6, float(total_audio_s)),
        demote_ratio=float(demote_ratio),
        n_lines_evaluable=int(n_lines_evaluable),
        n_lines_demoted=int(n_lines_demoted),
        first_start_s=first_start_s,
        last_end_s=last_end_s,
        hints_span_s=hints_span_s,
    )


# ── 整曲门禁 ───────────────────────────────────────────────────────────────
def evaluate_track_gate(stats: TrackStats, cfg: Optional[GateConfig] = None) -> List[str]:
    """返回熔断理由列表；空列表 = 通过。"""
    cfg = cfg or load_gate_config()
    if not cfg.enabled:
        return []
    reasons: List[str] = []

    # 维度 A：样本量不足时不看均值（<30 样本的均值无统计意义）
    if stats.n_scored >= cfg.track_min_scored:
        if stats.mean < cfg.track_mean_min:
            reasons.append(f"R_TRACK_MEAN({stats.mean:.3f} < {cfg.track_mean_min})")
        if stats.med < cfg.track_median_min:
            reasons.append(f"R_TRACK_MEDIAN({stats.med:.3f} < {cfg.track_median_min})")
        if stats.p10 < cfg.track_p10_min:
            reasons.append(f"R_TRACK_P10({stats.p10:.3f} < {cfg.track_p10_min})")

    # 维度 B：大量音节无跨度 = 错配/音频不符
    if stats.unaligned_ratio > cfg.track_unaligned_max:
        reasons.append(
            f"R_TRACK_UNALIGNED({stats.unaligned_ratio:.3f} > {cfg.track_unaligned_max})"
        )

    # 维度 C：时长覆盖（引入智能豁免）
    span_exempt = False
    if stats.hints_span_s and stats.hints_span_s > 0:
        # 有提示窗：只考核与参考歌词跨度的相对吻合率
        relative_cov = max(0.0, stats.last_end_s - stats.first_start_s) / stats.hints_span_s
        if relative_cov >= 0.70:
            span_exempt = True
    elif stats.n_scored >= 20 and stats.mean >= -3.00 and stats.demote_ratio == 0:
        # 高置信度、零降级集中演唱曲风：豁免时长占比门限
        span_exempt = True

    if not span_exempt and stats.span_ratio < cfg.track_span_min:
        reasons.append(f"R_TRACK_SPAN({stats.span_ratio:.3f} < {cfg.track_span_min})")

    # 维度 D：分行降级过多 → 升级为整曲熔断
    if stats.n_lines_evaluable > 0 and stats.demote_ratio > cfg.demote_abort:
        reasons.append(
            f"R_MASS_DEMOTE({stats.demote_ratio:.3f} > {cfg.demote_abort})"
        )

    return reasons


# ── 分行门禁 ───────────────────────────────────────────────────────────────
@dataclass
class LineDecision:
    demote: bool = False
    reasons: List[str] = field(default_factory=list)


def evaluate_line_gate(
    line: Dict[str, Any],
    cfg: Optional[GateConfig] = None,
    n_target_in_line: Optional[int] = None,
) -> LineDecision:
    """分行判定：一行过短/过差时降级为行级（words: []），其余行保留。"""
    cfg = cfg or load_gate_config()
    if not cfg.enabled:
        return LineDecision()

    words = line.get("words") or []
    hanzi = [w for w in words if (w.get("text") or "").strip()]
    n_chars = n_target_in_line if n_target_in_line is not None else len(hanzi)
    if n_chars < cfg.line_min_chars:
        # 3 字行的均值抖动 ±0.6，与门限同量级，判了必误杀
        return LineDecision()

    scores = [
        float(w["score"]) for w in words
        if w.get("score") is not None and float(w["score"]) < 0
    ]
    coverage = (len(scores) / n_chars) if n_chars else 0.0
    reasons: List[str] = []
    if coverage < cfg.line_coverage_min:
        reasons.append(f"R_LINE_COVERAGE({coverage:.3f} < {cfg.line_coverage_min})")
    if scores and (sum(scores) / len(scores)) < cfg.line_mean_min:
        reasons.append(f"R_LINE_MEAN({sum(scores)/len(scores):.3f} < {cfg.line_mean_min})")

    return LineDecision(demote=bool(reasons), reasons=reasons)


# ── 记账：synthesized_words 语义 ───────────────────────────────────────────
def finalize_word_source_flags(
    data: Dict[str, Any],
    fallback_lines: Sequence[int] = (),
    demoted_lines: Sequence[int] = (),
    numpy_lines: Sequence[int] = (),
) -> Dict[str, Any]:
    """写入 words_source / words_source_stats / synthesized_ratio / synthesized_words。

    语义修正（C1）：`synthesized_words` 只在**整首没有任何真声学逐字**时为 True。
    旧版双向失真 —— 线性均分仍报 False（假阴性）、整首只要一行缺词就全标 True（假阳性）。
    """
    lines = data.get("lines") or []
    fb = set(fallback_lines)
    dm = set(demoted_lines)
    nb = set(numpy_lines)

    stats = {SRC_GPU_ACOUSTIC: 0, SRC_GPU_LINEAR: 0, SRC_NUMPY: 0,
             SRC_GATE_DEMOTED: 0, SRC_INTERPOLATED: 0}
    n_total = 0
    for i, ln in enumerate(lines):
        words = ln.get("words") or []
        if i in dm:
            # 质量门禁降级行：虽然 words 被清空，但明确属于降级来源
            ln["words_source"] = SRC_GATE_DEMOTED
            stats[SRC_GATE_DEMOTED] = stats.get(SRC_GATE_DEMOTED, 0) + 1
            n_total += 1
            continue

        if not words:
            # 原生行级歌词（非门禁降级）：不算进合成比例
            ln["words_source"] = "line_level"
            continue

        n_total += 1
        if i in nb:
            src = SRC_NUMPY
        elif i in fb:
            src = SRC_GPU_LINEAR
        elif any(w.get("score") is None for w in words):
            src = SRC_INTERPOLATED
        else:
            src = SRC_GPU_ACOUSTIC
        ln["words_source"] = src
        stats[src] = stats.get(src, 0) + 1

    n_real = stats[SRC_GPU_ACOUSTIC]
    synth = n_total - n_real
    ratio = (synth / n_total) if n_total else 1.0

    data["words_source_stats"] = stats
    data["synthesized_ratio"] = round(ratio, 4)
    data["linear_fallback_lines"] = len(fb)
    data["synthesized_words"] = (n_real == 0)   # 仅当整首没有真声学逐字
    data["acoustic_coverage"] = round((n_real / n_total) if n_total else 0.0, 4)

    logger.debug(
        "  [quality] 记账：%s | synthesized_ratio=%.3f synthesized_words=%s",
        stats, ratio, data["synthesized_words"],
    )
    return data
