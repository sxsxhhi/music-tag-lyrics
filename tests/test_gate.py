# -*- coding: utf-8 -*-
"""逐行合理性闸单测（v1.6.0）——纯函数，不需要模型/GPU。

判据依据（双样本实测）：
  · 真实长音 1456/1537/1455ms（字内停顿 60–160ms）必须放行
  · 吞停顿 2306ms（字内停顿 600ms）必须拦住
  · 硬上限 1600：1537 过、2306 拦；取 1500 会误杀 1537
"""
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from _testkit import main  # noqa: E402
from core.acoustic.qc import Env, LINE_GAP_MS, MIN_WORD_MS  # noqa: E402
from core.acoustic.gate import (  # noqa: E402
    apply_gate, judge_line, linear_split_words, restore_words, snapshot_words, split_display_units,
)


def _mk_words(specs):
    return [{"text": t, "start": int(s), "end": int(e)} for t, s, e in specs]


def _rms_with_silence(total_s, events):
    """events: [(t0_s, t1_s, level)]；其余时间为低本底。"""
    n = int(total_s * 50)
    rms = np.full(n, 0.005, dtype=np.float32)
    for t0, t1, lvl in events:
        a, b = int(t0 * 50), int(t1 * 50)
        rms[a:b] = lvl
    return rms


def test_judge_empty_words_rejects():
    ok, why = judge_line([], [], None, 3)
    assert not ok and why == "空行"


def test_judge_hard_width_1600():
    # 1800ms 连续能量（dip=0）→ 不算吞停顿，落进硬上限 1600
    rms = _rms_with_silence(20, [(0.5, 2.8, 0.30)])
    env = Env(rms)
    ws = _mk_words([("中", 500, 1000), ("文", 1000, 2800)])
    ok, why = judge_line(ws, [], -4.0, 2, env=env)
    assert not ok and "1.6" in why, why


def test_judge_real_long_note_passes():
    # 1456ms + 字内停顿 60ms（真实长音，能量连续）→ 放行
    rms = _rms_with_silence(20, [(3.0, 4.0, 0.30), (4.06, 4.876, 0.30), (0.0, 2.9, 0.2)])
    env = Env(rms)
    ws = _mk_words([("中", 3000, 3400), ("文", 3400, 3420), ("长", 3420, 4876)])
    ok, why = judge_line(ws, [], -4.0, 3, env=env)
    assert ok, why


def test_judge_swallow_pause_rejected():
    # 2306ms + 字内停顿 600ms（吞停顿）→ 拦
    rms = _rms_with_silence(20, [(10.0, 10.8, 0.30), (11.4, 12.0, 0.30)])
    env = Env(rms)
    ws = _mk_words([("前", 9000, 9400), ("好", 9400, 11706)])
    ok, why = judge_line(ws, [], -4.0, 2, env=env)
    assert not ok and "吞停顿" in why, why


def test_judge_soft_width_without_env_rejects():
    ws = _mk_words([("中", 0, 400), ("长", 400, 1700)])
    ok, why = judge_line(ws, [], -4.0, 2, env=None)
    assert not ok and "无包络" in why, why


def test_judge_low_score_with_big_delta():
    ws = _mk_words([("中", 1000, 1200), ("文", 1200, 1400), ("人", 1400, 1600)])
    old = _mk_words([("中", 300, 500), ("文", 500, 700), ("人", 700, 900)])
    ok, why = judge_line(ws, old, -9.5, 3, onsets=[1.0])
    assert not ok and why == "分数低"
    ok, _ = judge_line(ws, old, -8.0, 3, onsets=[1.0])
    assert ok


def test_judge_no_onset_rejects():
    ws = _mk_words([("中", 1000, 1200), ("文", 1200, 1400), ("人", 1400, 1600)])
    old = _mk_words([("中", 300, 500), ("文", 500, 700), ("人", 700, 900)])
    ok, why = judge_line(ws, old, -5.0, 3, onsets=[5.0])      # 远处才有起振
    assert not ok and why == "无起振"
    ok, _ = judge_line(ws, old, -5.0, 3, onsets=[1.0])        # 附近有起振
    assert ok


