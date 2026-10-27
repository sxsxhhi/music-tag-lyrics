# -*- coding: utf-8 -*-
"""粤语声学对齐器 (Cantonese CTC Aligner) 逻辑与门禁单元测试。"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

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
        _testkit.skip("无 torch")

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


def _run_fake_align(lines, hints=None, total_sec=20.0):
    """用伪造 logits 驱动真实的 align_cantonese_lines（v1.5.1-E3）。

    v1.5.1 之前这两个用例把插值算法在测试里**抄了一遍**再断言自己写的副本，
    改坏生产代码它们不会红，等于没有回归保护。这里改为 mock
    forward_sliding_window 后调用真实引擎。
    """
    import unittest.mock as mock
    try:
        import torch
    except ImportError:
        _testkit.skip("无 torch")

    from core.utils.jyutping import text_to_jyutping_tokens

    seq = []
    for ln in lines:
        seq.extend(text_to_jyutping_tokens(ln))
    syl = []
    for s in seq:
        if s not in syl:
            syl.append(s)

    aligner = CantoneseCTCAligner(device="cpu")
    aligner._init_model_and_mapping = mock.MagicMock()
    aligner._syllable_list = syl
    aligner._syllable_to_id = {s: i + 1 for i, s in enumerate(syl)}

    T = int(total_sec * 50)  # 20ms/帧
    fake_lp = torch.full((1, T, len(syl) + 1), -3.0)
    # 音节铺满音频的 10%~85%：若挤在开头，span_ratio 会低于门限被熔断，
    # 相当于测试在验证门禁而不是在验证插值。
    span_start = max(10, int(T * 0.10))
    span_end = int(T * 0.85)
    step = max(30, (span_end - span_start) // max(1, len(seq)))
    for i, s in enumerate(seq):
        sid = syl.index(s) + 1
        st = span_start + i * step
        fake_lp[0, st: st + max(30, step // 2), sid] = -0.2
    aligner.forward_sliding_window = mock.MagicMock(return_value=fake_lp)

    # 用低幅噪声而不是纯静音：纯静音会让第 6 步的物理门禁（RMS 谷底）把
    # 行尾收束到几百毫秒，span_ratio 掉到 0.026 触发熔断，测的就不是插值了。
    audio = np.random.default_rng(0).normal(0, 0.05, int(total_sec * 16000)).astype(np.float32)
    return aligner.align_cantonese_lines(audio, lines, hints=hints)


def _assert_words_sane(out, name):
    assert out and out.get("lines"), f"{name}: 引擎没有返回行"
    for li, ln in enumerate(out["lines"]):
        ws = ln.get("words") or []
        assert ws, f"{name}: 行 {li} 没有逐字"
        for k, w in enumerate(ws):
            assert w.get("start") is not None and w.get("end") is not None, (
                f"{name}: 行 {li} 字 {k} 时间缺失"
            )
            assert w["end"] > w["start"], f"{name}: 行 {li} 字 {k} 时长 <= 0"
            if k:
                assert w["start"] >= ws[k - 1]["end"] - 1e-9, f"{name}: 行 {li} 字重叠"
    for i in range(len(out["lines"]) - 1):
        assert out["lines"][i]["end"] <= out["lines"][i + 1]["start"], f"{name}: 行重叠"


def test_latin_word_interpolation_in_line():
    """含英文字词的行：真实引擎必须给每个字都分到时间，且严格单调无零宽。"""
    line = "Baby, hold住我"
    out = _run_fake_align([line])
    _assert_words_sane(out, "latin-in-line")

    words = out["lines"][0]["words"]
    joined = "".join(w["text"] for w in words).replace(" ", "")
    assert joined == line.replace(" ", ""), f"文本被改写: {joined!r} != {line!r}"
    # 英文词条也必须拿到时间（旧实现留 None，下游会硬编成 0.0s）
    texts = [w["text"].strip() for w in words]
    assert any("Baby" in t for t in texts), texts
    assert any("hold" in t for t in texts), texts


def test_multi_word_gap_monotonicity():
    """汉字之间夹多个英文单词：真实引擎必须把间隙等分且不重叠。"""
    line = "前 come on now 后"
    out = _run_fake_align([line], total_sec=10.0)
    _assert_words_sane(out, "multi-word-gap")

    words = out["lines"][0]["words"]
    joined = "".join(w["text"] for w in words).replace(" ", "")
    assert joined == line.replace(" ", ""), f"文本被改写: {joined!r}"
    # 中间三个英文词必须各自拿到互不重叠的时间段
    latin = [w for w in words if w["text"].strip().lower() in ("come", "on", "now")]
    assert len(latin) == 3, [w["text"] for w in words]
    for a, b in zip(latin, latin[1:]):
        assert a["end"] <= b["start"], (a, b)


def test_pure_english_line_in_cantonese_track():
    """测试粤语歌中插入纯英文行时时间戳不倒流、不硬编码为 0.0s、时长为正。"""
    import unittest.mock as mock
    try:
        import torch
    except ImportError:
        _testkit.skip("无 torch")

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


def test_reconstruct_no_cascade_a1():
    """v1.5.1-A1 级联修复：单个音节缺失不得污染其后所有 target。

    旧实现遇到「本组找不到 run」直接 break，把剩余 target 全部置 None ——
    实测 target=[1,3,2]、path 只有 1 和 2 时，索引 2 的 id=2 明明存在也被置 None。
    """
    from core.acoustic_canto import reconstruct_target_spans

    def run(path, targets):
        return reconstruct_target_spans(
            _MockTensor1D(path), _MockTensor1D([-0.1] * len(path)), targets
        )

    # 中间缺一个：id=3 不在帧路径里，但 id=2 在后面还能匹配上
    sp = run([1, 1, 0, 2, 2], [1, 3, 2])
    assert len(sp) == 3
    assert sp[0] is not None and sp[1] is None and sp[2] is not None, sp
    assert round(sp[2]["start"], 3) == 0.060, sp[2]  # id=2 的 run 从第 3 帧开始

    # 连续缺两个
    sp = run([1, 1, 0, 2, 2], [1, 4, 5, 2])
    assert len(sp) == 4
    assert sp[0] is not None and sp[1] is None and sp[2] is None and sp[3] is not None, sp

    # 首尾都缺
    sp = run([1, 1, 0, 2, 2], [9, 1, 2, 7])
    assert len(sp) == 4
    assert sp[0] is None and sp[3] is None and sp[1] and sp[2], sp

    # 全缺：不抛异常，长度恒等
    sp = run([0, 0, 0, 0], [1, 3])
    assert len(sp) == 2 and all(s is None for s in sp)

    # 多余 run 被丢弃，正常 target 不受影响
    sp = run([1, 1, 0, 9, 9, 0, 2, 2], [1, 2])
    assert len(sp) == 2 and sp[0] is not None and sp[1] is not None, sp

    # 长度恒等：任意组合都不得越界
    for targets in ([1], [1, 1, 1], [1, 2, 3, 4, 5]):
        sp = run([1, 1, 0, 2, 2], targets)
        assert len(sp) == len(targets), (targets, len(sp))


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


def _gate_run(uniform_score, n_chars=40, total_sec=30.0, mode=None):
    """用均匀 log-prob 驱动真实引擎，返回 (status, payload)。

    整个张量铺同分 → 无论 Viterbi 选中哪一帧，取到的 log-prob 都等于该值，
    门禁算出的 mean/median/p10 因而确定可预测。
    """
    import unittest.mock as mock
    import os
    try:
        import torch
    except ImportError:
        _testkit.skip("无 torch")

    from core.acoustic_quality import AcousticQualityError

    line = "".join("字" * n_chars)
    from core.utils.jyutping import text_to_jyutping_tokens
    seq = text_to_jyutping_tokens(line)
    syl = list(dict.fromkeys(seq))
    T = int(total_sec * 50)
    aligner = CantoneseCTCAligner(device="cpu")
    aligner._init_model_and_mapping = mock.MagicMock()
    aligner._syllable_list = syl
    aligner._syllable_to_id = {s: i + 1 for i, s in enumerate(syl)}
    fake_lp = torch.full((1, T, len(syl) + 1), float(uniform_score))
    aligner.forward_sliding_window = mock.MagicMock(return_value=fake_lp)

    audio = np.random.default_rng(0).normal(0, 0.05, int(total_sec * 16000)).astype(np.float32)
    old = os.environ.get("MTL_GATE_MODE")
    if mode is not None:
        os.environ["MTL_GATE_MODE"] = mode
    try:
        out = aligner.align_cantonese_lines(audio, [line])
        return ("OK", out)
    except AcousticQualityError as e:
        return ("FUSE", e.reasons)
    finally:
        if old is None:
            os.environ.pop("MTL_GATE_MODE", None)
        else:
            os.environ["MTL_GATE_MODE"] = old


def test_canto_circuit_breaker_p0_3():
    """P0-3 熔断：声学置信度不足时必须熔断（v1.5.1 起以 AcousticQualityError 表达）。

    旧实现返回 None，与「环境失败/正常失败」不可区分，上层无法做 zh 重试。
    """
    from core.acoustic_quality import AcousticQualityError
    status, payload = _gate_run(-9.0)
    assert status == "FUSE", f"极差声学分未熔断: {status}"
    assert payload and any(str(r).startswith(("R_TRACK_", "R_MASS_")) for r in payload), payload


def test_canto_gate_pass_side_boundary():
    """E2 pass 侧边界：接近门限的好分**必须放行**。

    旧用例只有 fail 侧（全 -2.50 断言熔断），阈值往回松到 -1.9 也不会红——
    这正是「-2.10 距焚情 -1.886 仅 0.214」能活下来的原因。
    实测锚点：粤语《焚情》mean=-1.886；普通话《那么骄傲》强入 yue mean=-7.785。
    """
    # 焚情量级：必须放行（实测 mean=-1.936）
    status, payload = _gate_run(-1.886, n_chars=200)
    assert status == "OK", f"焚情量级的好分被误熔断: {payload}"

    # 实测门槛（-4.00）内侧一档，也必须放行
    status, payload = _gate_run(-3.00, n_chars=200)
    assert status == "OK", f"-3.00 被误熔断: {payload}"

    # 那么骄傲量级（实测 mean=-8.279）：必须熔断
    status, payload = _gate_run(-7.785, n_chars=200)
    assert status == "FUSE", f"那么骄傲量级未熔断: {payload}"
    assert any("R_TRACK_MEAN" in str(r) for r in payload), payload


def test_canto_gate_modes():
    """warn 模式只告警不熔断；off 模式整体回 v1.5.0 行为。"""
    status, payload = _gate_run(-9.0, mode="warn")
    assert status == "OK", f"warn 模式不应熔断: {payload}"
    assert payload and payload.get("quality_stats"), "warn 模式应保留统计供排查"

    status, payload = _gate_run(-9.0, mode="off")
    assert status == "OK", f"off 模式不应熔断: {payload}"


def test_unaligned_word_score_none():
    """契约验证：未对齐词（无声学跨度）的 score 必须显式为 None。

    v1.5.1-E3：旧用例把消费逻辑在测试里**抄了一遍**再断言自己刚写死的 None，
    全程没调用生产函数，改坏 `acoustic_canto.py:377-381` 它也不会红。
    现在改为 mock forward_sliding_window 驱动真实 align_cantonese_lines：
    帧路径里只放汉字音节，英文词条必然拿不到跨度 → score 必须为 None。
    """
    out = _run_fake_align(["la 住"], total_sec=8.0)
    assert out and out.get("lines"), "引擎没有返回行"
    words = out["lines"][0]["words"]
    latin = [w for w in words if w["text"].strip().lower().startswith("la")]
    hanzi = [w for w in words if "住" in w["text"]]
    assert latin, [w["text"] for w in words]
    assert hanzi, [w["text"] for w in words]
    for w in latin:
        assert w.get("score") is None, f"未对齐词拿到了伪分数: {w}"
    # 对齐上的汉字必须有负分（对数域），否则门禁统计会把它当占位
    assert all(isinstance(w.get("score"), float) and w["score"] < 0 for w in hanzi), hanzi



def test_vocal_cache_hit_and_clear():
    """验证 Demucs 单曲内存人声缓存复用与显式清理（免二次分离）。"""
    import unittest.mock as mock
    from core.acoustic_aligner import AcousticAligner

    AcousticAligner.clear_vocal_cache()
    fake_audio = "dummy/path/song.flac"
    norm_path = os.path.abspath(fake_audio)

    # 伪造一个已分离的人声数组
    mock_vocals = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    sr = 44100

    # 首次注入缓存
    AcousticAligner._vocal_cache = (norm_path, mock_vocals, sr)

    # 二次调用相同路径：必须命中缓存直接返回，不走实际分离逻辑
    with mock.patch("soundfile.read") as mock_sf, mock.patch.object(AcousticAligner, "get_models") as mock_get_models:
        v_out, sr_out = AcousticAligner.extract_vocals_mono(fake_audio)
        assert mock_sf.call_count == 0, "命中缓存时不应调用音频读取"
        assert mock_get_models.call_count == 0, "命中缓存时不应加载模型"
        assert np.array_equal(v_out, mock_vocals)
        assert sr_out == sr

    # 调用 clear_vocal_cache 后缓存清空
    AcousticAligner.clear_vocal_cache()
    assert AcousticAligner._vocal_cache is None


def test_words_source_demoted_accounting():
    """验证 finalize_word_source_flags 对门禁降级行（words为空）的准确记账与分母闭环。"""
    from core.acoustic_quality import (
        finalize_word_source_flags, SRC_GPU_ACOUSTIC, SRC_GATE_DEMOTED, SRC_LINE_LEVEL,
    )

    data = {
        "lines": [
            {
                "start": 1000,
                "end": 3000,
                "text": "第一行对齐成功",
                "words": [{"text": "第", "start": 1000, "end": 1500, "score": -1.2}],
            },
            {
                "start": 3050,
                "end": 5000,
                "text": "第二行被质量门禁降级",
                "words": [],  # 门禁降级后 words 被清空
            },
            {
                "start": 5050,
                "end": 7000,
                "text": "第三行原生行级歌词",
                "words": [],  # 原生行级，非门禁降级
            },
        ]
    }

    # demoted_lines 指定第 1 行为门禁降级行（索引 1）
    out = finalize_word_source_flags(data, demoted_lines=[1])
    lines = out["lines"]
    stats = out["words_source_stats"]

    # 断言行级标签
    assert lines[0]["words_source"] == SRC_GPU_ACOUSTIC
    assert lines[1]["words_source"] == SRC_GATE_DEMOTED
    assert lines[2]["words_source"] == SRC_LINE_LEVEL

    # 断言统计字典：gate_demoted 为 1，acoustic 为 1，line_level 也入账
    assert stats[SRC_GATE_DEMOTED] == 1, stats
    assert stats[SRC_GPU_ACOUSTIC] == 1, stats
    assert stats[SRC_LINE_LEVEL] == 1, stats
    # v1.5.2-D4: stats 必须覆盖全部行，不允许有「没入账」的行
    assert sum(stats.values()) == len(lines), stats

    # 断言覆盖率分母闭环：可评估行数 2（第 0 行 + 第 1 行；第 2 行是原生行级不进分母）
    # n_real = 1, n_wordable = 2 -> acoustic_coverage = 0.5, synthesized_ratio = 0.5
    assert out["n_wordable_lines"] == 2, out["n_wordable_lines"]
    assert out["acoustic_coverage"] == 0.5000, out["acoustic_coverage"]
    assert out["synthesized_ratio"] == 0.5000, out["synthesized_ratio"]
    assert out["synthesized_words"] is False
    assert out["no_word_level"] is False


def test_track_gate_span_smart_exemption():
    """验证超长前奏/尾奏曲目 span_ratio 智能豁免逻辑。"""
    from core.acoustic_quality import (
        compute_track_stats, evaluate_track_gate, GateConfig,
    )

    cfg = GateConfig(track_span_min=0.35, track_mean_min=-4.0)

    # Case A: 存在 hints 且相对参考跨度吻合率 >= 0.70
    # 总音频 300 秒，实际演唱只有 100s~130s (span = 30s, span_ratio = 30/300 = 0.10 < 0.35)
    # 但参考歌词 hints 跨度为 100s~135s (hints_span_s = 35s, 30/35 = 0.857 >= 0.70)
    lines_a = [
        {"start": 100000, "end": 115000, "words": [{"score": -1.5}] * 15},
        {"start": 115050, "end": 130000, "words": [{"score": -1.5}] * 15},
    ]
    stats_a = compute_track_stats(
        lines_a, total_audio_s=300.0, n_target=30, hints_span_s=35.0,
    )
    assert stats_a.span_ratio < 0.35
    reasons_a = evaluate_track_gate(stats_a, cfg)
    assert not any("R_TRACK_SPAN" in r for r in reasons_a), f"有 hints 且相对吻合时不应触发 R_TRACK_SPAN: {reasons_a}"

    # Case B: 无 hints，但高分 (mean >= -3.0)、零降级且样本充足 (n_scored >= 20)
    lines_b = [
        {"start": 200000, "end": 220000, "words": [{"score": -1.8}] * 25},
    ]
    stats_b = compute_track_stats(
        lines_b, total_audio_s=400.0, n_target=25, demote_ratio=0.0, hints_span_s=None,
    )
    assert stats_b.span_ratio < 0.35
    reasons_b = evaluate_track_gate(stats_b, cfg)
    assert not any("R_TRACK_SPAN" in r for r in reasons_b), f"高分零降级长前奏曲目不应触发 R_TRACK_SPAN: {reasons_b}"

    # Case C: 无 hints，低分 (mean < -3.00)，不满足豁免条件 -> 正常被 R_TRACK_SPAN 拦截
    lines_c = [
        {"start": 200000, "end": 220000, "words": [{"score": -3.5}] * 25},
    ]
    stats_c = compute_track_stats(
        lines_c, total_audio_s=400.0, n_target=25, demote_ratio=0.0, hints_span_s=None,
    )
    reasons_c = evaluate_track_gate(stats_c, cfg)
    assert any("R_TRACK_SPAN" in r for r in reasons_c), f"低分曲目应被 R_TRACK_SPAN 拦截: {reasons_c}"

    # Case D: 倒流或零/负 hints 跨度防零除异常与误豁免
    stats_d = compute_track_stats(
        lines_c, total_audio_s=400.0, n_target=25, demote_ratio=0.0, hints_span_s=-5.0,
    )
    reasons_d = evaluate_track_gate(stats_d, cfg)
    assert any("R_TRACK_SPAN" in r for r in reasons_d)

    # Case E: 行时间戳倒置防 relative_cov 负数异常
    lines_e = [
        {"start": 120000, "end": 100000, "words": [{"score": -1.5}] * 15},
    ]
    stats_e = compute_track_stats(
        lines_e, total_audio_s=300.0, n_target=15, hints_span_s=35.0,
    )
    reasons_e = evaluate_track_gate(stats_e, cfg)
    assert any("R_TRACK_SPAN" in r for r in reasons_e)


def test_span_score_none_when_scores_empty():
    """v1.5.2-D5: 没有可用分数时 span.score 必须是 None，不能是 0.0（伪满分）。"""
    try:
        import torch
    except ImportError:
        _testkit.skip("无 torch")

    from core.acoustic_canto import reconstruct_target_spans

    # 两帧都是 token 12，target 只有一个 12；scores 为空 → 整段无分
    spans = reconstruct_target_spans(
        torch.tensor([12, 12], dtype=torch.int32),
        torch.tensor([], dtype=torch.float32),
        [12],
    )
    assert spans and spans[0] is not None, spans
    assert spans[0]["score"] is None, f"空分数应记 None，实际 {spans[0]['score']!r}"


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())

