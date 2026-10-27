# -*- coding: utf-8 -*-
"""声学流水线 v1.4.0 离线单测：铁律 20 环境门禁、谷底双向锁定、STFT 谱流拆分、
validator 零重叠防御（R2）、converter 链式前奏（B3）、Golden 夹具结构校验。

全部离线（CI 无 torch 可跑；STFT 用例在无 torch 环境自动跳过）。
运行：python tests/test_acoustic_pipeline.py
"""
try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import os
import re
import sys
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np

from core.acoustic_aligner import (
    AcousticEnvironmentError,
    AcousticAligner,
    _check_acoustic_environment,
    compute_rms_timeline,
    resolve_inter_phrase_boundaries,
)

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
GOLDEN_LRC = os.path.join(FIXTURES, "golden_jinhaixin_2000.lrc")
GOLDEN_VALLEYS = (40.87, 53.87, 125.15)


def _torch_available():
    try:
        import torch
        return True
    except ImportError:
        return False


def test_01_env_gate_raises_without_torch():
    """铁律 20：缺 torch / stable_whisper 时必须抛 AcousticEnvironmentError（mock 注入）。"""
    with mock.patch.dict(sys.modules, {"torch": None, "stable_whisper": None}):
        try:
            _check_acoustic_environment("cuda")
        except AcousticEnvironmentError:
            pass
        else:
            raise AssertionError("缺 torch 未拦截")

        # Facade 入口同样必须被门禁拦截（不得触碰音频文件）
        try:
            AcousticAligner.align_lyrics("Z:/not/exist.flac", "测试文本")
        except AcousticEnvironmentError:
            pass
        else:
            raise AssertionError("Facade 未被门禁拦截")


def test_02_env_gate_passes_with_real_cuda():
    """真机 CUDA 可用时门禁放行（CI 无 torch 自动跳过）。"""
    if not _torch_available():
        _testkit.skip("无 torch 环境")
    import torch
    if not torch.cuda.is_available():
        _testkit.skip("无 CUDA")
    _check_acoustic_environment("cuda")  # 不抛即通过


def test_03_facade_signature_backward_compatible():
    """A2：align_lyrics 类方法签名与 process_song 调用面完全兼容。"""
    import inspect
    sig = inspect.signature(AcousticAligner.align_lyrics)
    params = set(sig.parameters)
    assert {"audio_path", "lyrics_text", "line_timing_hints", "device"} <= params, params
    # enrich_track_with_acoustic_words 仍可直接导入（process_song:199 依赖）
    from core.acoustic_aligner import enrich_track_with_acoustic_words
    data = {"lines": [{"start": 0, "duration": 1000, "end": 1000, "text": "两字", "words": []}]}
    out = enrich_track_with_acoustic_words(data)
    assert out["lines"][0]["words"], "numpy 合成路径丢失"


def test_04_valley_synthetic_two_phrases():
    """合成包络：两段人声夹静音谷底 → 双向锁定命中静音区，间隙 ≥50ms。"""
    sr = 44100
    v = np.zeros(int(10.0 * sr), dtype=np.float32)
    # 人声段 1-3s 与 5-8s（RMS 0.1）
    v[int(1.0 * sr):int(3.0 * sr)] = 0.1 * np.sin(
        2 * np.pi * 220 * np.arange(int(3.0 * sr) - int(1.0 * sr)) / sr).astype(np.float32)
    v[int(5.0 * sr):int(8.0 * sr)] = 0.1 * np.sin(
        2 * np.pi * 220 * np.arange(int(8.0 * sr) - int(5.0 * sr)) / sr).astype(np.float32)
    hop_times, hop_rms = compute_rms_timeline(v, sr)
    prev_end, next_start = resolve_inter_phrase_boundaries(
        hop_times, hop_rms, raw_end_prev=3.0, raw_start_next=5.0)
    assert 2.9 <= prev_end <= 3.2, prev_end
    assert 4.8 <= next_start <= 5.1, next_start
    assert (next_start - prev_end) * 1000 >= 50


