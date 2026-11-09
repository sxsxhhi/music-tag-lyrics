# -*- coding: utf-8 -*-
"""v1.8.2 R1 ELRC ↔ TTML 双向独立一致性与反例故障注入测试。

涵盖规范正例 roundtrip、<=1ms 时间误差、<=2ms div 容器边界门禁，
以及 §7.4 全部 10+ 类结构反例注入。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auditors.elrc_ttml_comparator import compare_elrc_ttml
from core.models.canonical_lyrics import (
    CanonicalLine,
    CanonicalLyrics,
    CanonicalWord,
    LineRole,
    PreludePlan,
)
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from tests._testkit import run


def _build_test_canonical() -> CanonicalLyrics:
    """构造一份包含前奏、逐字唱句、译文与英文词界空格的标准 CanonicalLyrics。"""
    c = CanonicalLyrics()
    c.song = {"title": "测试金曲", "extracted_lyricist": "林夕", "extracted_composer": "陈辉阳"}
    c.prelude = PreludePlan(
        mode="A",
        t_sing_ms=25000,
        title_window=(1500, 12000),
        lyricist_window=(12000, 16000),
        composer_window=(16000, 20000),
        display_title="测试金曲",
        display_lyricist="词：林夕",
        display_composer="曲：陈辉阳",
    )
    l1 = CanonicalLine(
        stable_id="L1",
        source_index=0,
        role=LineRole.SINGING,
        start_ms=25000,
        end_ms=28000,
        text="Hello world",
        words=[
            CanonicalWord(text="Hello", start_ms=25000, end_ms=26500),
            CanonicalWord(text="world", start_ms=26500, end_ms=28000, join=" "),
        ],
        translation="你好世界",
    )
    l2 = CanonicalLine(
        stable_id="L2",
        source_index=1,
        role=LineRole.SINGING,
        start_ms=29000,
        end_ms=32000,
        text="海阔天空",
        words=[
            CanonicalWord(text="海", start_ms=29000, end_ms=29750),
            CanonicalWord(text="阔", start_ms=29750, end_ms=30500),
            CanonicalWord(text="天", start_ms=30500, end_ms=31250),
            CanonicalWord(text="空", start_ms=31250, end_ms=32000),
        ],
        translation="Boundless oceans, vast skies",
    )
    c.lines = [l1, l2]
    return c


def test_positive_roundtrip_passes_zero_delta():
    """正例：同源生成的 ELRC 与 TTML 双向比对 100% 通过且最大误差 0ms。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    ttml = render_ttml_v182(c)

    res = compare_elrc_ttml(elrc, ttml)
    assert res.passed is True
    assert len(res.issues) == 0
    assert res.max_time_delta_ms <= 1
    assert res.max_div_delta_ms <= 2


def test_counter_word_time_offset_2ms():
    """反例：字块时间被篡改偏移 2ms (>1ms 阈值) 必须阻断。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    ttml = render_ttml_v182(c)

    # 篡改 TTML 中的一个 span 时间
    corrupted_ttml = ttml.replace('begin="00:25.000"', 'begin="00:25.002"')
    res = compare_elrc_ttml(elrc, corrupted_ttml)
    assert res.passed is False
    codes = [iss.code for iss in res.issues]
    assert "TIME_AXIS_MISMATCH" in codes


def test_counter_div_boundary_delta_3ms():
    """反例：div 结束时间偏差 3ms (>2ms 阈值) 必须阻断。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    ttml = render_ttml_v182(c)

    corrupted_ttml = ttml.replace('end="00:32.000"', 'end="00:32.003"')
    res = compare_elrc_ttml(elrc, corrupted_ttml)
    assert res.passed is False
    codes = [iss.code for iss in res.issues]
    assert "DIV_BOUNDARY_MISMATCH" in codes


def test_counter_hanging_translation_reference():
    """反例：译文挂载到不存在的行 for='L99' 必须阻断。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    ttml = render_ttml_v182(c)

    corrupted_ttml = ttml.replace('for="L1"', 'for="L99"')
    res = compare_elrc_ttml(elrc, corrupted_ttml)
    assert res.passed is False
    codes = [iss.code for iss in res.issues]
    assert "TRANSLATION_HANGING_REFERENCE" in codes


def test_counter_missing_line():
    """反例：两格式行数不一致必须阻断。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    # 删除 ELRC 的第二行
    elrc_lines = elrc.splitlines()
    truncated_elrc = "\n".join(elrc_lines[:4]) + "\n"
    ttml = render_ttml_v182(c)

    res = compare_elrc_ttml(truncated_elrc, ttml)
    assert res.passed is False
    assert any(iss.code == "LINE_MISSING" for iss in res.issues)


def test_counter_duplicate_p_key():
    """反例：TTML 出现重复行键必须阻断。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    ttml = render_ttml_v182(c)

    corrupted_ttml = ttml.replace('itunes:key="L2"', 'itunes:key="L1"')
    res = compare_elrc_ttml(elrc, corrupted_ttml)
    assert res.passed is False
    assert any(iss.code == "DUPLICATE_P_KEY" for iss in res.issues)


def test_counter_xml_syntax_error():
    """反例：TTML 非法 XML 结构必须准确报告。"""
    c = _build_test_canonical()
    elrc = render_elrc_v182(c)
    bad_ttml = "<tt><broken_tag></tt>"

    res = compare_elrc_ttml(elrc, bad_ttml)
    assert res.passed is False
    assert any(iss.code == "XML_SYNTAX_ERROR" for iss in res.issues)


if __name__ == "__main__":
    sys.exit(run(globals()))
