# -*- coding: utf-8 -*-
"""line_axis 单测（v1.6.0）：行轴语义证据链 + 三段裁决 + 幂等。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _testkit import main, skip  # noqa: E402
from core.utils.line_axis import (  # noqa: E402
    AXIS_DISPLAY, AXIS_OFFICIAL, AXIS_SING, AXIS_UNKNOWN,
    mark_line_axis, infer_from_words, calibrate_line_axis, check_line_axis,
)


def _mk_lines(*specs):
    """specs: (tag_ms, [(word_text, start_ms, end_ms), ...]) 或 (tag_ms, None) 表示纯译文行。"""
    lines = []
    for tag, words in specs:
        ln = {"start": int(tag), "end": int(tag) + 1000, "text": "x", "words": []}
        if words:
            ln["words"] = [{"text": t, "start": int(s), "end": int(e),
                            "duration": int(e) - int(s)} for t, s, e in words]
        lines.append(ln)
    return {"lines": lines}


def test_mark_axis_line_level_is_display():
    data = _mk_lines((23200, None), (26530, None))
    assert mark_line_axis(data, source="netease") == AXIS_DISPLAY
    assert data["line_axis"] == AXIS_DISPLAY


def test_mark_axis_ttml_is_official():
    data = _mk_lines((23200, None))
    assert mark_line_axis(data, source="apple-ttml") == AXIS_OFFICIAL


def test_mark_axis_word_level_sing_inferred():
    # lead 全 0 → sing；且 krc 这类带词源升格为 official
    data = _mk_lines((1000, [("那", 1000, 1200)]), (2000, [("么", 2000, 2200)]))
    assert mark_line_axis(data, source="netease") == AXIS_SING
    data2 = _mk_lines((1000, [("那", 1000, 1200)]))
    assert mark_line_axis(data2, source="kugou") == AXIS_OFFICIAL


def test_mark_axis_display_inferred_by_median():
    # 典型显示轴：lead 中位 ≈880ms
    data = _mk_lines(
        (0, [("a", 880, 1000)]),
        (1000, [("b", 1850, 2000)]),
        (2000, [("c", 2900, 2100)]),
    )
    data["lines"][2]["words"][0]["start"] = 2880
    assert infer_from_words(data) == AXIS_DISPLAY


def test_mark_axis_unknown_when_undecidable():
    data = _mk_lines((0, [("a", 30, 100)]), (1000, [("b", 1010, 1100)]))
    assert infer_from_words(data) == AXIS_UNKNOWN


def test_calibrate_normal_sinking():
    data = _mk_lines((2000, [("皇", 2260, 2500)]))
    data["line_axis"] = AXIS_DISPLAY
    calibrate_line_axis(data)
    ln = data["lines"][0]
    assert ln["start"] == 2260 - 60, ln["start"]
    ok, bad = check_line_axis(data)
    assert ok and not bad


def test_calibrate_shrink_prev_word():
    # 上一行末字 200ms、本行首字 1030：② 档应把上一行末字收到 980，本行 tag=1030，gap=50
    data = _mk_lines(
        (0, [("前", 0, 1200)]),
        (900, [("后", 1030, 1400)]),
    )
    data["line_axis"] = AXIS_DISPLAY
    calibrate_line_axis(data)
    l0, l1 = data["lines"]
    assert l0["words"][-1]["end"] == 980, l0["words"][-1]
    assert l1["start"] == 980 + 50, l1["start"]
    assert l1["words"][0]["start"] - l1["start"] == 0   # gap 恰好 50 → lead 0
    assert l1["words"][0]["start"] >= l1["start"]


def test_calibrate_shift_line_when_infeasible():
    # ③ 档：上一行末字已是 50ms 下限仍压不下来（900–950，首字 980）→ 整行后推
    # shift = (950+50) − 980 = 20ms；gap 恰为 50；上界 ≤99ms
    data = _mk_lines(
        (0, [("甲", 900, 950)]),
        (800, [("乙", 980, 1400)]),
    )
    data["line_axis"] = AXIS_DISPLAY
    calibrate_line_axis(data)
    l0, l1 = data["lines"]
    gap = l1["words"][0]["start"] - l0["words"][-1]["end"]
    assert gap >= 50, gap
    assert l1["words"][0]["start"] >= l1["start"]
    assert data["line_axis_shifted_lines"] == 1
    assert data["line_axis_warns"]
    assert 0 < data["line_axis_warns"][0]["shift_ms"] <= 99
    assert l1["words"][0]["start"] == 1000, l1["words"][0]


def test_calibrate_translation_line_follows():
    # 同时间戳的独立译文行必须跟随主行位移
    data = _mk_lines(
        (2000, [("皇", 2260, 2500)]),
        (2000, None),                     # 纯译文行（无 words）
        (4000, [("后", 4260, 4500)]),
    )
    data["line_axis"] = AXIS_DISPLAY
    calibrate_line_axis(data)
    assert data["lines"][1]["start"] == data["lines"][0]["start"]


def test_calibrate_official_and_unknown_untouched():
    for axis in (AXIS_OFFICIAL, AXIS_UNKNOWN, AXIS_SING):
        data = _mk_lines((2000, [("皇", 2000, 2500)]))
        data["line_axis"] = axis
        before = [dict(l) for l in data["lines"]]
        calibrate_line_axis(data)
        assert data["lines"][0]["start"] == before[0]["start"], axis
        assert data["line_axis"] == axis, axis


def test_calibrate_idempotent():
    data = _mk_lines((2000, [("皇", 2260, 2500)]))
    data["line_axis"] = AXIS_DISPLAY
    calibrate_line_axis(data)
    snap = data["lines"][0]["start"]
    assert data["line_axis"] == AXIS_SING
    calibrate_line_axis(data)                     # 二次调用必须零改动
    assert data["lines"][0]["start"] == snap


def test_check_line_axis_tolerances():
    data = _mk_lines((2200, [("皇", 2260, 2500)]))     # lead +60 合规
    ok, bad = check_line_axis(data)
    assert ok and not bad
    data["lines"][0]["start"] = 2500                    # 行签晚于首字 → 违规
    ok, bad = check_line_axis(data)
    assert not ok and len(bad) == 1


def test_env_free_and_fast():
    # 自检：整套流程不应触发任何模型/包络加载
    import core.utils.line_axis as la
    assert la.MIN_WORD_MS == 50


if __name__ == "__main__":
    sys.exit(main(globals()))