def test_05_valley_min_gap_enforced():
    """谷底间隙不足 50ms 时中点对半强制保底。"""
    sr = 44100
    v = np.zeros(int(4.0 * sr), dtype=np.float32)
    v[int(0.5 * sr):int(1.9 * sr)] = 0.1
    v[int(1.93 * sr):int(3.5 * sr)] = 0.1  # 间隙仅 30ms
    hop_times, hop_rms = compute_rms_timeline(v, sr)
    prev_end, next_start = resolve_inter_phrase_boundaries(
        hop_times, hop_rms, raw_end_prev=1.9, raw_start_next=1.93)
    assert (next_start - prev_end) * 1000 >= 50 - 1e-6, (prev_end, next_start)


def test_06_flux_split_two_char_token():
    """B6/C2：双字 token 谱流权威切分——合成 0.4s 音频在 0.2s 处有爆破瞬态。"""
    if not _torch_available():
        _testkit.skip("无 torch 环境")
    from core.acoustic_aligner import spectral_flux_split_time
    sr = 44100
    n = int(0.4 * sr)
    sig = 0.05 * np.sin(2 * np.pi * 300 * np.arange(n) / sr).astype(np.float32)
    # 0.2s 处爆破（宽带瞬态）
    burst_at = int(0.2 * sr)
    sig[burst_at:burst_at + int(0.01 * sr)] += 0.6 * np.random.default_rng(7).standard_normal(int(0.01 * sr)).astype(np.float32)
    split = spectral_flux_split_time(sig, sr)
    assert split is not None
    assert abs(split - 0.2) <= 0.04, split


def test_07_golden_fixture_structure():
    """R3：Golden 夹具零行重叠（铁律 19）、行单调、三个谷底被行边界覆盖。"""
    text = open(GOLDEN_LRC, encoding="utf-8").read()
    line_re = re.compile(r"^\[(\d{1,2}):(\d{2}(?:\.\d{1,3})?)\]", re.M)
    starts = []
    for m in line_re.finditer(text):
        starts.append(int(m.group(1)) * 60 + float(m.group(2)))
    body = [t for t in starts if t >= 12.0]  # 排除标题/署名行
    assert len(body) >= 50, len(body)
    assert all(b < a for a, b in zip(body, body[1:])) is False  # 单调递增
    assert all(a < b for a, b in zip(body, body[1:])), "行时间戳非单调"
    for valley in GOLDEN_VALLEYS:
        # 存在一条行起点落在谷底 ±60ms 内（谷底即下一行开嗓间隙）
        near = [t for t in body if abs(t - valley) <= 0.06]
        assert near, f"谷底 {valley}s 附近无行边界"
    # 全曲末行 ≤ 250s（母盘 247.7s）
    assert body[-1] <= 250.0, body[-1]


def test_08_validator_overlap_trim_with_r2():
    """B2/R2：故意重叠 → 前句收拢 + 末字 R2 回移，双约束恒成立。"""
    from scripts.lyric_validator import validate_and_fix_lyrics

    data = {
        "lines": [
            {"start": 1000, "end": 5000, "duration": 4000, "text": "第一句歌词",
             "words": [
                 {"text": "第", "start": 1000, "end": 1600, "duration": 600},
                 {"text": "一", "start": 1600, "end": 2200, "duration": 600},
                 {"text": "句", "start": 2200, "end": 2500, "duration": 300},
                 {"text": "歌", "start": 2500, "end": 3800, "duration": 1300},
                 {"text": "词", "start": 3800, "end": 5000, "duration": 1200},
             ]},
            {"start": 2600, "end": 6000, "duration": 3400, "text": "第二句歌词",
             "words": [
                 {"text": "第", "start": 2600, "end": 3200, "duration": 600},
                 {"text": "二", "start": 3200, "end": 3800, "duration": 600},
                 {"text": "句", "start": 3800, "end": 4400, "duration": 600},
                 {"text": "歌", "start": 4400, "end": 5200, "duration": 800},
                 {"text": "词", "start": 5200, "end": 6000, "duration": 800},
             ]},
        ]
    }
    ok, out, warnings = validate_and_fix_lyrics(data, audio_duration_ms=10000)
    assert ok
    assert any("overlap" in w for w in warnings), warnings
    l1, l2 = out["lines"]
    # trimmed = max(1000+500, 2600-50) = 2550
    assert l1["end"] == 2550, l1["end"]
    last_w = l1["words"][-1]
    # R2: 2550 - 3800 < 100 → start 回移至 2450，end=2550，时长 100
    assert last_w["start"] == 2450, last_w
    assert last_w["end"] == 2550, last_w
    assert last_w["duration"] == 100, last_w
    # 铁律 19：行间隙 ≥50ms
    assert l2["start"] - l1["end"] >= 50