def test_judge_short_line_lenient():
    # ≤2 字：跳过「分数低/无起振」（实测「糟糕」被误杀）
    ws = _mk_words([("糟", 1000, 1200), ("糕", 1200, 1400)])
    old = _mk_words([("糟", 300, 500), ("糕", 500, 700)])
    ok, _ = judge_line(ws, old, -10.2, 2, onsets=[])
    assert ok


def test_judge_no_ctc_word_excluded_from_widest():
    # 数字词 3000ms 但 no_ctc → 不参与 widest；真词最大 400ms → 通过
    ws = _mk_words([("中", 0, 400), ("2000", 400, 3400), ("文", 3400, 3800)])
    ws[1]["no_ctc"] = True
    ok, why = judge_line(ws, [], -4.0, 6)
    assert ok, why
    # 同一数据若不排除 no_ctc，widest=3000 会被硬上限拦下 —— 证明排除逻辑生效
    ws2 = _mk_words([("中", 0, 400), ("2000", 400, 3400), ("文", 3400, 3800)])
    assert judge_line(ws2, [], -4.0, 6)[0] is False


def test_judge_span_anomaly():
    # 两字各 ≤1200ms（宽度合规），但总跨度 2100 > max(2000, 2×800)
    ws = _mk_words([("中", 0, 1000), ("文", 1000, 2100)])
    ok, why = judge_line(ws, [], -3.0, 2)
    assert not ok and "跨度" in why, why


def test_judge_char_count_mismatch():
    ws = _mk_words([("中", 0, 400)])                           # 声称 3 字只有 1 字
    ok, why = judge_line(ws, [], -3.0, 3)
    assert not ok and why == "字数不符"


def test_apply_gate_reverts_with_old_words():
    bad = _mk_words([("中", 1000, 900), ("文", 800, 3000)])    # 乱序 + 超宽
    lines = [{"start": 1000, "end": 3000, "text": "中文", "words": bad}]
    old = [[{"text": "中", "start": 1000, "end": 1300},
            {"text": "文", "start": 1300, "end": 1600}]]
    rep = apply_gate(lines, scores=[None], anchors=[1000], old_words=old)
    assert len(rep["reverts"]) == 1
    assert lines[0]["words"] == old[0]
    assert lines[0]["end"] == 1600


def test_apply_gate_falls_back_to_linear():
    bad = _mk_words([("中", 500, 30000)])                      # 跨度异常
    lines = [{"start": 500, "end": 30000, "text": "中文人", "words": bad}]
    rep = apply_gate(lines, next_starts=[6000])
    assert rep["fallback_lines"] == [0]
    ws = lines[0]["words"]
    assert len(ws) == 3 and all(w["end"] - w["start"] >= MIN_WORD_MS for w in ws)
    assert "".join(w["text"] for w in ws) == "中文人"


def test_snapshot_restore_roundtrip():
    line = {"start": 1, "end": 9, "duration": 8, "words": [{"text": "a", "start": 1, "end": 9}]}
    snap = snapshot_words(line)
    line["start"] = 100
    line["words"][0]["start"] = 200
    restore_words(line, snap)
    assert line["start"] == 1 and line["words"][0]["start"] == 1


def test_split_display_units_merges_punct():
    units = split_display_units("宮?中文")
    assert units == ["宮?", "中", "文"], units
    assert "".join(units) == "宮?中文"


def test_linear_split_identity_and_width():
    ws = linear_split_words("让我睡不着", 103353, 105060)
    assert "".join(w["text"] for w in ws) == "让我睡不着"
    assert all(w["end"] - w["start"] >= MIN_WORD_MS for w in ws)
    gaps_ok = all(b["start"] - a["end"] >= 0 for a, b in zip(ws, ws[1:]))
    assert gaps_ok


if __name__ == "__main__":
    sys.exit(main(globals()))
