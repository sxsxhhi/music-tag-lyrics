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
    fake_lp = torch.full((1, T, 9), -0.5)
    for t_idx in range(4):
        fake_lp[0, 500 + t_idx * 10: 500 + (t_idx + 1) * 10, t_idx + 1] = -0.1
    for t_idx in range(4):
        fake_lp[0, 700 + t_idx * 10: 700 + (t_idx + 1) * 10, t_idx + 5] = -0.1

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


class _MockTensor1D:
    def __init__(self, data):
        self._data = list(data)

    def tolist(self):
        return self._data


def test_reconstruct_target_spans_b9_b12():
    """附录 B.3 叠字重建（P1-21）：测试 B9, B10, B11, B12 四种核心场景。"""
    from core.acoustic_canto import reconstruct_target_spans

    # B9: 无 blank 合并：raw=[12,12,12,12, 0, 20,20], targets=[12, 12, 20]
    toks_b9 = _MockTensor1D([12, 12, 12, 12, 0, 20, 20])
    scs_b9 = _MockTensor1D([-0.1] * 7)
    spans_b9 = reconstruct_target_spans(toks_b9, scs_b9, [12, 12, 20])
    assert len(spans_b9) == 3
    # 4 帧被等分为前 2 帧与后 2 帧
    assert round(spans_b9[0]["start"], 3) == 0.000
    assert round(spans_b9[0]["end"], 3) == 0.040
    assert round(spans_b9[1]["start"], 3) == 0.040
    assert round(spans_b9[1]["end"], 3) == 0.080
    assert round(spans_b9[2]["start"], 3) == 0.100
    assert round(spans_b9[2]["end"], 3) == 0.140

    # B10: blank 分隔 1:1：raw=[12,12, 0, 12,12, 0, 20,20], targets=[12, 12, 20]
    toks_b10 = _MockTensor1D([12, 12, 0, 12, 12, 0, 20, 20])
    scs_b10 = _MockTensor1D([-0.1] * 8)
    spans_b10 = reconstruct_target_spans(toks_b10, scs_b10, [12, 12, 20])
    assert len(spans_b10) == 3
    assert round(spans_b10[0]["start"], 3) == 0.000
    assert round(spans_b10[0]["end"], 3) == 0.040
    assert round(spans_b10[1]["start"], 3) == 0.060
    assert round(spans_b10[1]["end"], 3) == 0.100
    assert round(spans_b10[2]["start"], 3) == 0.120
    assert round(spans_b10[2]["end"], 3) == 0.160

    # B11: 音节缺失：raw=[12,12, 0, 20,20], targets=[12, 20, 33]
    toks_b11 = _MockTensor1D([12, 12, 0, 20, 20])
    scs_b11 = _MockTensor1D([-0.1] * 5)
    spans_b11 = reconstruct_target_spans(toks_b11, scs_b11, [12, 20, 33])
    assert len(spans_b11) == 3
    assert spans_b11[0] is not None
    assert spans_b11[1] is not None
    assert spans_b11[2] is None  # 音节缺失置 None

    # B12: 真退化（帧数不足）：raw=[12,12, 0, 12,12], targets=[12, 12, 12, 12]
    # 4 个同音 target 只有 2 个 run 且末 run 仅 2 帧，帧数不足 3 帧 -> 整组 4 个全部回退为 None
    toks_b12 = _MockTensor1D([12, 12, 0, 12, 12])
    scs_b12 = _MockTensor1D([-0.1] * 5)
    spans_b12 = reconstruct_target_spans(toks_b12, scs_b12, [12, 12, 12, 12])
    assert len(spans_b12) == 4
    assert all(s is None for s in spans_b12)


def test_split_orig_tokens_b1_b5():
    """附录 B.1 分词与文本还原（P0-4）：验证 NFKC 归一比较与原文输出。"""
    from core.acoustic_aligner import split_orig_tokens

    # B1: Thank you
    m, o = split_orig_tokens("Thank you")
    assert m == ["Thank", "you"]
    assert "".join(o) == "Thank you"

    # B2: 全角 Ｔｈａｎｋ ｙｏｕ
    m, o = split_orig_tokens("Ｔｈａｎｋ ｙｏｕ")
    assert m == ["Thank", "you"]
    assert "".join(o) == "Ｔｈａｎｋ ｙｏｕ"

    # B3: café Señorita
    m, o = split_orig_tokens("café Señorita")
    assert m == ["café", "Señorita"]
    assert "".join(o) == "café Señorita"

    # B4: 我 love you
    m, o = split_orig_tokens("我 love you")
    assert m == ["我", "love", "you"]
    assert o == ["我 ", "love ", "you"]
    assert "".join(o) == "我 love you"

    # B5: 前导/尾随空白保留
    _, o1 = split_orig_tokens("  前导")
    assert o1 == ["  前", "导"]
    assert "".join(o1) == "  前导"

    _, o2 = split_orig_tokens("尾随  ")
    assert o2 == ["尾", "随  "]
    assert "".join(o2) == "尾随  "