def test_09_converter_chained_prelude():
    """B3：链式前奏——标题行结束+100ms 才轮到作词，作词结束+100ms 才轮到作曲，且不压首唱。"""
    from core.converter import to_spl

    def line(text, start, end):
        n = max(1, len(text))
        step = max(1, (end - start) // n)
        words = [{"text": c, "start": start + i * step, "end": start + (i + 1) * step}
                 for i, c in enumerate(text)]
        return {"start": start, "end": end, "duration": end - start, "text": text, "words": words}

    data = {"tags": {}, "lines": [line("正文甲", 13000, 17000), line("正文乙", 17000, 21000)]}
    out = to_spl(data, title="测试曲", artist="歌手", lyricist="甲", composer="乙")
    rows = [l for l in out.split("\n") if l.strip() and not re.match(r"^\[(ti|ar|al|by|offset):", l)]
    ts = []
    for r in rows:
        m = re.match(r"\[(\d{2}):(\d{2}\.\d{2,3})\]", r)
        assert m, r
        frac = (m.group(2).split(".")[1] + "00")[:3]  # 2/3 位精度统一归一为毫秒
        ts.append(int(m.group(1)) * 60000 + int(m.group(2).split(".")[0]) * 1000 + int(frac))
    title_start, lyricist_start, composer_start, first_start = ts[0], ts[1], ts[2], ts[3]
    assert title_start == 1000
    assert lyricist_start >= 5600, lyricist_start        # ≥ title_end(5500) + 100
    assert composer_start >= lyricist_start + 3000 + 100
    assert composer_start + 3000 + 100 <= first_start    # 不压首唱


def _mk_line(start, text, words=None, dur=3000):
    return {"start": start, "end": start + dur, "duration": dur, "text": text,
            "translation": "", "words": words or []}


def _mk_words(chars, st, step):
    return [{"text": c, "start": st + i * step, "end": st + (i + 1) * step, "duration": step}
            for i, c in enumerate(chars)]


def test_10_validator_zero_overlap_priority():
    """v1.5.1-A2：行间零重叠绝对优先，500ms 行下限让位。

    旧版用 max(last_line_start + 500, line_start - 50) 收拢前一行，当两行间隔
    不足 550ms 时该 max 会反超 line_start - 50，**重新制造**行间重叠
    （实测两行 0/200ms、字 60ms 时残留 350ms）。
    """
    from scripts.lyric_validator import validate_and_fix_lyrics

    cases = {
        "间隔不足 550ms": [_mk_line(0, "abc", _mk_words("abc", 0, 60)),
                          _mk_line(200, "def", _mk_words("def", 200, 60))],
        "两行起点相同": [_mk_line(0, "a", _mk_words("a", 0, 60)),
                        _mk_line(0, "b", _mk_words("b", 0, 60))],
        "三行连挤": [_mk_line(0, "a", _mk_words("a", 0, 60)),
                    _mk_line(100, "b", _mk_words("b", 100, 60)),
                    _mk_line(200, "c", _mk_words("c", 200, 60))],
        "空间充足": [_mk_line(0, "ab", _mk_words("ab", 0, 60)),
                    _mk_line(6000, "cd", _mk_words("cd", 6000, 60))],
    }
    for name, lines in cases.items():
        data = {"tags": {}, "lines": lines}
        ok, out, _w = validate_and_fix_lyrics(data, 0)
        assert ok, name
        rows = out["lines"]
        for i in range(len(rows) - 1):
            assert rows[i]["end"] <= rows[i + 1]["start"], (
                f"{name}: 行 {i+1} end={rows[i]['end']} 越过行 {i+2} start={rows[i+1]['start']}"
            )
        # 恒等式：line.end 必须等于末字 end（to_spl 的 <tend> 取末字 end）
        for i, r in enumerate(rows):
            if r.get("words"):
                assert r["end"] == r["words"][-1]["end"], f"{name}: 行 {i+1} 恒等式破坏"
            # 字级单调 + 无零/负时长
            for k in range(len(r.get("words") or []) - 1):
                assert r["words"][k]["end"] <= r["words"][k + 1]["start"], f"{name}: 字重叠"
            for w in r.get("words") or []:
                assert w["end"] > w["start"] or len(r["words"]) > 1, f"{name}: 零宽字"


def test_11_validator_idempotent():
    """v1.5.1-A2：二次校验必须完全幂等（process_song 会跑两遍），否则时间轴二次漂移。"""
    from scripts.lyric_validator import validate_and_fix_lyrics

    lines = [_mk_line(1000, "第一句歌词",
                      [{"text": "第", "start": 1000, "end": 1600, "duration": 600},
                       {"text": "词", "start": 3800, "end": 5000, "duration": 1200}]),
             _mk_line(2600, "第二句歌词",
                      [{"text": "第", "start": 2600, "end": 3200, "duration": 600},
                       {"text": "词", "start": 5200, "end": 6000, "duration": 800}])]
    _ok, once, _w1 = validate_and_fix_lyrics({"lines": lines}, 0)
    _ok2, twice, w2 = validate_and_fix_lyrics(once, 0)
    assert not w2, f"二次校验仍有告警（非幂等）: {w2}"
    for a, b in zip(once["lines"], twice["lines"]):
        assert a["start"] == b["start"] and a["end"] == b["end"], (a, b)
        for wa, wb in zip(a.get("words") or [], b.get("words") or []):
            assert wa["start"] == wb["start"] and wa["end"] == wb["end"]


def test_12_naturalness_packed_exemption():
    """v1.5.1-N17：贴齐的 60ms 短字是「字距挤出来的结构性压缩」，不是伪时间。

    粤语 CTC 的 _MIN_CHAR_S=60ms，旧口径会把每首粤语快歌都刷成
    「Natural Acoustic Alert」恒噪声，反而掩盖真正的机器伪时间。
    """
    from scripts.lyric_validator import check_acoustic_naturalness

    packed = [{"text": c, "start": i * 60, "end": (i + 1) * 60, "duration": 60}
              for i, c in enumerate("一二三四五六七八九十")]
    healthy, ratio = check_acoustic_naturalness(packed)
    assert healthy, f"贴齐 60ms 被误判为伪时间（ratio={ratio}）"

    gappy = [{"text": c, "start": i * 90, "end": i * 90 + 60, "duration": 60}
             for i, c in enumerate("一二三四五六七八九十")]
    healthy2, _ = check_acoustic_naturalness(gappy)
    assert not healthy2, "留 30ms 空隙的 60ms 短字应仍被判为伪时间"


def test_13_quality_gate_track():
    """v1.5.1-B1 整曲门禁：好分放行、差分熔断、各维度独立生效。"""
    from core.acoustic_quality import GateConfig, TrackStats, evaluate_track_gate

    cfg = GateConfig()
    ok = TrackStats(n_target=200, n_scored=180, mean=-1.886, med=-1.75, p10=-3.0,
                    unaligned_ratio=0.05, span_ratio=0.85, demote_ratio=0.0,
                    n_lines_evaluable=30, n_lines_demoted=0)
    assert evaluate_track_gate(ok, cfg) == [], "焚情量级被误熔断"

    # 那么骄傲量级：必须熔断，且理由里要带 R_TRACK_MEAN
    bad = TrackStats(n_target=200, n_scored=180, mean=-7.785, med=-8.153, p10=-12.0,
                     unaligned_ratio=0.05, span_ratio=0.85, demote_ratio=0.0,
                     n_lines_evaluable=30, n_lines_demoted=0)
    reasons = evaluate_track_gate(bad, cfg)
    assert any("R_TRACK_MEAN" in r for r in reasons), reasons

    # 未对齐过多（伪时间）：均值好看也要熔断
    ua = TrackStats(n_target=200, n_scored=180, mean=-1.5, med=-1.5, p10=-2.0,
                    unaligned_ratio=0.60, span_ratio=0.85, demote_ratio=0.0,
                    n_lines_evaluable=30, n_lines_demoted=0)
    assert any("R_TRACK_UNALIGNED" in r for r in evaluate_track_gate(ua, cfg)), evaluate_track_gate(ua, cfg)

    # 时长覆盖不足（未满足豁免条件：均值 -3.5 < -3.00）
    sp = TrackStats(n_target=200, n_scored=180, mean=-3.5, med=-3.5, p10=-4.0,
                    unaligned_ratio=0.05, span_ratio=0.10, demote_ratio=0.0,
                    n_lines_evaluable=30, n_lines_demoted=0)
    assert any("R_TRACK_SPAN" in r for r in evaluate_track_gate(sp, cfg))

    # 分行降级过多 → 升级为整曲熔断
    md = TrackStats(n_target=200, n_scored=180, mean=-1.5, med=-1.5, p10=-2.0,
                    unaligned_ratio=0.05, span_ratio=0.85, demote_ratio=0.70,
                    n_lines_evaluable=30, n_lines_demoted=21)
    assert any("R_MASS_DEMOTE" in r for r in evaluate_track_gate(md, cfg))

    # 样本不足时跳过均值维度（n_scored < 30）
    small = TrackStats(n_target=8, n_scored=8, mean=-9.0, med=-9.0, p10=-9.0,
                       unaligned_ratio=0.0, span_ratio=0.9, demote_ratio=0.0,
                       n_lines_evaluable=1, n_lines_demoted=0)
    assert evaluate_track_gate(small, cfg) == [], "样本不足不应按均值熔断"


def test_14_quality_gate_line_and_modes():
    """v1.5.1-B3 分行门禁 + off/warn 模式回滚开关。"""
    from core.acoustic_quality import GateConfig, evaluate_line_gate, evaluate_track_gate, TrackStats

    cfg = GateConfig()

    def line(n, score, none_tail=0):
        ws = [{"text": "字", "score": score, "start": i * 100, "end": (i + 1) * 100}
              for i in range(n - none_tail)]
        ws += [{"text": "字", "score": None, "start": i * 100, "end": (i + 1) * 100}
               for i in range(n - none_tail, n)]
        return {"words": ws}

    assert not evaluate_line_gate(line(8, -1.5), cfg).demote, "好分行不该降级"
    assert evaluate_line_gate(line(8, -6.5), cfg).demote, "整行差应降级（< line_mean_min=-5.0）"
    assert evaluate_line_gate(line(8, -1.5, none_tail=5), cfg).demote, "覆盖不足应降级"
    assert not evaluate_line_gate(line(3, -6.5), cfg).demote, "3 字行不评估（噪声）"

    worst = TrackStats(n_target=200, n_scored=180, mean=-99.0, med=-99.0, p10=-99.0,
                       unaligned_ratio=0.99, span_ratio=0.01, demote_ratio=0.9,
                       n_lines_evaluable=30, n_lines_demoted=27)
    assert evaluate_track_gate(worst, GateConfig(mode="off")) == [], "off 模式必须整体放行"
    assert evaluate_track_gate(worst, GateConfig(mode="warn")), "warn 模式仍应算出理由（只告警不熔断）"
    assert not GateConfig(mode="off").enabled
    assert GateConfig(mode="warn").enabled and not GateConfig(mode="warn").enforce


def test_15_synthesized_word_accounting():
    """v1.5.2-D2/D4 synthesized_words 语义：只有「有逐字行、且无任何真声学行」才为 True。"""
    from core.acoustic_quality import finalize_word_source_flags

    def mk():
        return {"lines": [
            {"text": "ab", "words": [{"text": "a", "score": -1.0}, {"text": "b", "score": -1.2}]},
            {"text": "cd", "words": [{"text": "c", "score": -1.1}, {"text": "d", "score": -1.3}]},
        ]}

    # 两行都是真声学 → 不算合成
    d = finalize_word_source_flags(mk())
    assert d["synthesized_words"] is False
    assert d["synthesized_ratio"] == 0.0
    assert d["acoustic_coverage"] == 1.0

    # 一行线性保底 → 比例 0.5，但仍不算「整首合成」
    d = finalize_word_source_flags(mk(), fallback_lines=[1])
    assert d["synthesized_words"] is False
    assert d["synthesized_ratio"] == 0.5
    assert d["words_source_stats"]["gpu_linear_fallback"] == 1
    assert d["linear_fallback_lines"] == 1

    # 两行都线性保底 → 才算整首合成
    d = finalize_word_source_flags(mk(), fallback_lines=[0, 1])
    assert d["synthesized_words"] is True
    assert d["synthesized_ratio"] == 1.0

    # 全行级歌词（没有任何逐字）：既没有声学、也没有合成 → 不是「合成」
    d = finalize_word_source_flags({"lines": [{"text": "x", "words": []}, {"text": "y", "words": []}]})
    assert d["no_word_level"] is True
    assert d["n_wordable_lines"] == 0
    assert d["synthesized_words"] is False, "无逐字 ≠ 合成"
    assert d["synthesized_ratio"] == 0.0
    assert d["acoustic_coverage"] == 0.0
    assert d["words_source_stats"]["line_level"] == 2


def test_16_finalize_engine_unscored_marks_gpu_whisper():
    """v1.5.2-D2: 普通话双引擎（engine_scored=False）的逐字是真声学，不得记成 interpolated。"""
    from core.acoustic_quality import (
        finalize_word_source_flags, SRC_GPU_WHISPER, SRC_INTERPOLATED,
    )

    data = {"lines": [
        {"text": "傍晚六点下班", "words": [
            {"text": "傍", "start": 1000, "end": 1200},
            {"text": "晚", "start": 1200, "end": 1400},
        ]},
        {"text": "换掉药厂的衣裳", "words": [
            {"text": "换", "start": 2000, "end": 2200},
            {"text": "掉", "start": 2200, "end": 2400},
        ]},
    ]}
    out = finalize_word_source_flags(data, engine_scored=False)
    assert out["words_source_stats"][SRC_GPU_WHISPER] == 2, out["words_source_stats"]
    assert out["words_source_stats"][SRC_INTERPOLATED] == 0, out["words_source_stats"]
    assert out["acoustic_coverage"] == 1.0
    assert out["scored_coverage"] == 0.0
    assert out["synthesized_words"] is False
    assert all(l["words_source"] == SRC_GPU_WHISPER for l in out["lines"])


def test_17_finalize_mixed_score_is_interpolated():
    """v1.5.2-D2: 同一行内「有分 + 无分」混合 = 邻字内插，才记 interpolated。"""
    from core.acoustic_quality import finalize_word_source_flags, SRC_INTERPOLATED

    data = {"lines": [{"text": "abc", "words": [
        {"text": "a", "score": -1.0},
        {"text": "b", "score": None},
        {"text": "c", "score": -1.2},
    ]}]}
    out = finalize_word_source_flags(data, engine_scored=True)
    assert out["lines"][0]["words_source"] == SRC_INTERPOLATED
    assert out["acoustic_coverage"] == 0.0
    assert out["synthesized_words"] is True


def test_18_finalize_stats_sum_equals_lines_and_no_double_count():
    """v1.5.2-D4: stats 覆盖全部行；fallback∩demoted 不重复计数。"""
    from core.acoustic_quality import finalize_word_source_flags

    data = {"lines": [
        {"text": "a", "words": [{"text": "a", "score": -1.0}]},
        {"text": "b", "words": [{"text": "b", "score": -1.0}]},
        {"text": "c", "words": []},
    ]}
    # 第 1 行同时出现在 fallback 与 demoted：只记优先级最高的 demoted
    out = finalize_word_source_flags(data, fallback_lines=[1], demoted_lines=[1])
    stats = out["words_source_stats"]
    assert sum(stats.values()) == len(data["lines"]), stats
    assert stats["gate_demoted"] == 1
    assert stats["gpu_linear_fallback"] == 0
    assert out["linear_fallback_lines"] == 0, "与 stats 不一致即为重复计数"


def test_19_track_gate_skips_score_dims_when_unscored():
    """v1.5.2-D2: 引擎不产分时，均值/unaligned 维度跳过（不拿 -99/1.0 误熔断）。"""
    from core.acoustic_quality import compute_track_stats, evaluate_track_gate, GateConfig

    lines = [{"start": 1000, "end": 3000, "words": [{"text": "a"}]}]
    stats = compute_track_stats(
        lines, total_audio_s=300.0, n_target=60, n_target_unaligned=60,
        scores_available=False, hints_span_s=None,
    )
    assert stats.unaligned_ratio == 1.0  # 没有分数时算出来必然 1.0
    reasons = evaluate_track_gate(stats, GateConfig())
    assert not any(r.startswith(("R_TRACK_MEAN", "R_TRACK_MEDIAN", "R_TRACK_P10", "R_TRACK_UNALIGNED")) for r in reasons), reasons


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
