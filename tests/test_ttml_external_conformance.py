# -*- coding: utf-8 -*-
"""v1.8.3 P0: 独立外部 TTML 时间输出规范性与输入兼容性验收 (TIME-01 ~ TIME-06)。

与生产渲染器、解析器完全解耦，使用独立严谨词法测试器进行符合性验收。
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auditors.elrc_ttml_comparator import (
    compare_elrc_ttml,
    parse_ttml_structural,
    parse_ttml_time_ms,
    verify_canonical_exports,
)
from core.models.canonical_lyrics import (
    CanonicalLine,
    CanonicalLyrics,
    CanonicalWord,
    LineRole,
    PreludePlan,
)
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import ms_to_ttml_time, render_ttml_v182
from tests._testkit import run

# 独立词法正则（不共享生产代码），严格匹配 W3C TTML §10.3 clock-time
# 要求：至少两位小时:两位分钟(00-59):两位秒(00-59).三位毫秒
_INDEPENDENT_W3C_CLOCK_RE = re.compile(r"^\d{2,}:([0-5]\d):([0-5]\d)\.(\d{3})$")


def _is_independent_w3c_clock(val: str) -> bool:
    """独立的外部 W3C clock-time 词法检查器。"""
    if not isinstance(val, str):
        return False
    return bool(_INDEPENDENT_W3C_CLOCK_RE.match(val.strip()))


def _build_sample_canonical() -> CanonicalLyrics:
    c = CanonicalLyrics()
    c.song = {"title": "合规测试歌曲", "extracted_lyricist": "词作者", "extracted_composer": "曲作者"}
    c.prelude = PreludePlan(
        mode="A",
        t_sing_ms=25000,
        title_window=(1500, 12000),
        lyricist_window=(12000, 16000),
        composer_window=(16000, 20000),
        display_title="合规测试歌曲",
        display_lyricist="词：词作者",
        display_composer="曲：曲作者",
    )
    l1 = CanonicalLine(
        stable_id="L1",
        source_index=0,
        role=LineRole.SINGING,
        start_ms=25000,
        end_ms=28000,
        text="Standard TTML output",
        words=[
            CanonicalWord(text="Standard", start_ms=25000, end_ms=26000),
            CanonicalWord(text="TTML", start_ms=26000, end_ms=27000, join=" "),
            CanonicalWord(text="output", start_ms=27000, end_ms=28000, join=" "),
        ],
        translation="标准输出",
    )
    c.lines.append(l1)
    return c


def test_time_01_output_formatting_values():
    """TIME-01: ms_to_ttml_time 严格按任务书 §2.1 格式化全部关键时间点。"""
    cases = [
        (0, "00:00:00.000"),
        (1, "00:00:00.001"),
        (15000, "00:00:15.000"),
        (59999, "00:00:59.999"),
        (60000, "00:01:00.000"),
        (65432, "00:01:05.432"),
        (3599999, "00:59:59.999"),
        (3600000, "01:00:00.000"),
        (3661234, "01:01:01.234"),
        (360000000, "100:00:00.000"),  # >99 小时
    ]
    for ms, expected in cases:
        got = ms_to_ttml_time(ms)
        assert got == expected, f"ms={ms}: expected {expected}, got {got}"

    # 负数显式抛出 ValueError
    try:
        ms_to_ttml_time(-1)
        assert False, "Should raise ValueError for negative timestamp"
    except ValueError:
        pass


def test_time_02_tolerant_apple_amll_input():
    """TIME-02: 兼容 Apple/AMLL 两段式、三段式和秒数 offset 输入，解析到同一毫秒。"""
    candidates = ["00:15.000", "00:00:15.000", "15.000s", "15.000"]
    for s in candidates:
        parsed = parse_ttml_time_ms(s, profile="APPLE_AMLL_INPUT")
        assert parsed == 15000, f"Failed to parse '{s}' to 15000ms, got {parsed}"

    # W3C_CLOCK 严格模式下，两段式必须被拒绝
    assert parse_ttml_time_ms("00:15.000", profile="W3C_CLOCK") is None
    assert parse_ttml_time_ms("00:00:15.000", profile="W3C_CLOCK") == 15000


def test_time_03_invalid_colon_times_rejected():
    """TIME-03: 非法冒号时间（秒>=60、分>=60）必须严格拒绝。"""
    invalids = ["00:75.000", "00:00:75.000", "00:60:00.000", "25:61:00.000"]
    for s in invalids:
        assert parse_ttml_time_ms(s, profile="W3C_CLOCK") is None, f"'{s}' should be rejected in W3C_CLOCK"
        assert parse_ttml_time_ms(s, profile="APPLE_AMLL_INPUT") is None, f"'{s}' should be rejected in APPLE_AMLL_INPUT"


def test_time_04_garbage_negative_empty_rejected():
    """TIME-04: 负值、NaN、尾随垃圾、空时间绝不误认为 0ms。"""
    garbages = ["-00:00:15.000", "-15.000", "NaN", "Infinity", "00:00:15.000 trailing", "", None]
    for s in garbages:
        assert parse_ttml_time_ms(s) is None, f"'{s}' must return None"


def test_time_05_independent_w3c_clock_oracle():
    """TIME-05: 独立外部校验器核验：新 TTML 所有时间属性全为三段式；旧两段式被独立拒绝。"""
    c = _build_sample_canonical()
    new_ttml = render_ttml_v182(c)

    # 1. 独立检查新生成的 TTML 每个 begin/end 属性
    root = ET.fromstring(new_ttml)
    checked_count = 0
    for elem in root.iter():
        for attr in ("begin", "end"):
            if attr in elem.attrib:
                val = elem.attrib[attr]
                assert _is_independent_w3c_clock(val), f"Tag <{elem.tag}> attr {attr}='{val}' not valid W3C clock-time"
                checked_count += 1
    assert checked_count >= 5, f"Expected at least 5 checked timestamps, found {checked_count}"

    # 2. 独立校验器必须能辨别并拒绝旧版 MM:SS.mmm 格式
    old_sample = 'begin="00:25.000"'
    assert not _is_independent_w3c_clock("00:25.000"), "Independent validator must reject 2-segment format"


def test_time_06_legacy_input_to_canonical_roundtrip():
    """TIME-06: 旧 v1.8.2 MM:SS TTML 作为输入无损解析，再生成输出为标准新格式。"""
    legacy_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
        'xml:lang="en" itunes:timing="Word">'
        '<body><div begin="00:01.000" end="00:03.000">'
        '<p begin="00:01.000" end="00:03.000" itunes:key="L1">'
        '<span begin="00:01.000" end="00:02.000">Hello</span> '
        '<span begin="00:02.000" end="00:03.000">world</span>'
        '</p></div></body></tt>'
    )
    # 输入能够被解析，且 issues 为空
    lines, bound, issues = parse_ttml_structural(legacy_ttml)
    blocking_issues = [i for i in issues if i.blocking]
    assert len(blocking_issues) == 0, f"Legacy TTML should parse without blocking issues: {blocking_issues}"
    assert len(lines) == 1
    assert bound == (1000, 3000)
    assert lines[0].start_ms == 1000
    assert lines[0].end_ms == 3000
    assert len(lines[0].words) == 2

    # 重建 Canonical 并导出为新 TTML
    c = CanonicalLyrics()
    c.lines.append(CanonicalLine(
        stable_id="L1",
        source_index=0,
        role=LineRole.SINGING,
        start_ms=1000,
        end_ms=3000,
        text="Hello world",
        words=[
            CanonicalWord(text="Hello", start_ms=1000, end_ms=2000),
            CanonicalWord(text="world", start_ms=2000, end_ms=3000, join=" "),
        ]
    ))
    new_ttml = render_ttml_v182(c)
    assert 'begin="00:00:01.000"' in new_ttml
    assert 'end="00:00:03.000"' in new_ttml
    assert 'begin="00:01.000"' not in new_ttml


if __name__ == "__main__":
    sys.exit(run(globals()))
