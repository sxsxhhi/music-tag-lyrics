# -*- coding: utf-8 -*-
"""声学流水线 v1.4.0 离线单测：铁律 20 环境门禁、谷底双向锁定、STFT 谱流拆分、
validator 零重叠防御（R2）、converter 链式前奏（B3）、Golden 夹具结构校验。

全部离线（CI 无 torch 可跑；STFT 用例在无 torch 环境自动跳过）。
运行：python tests/test_acoustic_pipeline.py
"""
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
        print("     (skip: 无 torch 环境)")
        return
    import torch
    if not torch.cuda.is_available():
        print("     (skip: 无 CUDA)")
        return
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
        print("     (skip: 无 torch 环境)")
        return
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


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            import traceback
            traceback.print_exc()
            failed.append(t.__name__)
    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print(f"FAILED: {failed}")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
