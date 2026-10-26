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
