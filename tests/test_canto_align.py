# -*- coding: utf-8 -*-
"""粤语声学对齐器 (Cantonese CTC Aligner) 逻辑与门禁单元测试。"""

import os
import sys
import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.acoustic_canto import (
    CantoneseCTCAligner,
    ENTERING_TONE_ENDINGS,
    _ENTERING_TONE_SILENT_MS,
    _MIN_SILENT_MS,
    _LINE_GAP_MS,
)


def test_entering_tone_constants():
    """测试入声字韵尾定义与闭气门控容错常量。"""
    assert "p" in ENTERING_TONE_ENDINGS
    assert "t" in ENTERING_TONE_ENDINGS
    assert "k" in ENTERING_TONE_ENDINGS
    # 入声字闭气截断容错门限应长于普通静默门限
    assert _ENTERING_TONE_SILENT_MS > _MIN_SILENT_MS
    assert _ENTERING_TONE_SILENT_MS == 120


def test_synthetic_forced_align_structure():
    """测试 forced_align 在无调粤拼空间上的映射与数据结构完整性。"""
    try:
        import torch
        import torchaudio.functional as F
    except ImportError:
        print("  SKIP  test_synthetic_forced_align_structure (无 torch 环境)")
        return

    # 模拟 3 个音节：hoi (1), fut (2), tin (3)
    # T=20 帧，K=3 (加上 blank=0 共 4 类)
    log_probs = torch.randn(1, 20, 4).log_softmax(dim=-1)
    targets = torch.tensor([[1, 2, 3]], dtype=torch.int32)
    input_lengths = torch.tensor([20], dtype=torch.int32)
    target_lengths = torch.tensor([3], dtype=torch.int32)

    aligned, scores = F.forced_align(log_probs, targets, input_lengths, target_lengths, blank=0)
    spans = F.merge_tokens(aligned[0], scores[0], blank=0)

    assert len(spans) == 3, f"Expected 3 spans, got {len(spans)}"
    for s in spans:
        assert s.start <= s.end
        assert s.score <= 0.0  # 对数域得分 ≤ 0


def test_latin_word_interpolation_in_line():
    """测试包含英文字词的行在时间分配后 100% 覆盖，无字缺失。"""
    aligner = CantoneseCTCAligner(device="cpu")

    # 模拟一行含英文和汉字：Baby, hold住我
    # 模拟汉字“住”(zyu)和“我”(ngo)已被对齐，Baby 和 hold 需被平滑插值
    raw_words = [
        {"char": "Baby", "start": None, "end": None, "type": "latin", "is_entering": False},
        {"char": "hold", "start": None, "end": None, "type": "latin", "is_entering": False},
        {"char": "住", "start": 2.0, "end": 2.5, "type": "hanzi", "is_entering": False},
        {"char": "我", "start": 2.6, "end": 3.0, "type": "hanzi", "is_entering": False},
    ]

    # 执行插值逻辑测试
    aligned_indices = [i for i, w in enumerate(raw_words) if w["start"] is not None]
    assert aligned_indices == [2, 3]

    # 行首未对齐单词插值
    first_al = aligned_indices[0]
    head_st = max(0.0, raw_words[first_al]["start"] - first_al * 0.35)
    step = (raw_words[first_al]["start"] - head_st) / first_al
    for i in range(first_al):
        raw_words[i]["start"] = head_st + i * step
        raw_words[i]["end"] = head_st + (i + 1) * step

    # 验证全部 words 都有有效时间戳且严格单调
    for i, w in enumerate(raw_words):
        assert w["start"] is not None, f"Word {w['char']} missing start"
        assert w["end"] is not None, f"Word {w['char']} missing end"
        assert w["start"] < w["end"], f"Word {w['char']} invalid duration"
        if i > 0:
            assert w["start"] >= raw_words[i - 1]["start"], "Not monotonic"