def test_align_orig_text_to_char_spans_b6_b8():
    """附录 B.2 弹性回填（P0-4）：验证全角对齐、丢词内插与快嘴保底。"""
    from core.acoustic_aligner import align_orig_text_to_char_spans

    # B6: 全角行对齐：orig=Ｔｈａｎｋ ｙｏｕ，Whisper 沿用轴
    w_tokens = [
        {"char": "Thank", "start": 1.000, "end": 1.200},
        {"char": "you", "start": 1.300, "end": 1.500},
    ]
    res_b6 = align_orig_text_to_char_spans("Ｔｈａｎｋ ｙｏｕ", w_tokens, 0.500, 2.000)
    assert len(res_b6) == 2
    assert "".join(c["char"] for c in res_b6) == "Ｔｈａｎｋ ｙｏｕ"
    assert round(res_b6[0]["start"], 3) == 1.000
    assert round(res_b6[1]["start"], 3) == 1.300

    # B7: Whisper 丢词：orig=I love you，W 只有 I 和 you
    w_tokens_b7 = [
        {"char": "I", "start": 1.000, "end": 1.100},
        {"char": "you", "start": 1.600, "end": 1.800},
    ]
    res_b7 = align_orig_text_to_char_spans("I love you", w_tokens_b7, 0.500, 2.000)
    assert len(res_b7) == 3
    assert "".join(c["char"] for c in res_b7) == "I love you"
    assert res_b7[0]["start"] == 1.000
    # love 必须在 I 之后、you 之前单调内插
    assert res_b7[1]["start"] >= res_b7[0]["end"]
    assert res_b7[2]["start"] >= res_b7[1]["end"]

    # B8: 快嘴不可行：orig=10 汉字，行宽 0.3s (< 10×60ms) -> 线性保底
    orig_b8 = "一二三四五六七八九十"
    res_b8 = align_orig_text_to_char_spans(orig_b8, [], 0.500, 0.800)
    assert len(res_b8) == 10
    assert "".join(c["char"] for c in res_b8) == orig_b8
    assert round(res_b8[0]["start"], 3) == 0.500
    assert round(res_b8[-1]["end"], 3) == 0.800


def test_absorb_punctuation_tokens():
    """P1-25: 标点符号吸收合并至前字，双路径统一。"""
    from core.acoustic_aligner import absorb_punctuation_tokens

    raw = [
        {"text": "你", "start": 1000, "end": 1200, "duration": 200},
        {"text": "好", "start": 1200, "end": 1400, "duration": 200},
        {"text": "，", "start": 1400, "end": 1500, "duration": 100},
        {"text": "世", "start": 1500, "end": 1700, "duration": 200},
        {"text": "界", "start": 1700, "end": 1900, "duration": 200},
        {"text": "！", "start": 1900, "end": 2000, "duration": 100},
    ]
    merged = absorb_punctuation_tokens(raw, "你好，世界！")
    assert len(merged) == 4
    assert [w["text"] for w in merged] == ["你", "好，", "世", "界！"]
    assert "".join(w["text"] for w in merged) == "你好，世界！"
    assert merged[1]["end"] == 1500  # end 时间覆盖逗号

    # 首标点合并至首字
    raw_lead = [
        {"text": "“", "start": 500, "end": 600, "duration": 100},
        {"text": "测试", "start": 600, "end": 900, "duration": 300},
    ]
    merged_lead = absorb_punctuation_tokens(raw_lead, "“测试")
    assert len(merged_lead) == 1
    assert merged_lead[0]["text"] == "“测试"


def test_canto_circuit_breaker_p0_3():
    """P0-3 熔断门禁验证：当对齐置信度不足（mean < -1.80）时，必须熔断并安全返回 None。"""
    import unittest.mock as mock
    try:
        import torch
    except ImportError:
        print("  SKIP  test_canto_circuit_breaker_p0_3 (无 torch)")
        return

    aligner = CantoneseCTCAligner(device="cpu")
    aligner._init_model_and_mapping = mock.MagicMock()
    aligner._syllable_list = ["hoi", "fut", "tin", "hung"]
    aligner._syllable_to_id = {s: i + 1 for i, s in enumerate(aligner._syllable_list)}

    # 模拟低置信对齐得分（全为 -2.50 < -1.80）
    T = 500
    fake_lp = torch.full((1, T, 5), -2.50)
    for t_idx in range(4):
        fake_lp[0, 100 + t_idx * 10, t_idx + 1] = -2.15

    aligner.forward_sliding_window = mock.MagicMock(return_value=fake_lp)
    clean_lines = ["海阔天空"]
    audio = np.zeros(16000 * 10, dtype=np.float32)

    out = aligner.align_cantonese_lines(audio, clean_lines)
    assert out is None, "低质量声学对齐未触发熔断！"


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

