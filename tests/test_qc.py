# -*- coding: utf-8 -*-
"""质检器单测（v1.6.0）——Env / L1 / L2 / L3，纯 numpy，不需要模型。"""
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from _testkit import main  # noqa: E402
from core.acoustic.qc import (  # noqa: E402
    Env, build_report, qc_l1_monotonic, qc_l2_rhythm, qc_l3_recheck, write_report,
)


def _mk_lines(*specs):
    lines = []
    for tag, words in specs:
        ln = {"start": int(tag), "end": int(tag), "text": "x", "words": []}
        if words:
            ln["words"] = [{"text": t, "start": int(s), "end": int(e)} for t, s, e in words]
            ln["end"] = int(words[-1][2])
        lines.append(ln)
    return {"lines": lines}


def test_env_max_dip_detects_internal_silence():
    # 3.0–3.1456 活跃、3.2–3.6 静音、3.6 后活跃 → 「长字」3.0–3.8 内含 400ms 停顿
    n = int(10 * 50)
    rms = np.full(n, 0.30, dtype=np.float32)
    rms[int(3.2 * 50):int(3.6 * 50)] = 0.002
    env = Env(rms)
    dip = env.max_dip_ms(3.0, 3.8)
    assert dip >= 380, dip


def test_env_release_end_finds_silence():
    n = int(10 * 50)
    rms = np.full(n, 0.30, dtype=np.float32)
    rms[int(2.0 * 50):] = 0.001          # 2.0s 后静音
    env = Env(rms)
    end = env.release_end(1.0, 5.0, silent_ms=180)
    assert 1.7 <= end <= 2.1, end
    assert end >= 1.0 + 0.05


def test_env_release_end_floor():
    rms = np.full(int(5 * 50), 0.30, dtype=np.float32)
    env = Env(rms)
    end = env.release_end(1.0, 1.02)      # cap 太紧 → 至少 50ms
    assert 1.05 <= end <= 1.02 + 0.2, end


def test_env_onsets_density_and_near():
    n = int(10 * 50)
    rms = np.zeros(n, dtype=np.float32)
    for t in (1.0, 2.0, 5.0):
        a = int(t * 50)
        rms[a:a + 10] = 0.5
    env = Env(rms)
    ons = env.onsets(0, 10)
    assert any(abs(t - 1.0) < 0.1 for t in ons)
    assert env.near_onset(1000, 150)
    assert not env.near_onset(3000, 150)


def test_l1_fixes_overlap_and_width():
    data = _mk_lines(
        (0, [("a", 0, 100), ("b", 50, 200)]),          # 字重叠
        (150, [("c", 180, 260)]),                      # 行间隙不足
    )
    out = qc_l1_monotonic(data)
    assert out["l1_violations"] > 0
    for ln in out["lines"]:
        ws = ln["words"]
        for a, b in zip(ws, ws[1:]):
            assert b["start"] >= a["end"] - 1 or b["start"] >= a["start"], (a, b)
        assert all(w["end"] - w["start"] >= 50 for w in ws), ws
        assert ln["end"] == ws[-1]["end"]              # 恒等式


def test_l1_zero_violation_on_clean_data():
    data = _mk_lines(
        (0, [("a", 0, 200), ("b", 200, 400)]),
        (600, [("c", 600, 800)]),
    )
    out = qc_l1_monotonic(data)
    assert out["l1_violations"] == 0, out


def test_l2_flags_rate_outliers_only_as_warning():
    data = _mk_lines(
        (0, [("快", 0, 60), ("嘴", 60, 122)]),                       # 61ms/字 → too_fast
        (1000, [("拖", 1000, 1000 + 900 * 3)]),                      # 900ms/字 → too_slow
        (5000, [("合", 5000, 5200), ("适", 5200, 5400)]),            # 200ms/字 → 无告警
    )
    warns = qc_l2_rhythm(data)
    reasons = {w["reason"] for w in warns}
    assert "too_fast" in reasons and "too_slow" in reasons
    assert len(warns) == 2


def test_l3_keep_when_three_windows_agree():
    # 真实含义：三个窗口都定位到同一个物理起振点 → 离散度 0 → 真实演唱位置
    line = {"text": "中文", "words": []}
    rep = qc_l3_recheck(line, 103.35, aligner=lambda a, b, t: 103.35, spread_tol=80)
    assert rep["verdict"] == "keep", rep
    assert rep["spread_ms"] == 0


def test_l3_unstable_picks_closest():
    line = {"text": "中文", "words": []}
    vals = {103.0: 103.9, 103.35: 103.4, 103.7: 104.2}
    rep = qc_l3_recheck(line, 103.0, aligner=lambda a, b, t: vals[round(a, 2)], spread_tol=80)
    assert rep["verdict"] == "unstable"
    assert abs(rep["picked"] - 103.0) <= 0.5


def test_l3_never_crashes_on_aligner_exception():
    line = {"text": "中文", "words": []}

    def boom(a, b, t):
        raise RuntimeError("x")

    rep = qc_l3_recheck(line, 5.0, aligner=boom)
    assert rep["verdict"] == "unstable"


def test_build_report_shape():
    data = _mk_lines((0, [("a", 0, 200)]), (600, [("b", 600, 900)]))
    qc_l1_monotonic(data)
    rep = build_report(data, engine="static", audio="x.flac", duration_s=9.0)
    assert rep["engine"] == "static" and rep["l1_violations"] == 0
    assert rep["summary"]["lines"] == 2 and rep["summary"]["chars"] == 2
    assert isinstance(rep["l2_warnings"], list)


def test_write_report_creates_dirs(tmp_path=None):
    import tempfile, os
    d = tempfile.mkdtemp()
    path = os.path.join(d, "nested", "qc.json")
    write_report(path, {"ok": True})
    assert os.path.isfile(path)


if __name__ == "__main__":
    sys.exit(main(globals()))