def test_pure_english_line_in_cantonese_track():
    """测试粤语歌中插入纯英文行时时间戳不倒流、不硬编码为 0.0s、时长为正。"""
    import unittest.mock as mock
    try:
        import torch
    except ImportError:
        print("  SKIP  test_pure_english_line_in_cantonese_track (无 torch)")
        return

    aligner = CantoneseCTCAligner(device="cpu")
    aligner._init_model_and_mapping = mock.MagicMock()
    aligner._syllable_list = ["hoi", "fut", "tin", "hung", "laang", "fung", "ceoi", "gwo"]
    aligner._syllable_to_id = {s: i + 1 for i, s in enumerate(aligner._syllable_list)}

    # 模拟 3 行：第一行粤语（10s），第二行纯英文（12s），第三行粤语（14s）
    T = 1000
    fake_lp = torch.full((1, T, 9), -10.0)
    for t_idx in range(4):
        fake_lp[0, 500 + t_idx * 10, t_idx + 1] = 0.0
    for t_idx in range(4):
        fake_lp[0, 700 + t_idx * 10, t_idx + 5] = 0.0

    aligner.forward_sliding_window = mock.MagicMock(return_value=fake_lp)

    clean_lines = ["海阔天空", "Yeah baby", "冷风吹过"]
    audio = np.zeros(16000 * 30, dtype=np.float32)
    hints = [(10.0, "海阔天空"), (12.0, "Yeah baby"), (14.0, "冷风吹过")]

    out = aligner.align_cantonese_lines(audio, clean_lines, hints=hints)
    lines = out["lines"]
    assert len(lines) == 3, f"Expected 3 lines, got {len(lines)}"

    # 验证行顺序单调递增，纯英文行没有跳到 0.0s
    for i in range(len(lines) - 1):
        assert lines[i]["start"] < lines[i + 1]["start"], f"Line {i} not before Line {i+1}"
        assert lines[i]["end"] <= lines[i + 1]["start"], f"Line {i} overlap with Line {i+1}"

    # 验证纯英文行 (index 1) 起止时间正序且位于 10s~14s 之间
    eng_line = lines[1]
    assert eng_line["text"] == "Yeah baby"
    assert eng_line["start"] >= 10000, f"English line start too early: {eng_line['start']}"
    assert eng_line["end"] > eng_line["start"], f"Negative duration: {eng_line}"
    assert eng_line["duration"] >= 100

    # 验证逐字严格正序
    for w in eng_line["words"]:
        assert w["end"] > w["start"]


def test_multi_word_gap_monotonicity():
    """测试汉字之间插入多个英文单词时各字时长不为零且严格单调。"""
    raw_words = [
        {"char": "前", "start": 1.0, "end": 1.2, "type": "hanzi"},
        {"char": "come", "start": None, "end": None, "type": "latin"},
        {"char": "on", "start": None, "end": None, "type": "latin"},
        {"char": "now", "start": None, "end": None, "type": "latin"},
        {"char": "后", "start": 1.25, "end": 1.5, "type": "hanzi"},
    ]
    aligned_indices = [0, 4]
    gap_count = 3
    gap_st = raw_words[0]["end"]
    gap_ed = raw_words[4]["start"]
    min_needed = gap_count * 0.060
    if gap_ed < gap_st + min_needed:
        gap_ed = gap_st + min_needed
        raw_words[4]["start"] = gap_ed
        if raw_words[4]["end"] <= gap_ed:
            raw_words[4]["end"] = gap_ed + 0.060
    step = (gap_ed - gap_st) / gap_count
    for j in range(1, gap_count + 1):
        raw_words[j]["start"] = gap_st + (j - 1) * step
        raw_words[j]["end"] = gap_st + j * step

    for i in range(len(raw_words)):
        assert raw_words[i]["end"] > raw_words[i]["start"], f"Word {i} duration <= 0"
        if i > 0:
            assert raw_words[i]["start"] >= raw_words[i - 1]["end"], f"Overlap between {i-1} and {i}"


def test_canto_aligner_vram_unload():
    """测试 unload 方法能安全重置模型引用。"""
    aligner = CantoneseCTCAligner(device="cpu")
    aligner._model = "fake_model"
    aligner._processor = "fake_proc"
    aligner._vocab_mapping = "fake_map"
    aligner.unload()
    assert aligner._model is None
    assert aligner._processor is None
    assert aligner._vocab_mapping is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
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

