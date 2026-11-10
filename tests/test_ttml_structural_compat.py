# -*- coding: utf-8 -*-
"""v1.8.3 P1: TTML 结构语义、div 边界推导与复杂角色阻断验收测试 (DIV-01~04, ROLE-01~06)。

验证原则：
- 所有输入文本与结构均为合成原创，严禁使用真实版权歌词。
- 复杂拓扑（x-bg、x-roman、Ruby/嵌套span、多div、seq时序）严格阻断，绝不静默吞词丢轴。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auditors.elrc_ttml_comparator import (
    compare_elrc_ttml,
    parse_ttml_structural,
)
from tests._testkit import run


def test_div_01_inferred_boundary_without_explicit_attrs():
    """DIV-01: div 缺失 begin/end 属性时，从子行推导边界并附带 DIV_BOUNDARY_INFERRED。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
        'xml:lang="en" itunes:timing="Word">'
        '<body><div>'
        '<p begin="00:00:10.000" end="00:00:15.000" itunes:key="L1">'
        '<span begin="00:00:10.000" end="00:00:12.000">Synthetic</span> '
        '<span begin="00:00:12.000" end="00:00:15.000">lyric</span>'
        '</p>'
        '<p begin="00:00:16.000" end="00:00:20.000" itunes:key="L2">'
        '<span begin="00:00:16.000" end="00:00:20.000">Ending</span>'
        '</p>'
        '</div></body></tt>'
    )
    lines, bound, issues = parse_ttml_structural(ttml)
    assert bound == (10000, 20000), f"Expected inferred bound (10000, 20000), got {bound}"
    assert any(iss.code == "DIV_BOUNDARY_INFERRED" and not iss.blocking for iss in issues)
    blocking = [iss for iss in issues if iss.blocking]
    assert len(blocking) == 0, f"Unexpected blocking issues: {blocking}"

    # 与对应 ELRC 进行双格式比较，能够正确通过且标注 div_inferred
    elrc = (
        "[00:10.000]<00:10.000>Synthetic<00:12.000> lyric<00:15.000>\n"
        "[00:16.000]<00:16.000>Ending<00:20.000>\n"
    )
    res = compare_elrc_ttml(elrc, ttml)
    assert res.passed is True, f"Comparison should pass: {[i.code for i in res.issues]}"
    assert res.div_inferred is True


def test_div_02_explicit_boundary_valid():
    """DIV-02: div 显式 begin/end 正确且包容子行时，审计完全通过。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
        'xml:lang="en" itunes:timing="Word">'
        '<body><div begin="00:00:10.000" end="00:00:20.000">'
        '<p begin="00:00:10.000" end="00:00:20.000" itunes:key="L1">'
        '<span begin="00:00:10.000" end="00:00:20.000">Valid</span>'
        '</p>'
        '</div></body></tt>'
    )
    lines, bound, issues = parse_ttml_structural(ttml)
    assert bound == (10000, 20000)
    blocking = [iss for iss in issues if iss.blocking]
    assert len(blocking) == 0


def test_div_03_explicit_boundary_conflict_blocks():
    """DIV-03: div 显式边界与子行范围冲突时，阻断返回 DIV_TIME_CONFLICT。"""
    # 子行延伸到 25000ms，但 div 声明在 20000ms 结束
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
        'xml:lang="en" itunes:timing="Word">'
        '<body><div begin="00:00:10.000" end="00:00:20.000">'
        '<p begin="00:00:10.000" end="00:00:25.000" itunes:key="L1">'
        '<span begin="00:00:10.000" end="00:00:25.000">Overrunning</span>'
        '</p>'
        '</div></body></tt>'
    )
    lines, bound, issues = parse_ttml_structural(ttml)
    assert any(iss.code == "DIV_TIME_CONFLICT" and iss.blocking for iss in issues)


def test_div_04_unsupported_timing_model_blocks():
    """DIV-04: 父级相对时间、timeContainer="seq" 或非标准 dur 必须明确阻断。"""
    # 案例 A: div 指定 timeContainer="seq"
    seq_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<body><div timeContainer="seq">'
        '<p begin="00:00:01.000" end="00:00:05.000"><span begin="00:00:01.000" end="00:00:05.000">A</span></p>'
        '</div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(seq_ttml)
    assert any(iss.code == "TIMING_MODEL_UNSUPPORTED" for iss in issues)

    # 案例 B: p 使用 dur 代替绝对时间
    dur_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml">'
        '<body><div>'
        '<p begin="00:00:01.000" dur="00:00:04.000"><span>A</span></p>'
        '</div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(dur_ttml)
    assert any(iss.code == "TIMING_MODEL_UNSUPPORTED" for iss in issues)


def test_role_01_translation_only_isolated_from_main():
    """ROLE-01: x-translation 仅进入译文轨，不得污染主唱词序列。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" '
        'xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<body><div begin="00:00:01.000" end="00:00:05.000">'
        '<p begin="00:00:01.000" end="00:00:05.000" itunes:key="L1">'
        '<span begin="00:00:01.000" end="00:00:05.000">Original text</span>'
        '<span ttm:role="x-translation">对应翻译文本</span>'
        '</p></div></body></tt>'
    )
    lines, _, issues = parse_ttml_structural(ttml)
    blocking = [iss for iss in issues if iss.blocking]
    assert len(blocking) == 0
    assert len(lines) == 1
    assert lines[0].text == "Original text"
    assert lines[0].translation == "对应翻译文本"
    assert len(lines[0].words) == 1
    assert lines[0].words[0].text == "Original text"


def test_role_02_roman_track_blocks_lossless():
    """ROLE-02: 含有 x-roman 辅助音节轨时，阻断返回 AUXILIARY_TRACK_UNSUPPORTED，绝不拼入歌词。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000">'
        '<span begin="00:00:01.000" end="00:00:05.000">歌词</span>'
        '<span ttm:role="x-roman" begin="00:00:01.000" end="00:00:05.000">ge ci</span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(ttml)
    assert any(iss.code == "AUXILIARY_TRACK_UNSUPPORTED" for iss in issues)


def test_role_03_background_vocal_blocks_lossless():
    """ROLE-03: 含有 x-bg 和声/背景声部时，阻断返回 BACKGROUND_VOCAL_UNSUPPORTED，绝不静默丢失。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000">'
        '<span begin="00:00:01.000" end="00:00:05.000">主唱歌词</span>'
        '<span ttm:role="x-bg" begin="00:00:03.000" end="00:00:05.000">(和声)</span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(ttml)
    assert any(iss.code == "BACKGROUND_VOCAL_UNSUPPORTED" for iss in issues)


def test_role_04_ruby_and_nested_span_blocks():
    """ROLE-04: Ruby 注音与嵌套 span 结构必须明确阻断 RUBY_UNSUPPORTED，不吞词。"""
    # 案例 A: 带有 tts:ruby
    ruby_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling">'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000">'
        '<span begin="00:00:01.000" end="00:00:05.000" tts:ruby="container">漢字</span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(ruby_ttml)
    assert any(iss.code == "RUBY_UNSUPPORTED" for iss in issues)

    # 案例 B: 嵌套 span
    nested_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml">'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000">'
        '<span begin="00:00:01.000" end="00:00:05.000"><span begin="00:00:01.000" end="00:00:02.000">Nest</span></span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(nested_ttml)
    assert any(iss.code == "RUBY_UNSUPPORTED" for iss in issues)


def test_role_05_multi_div_blocks():
    """ROLE-05: 多 div 平行段必须明确阻断 MULTI_DIV_UNSUPPORTED，不得只读第一个。"""
    multi_div_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml">'
        '<body>'
        '<div begin="00:00:01.000" end="00:00:05.000"><p begin="00:00:01.000" end="00:00:05.000"><span>A</span></p></div>'
        '<div begin="00:00:06.000" end="00:00:10.000"><p begin="00:00:06.000" end="00:00:10.000"><span>B</span></p></div>'
        '</body></tt>'
    )
    _, _, issues = parse_ttml_structural(multi_div_ttml)
    assert any(iss.code == "MULTI_DIV_UNSUPPORTED" for iss in issues)


def test_role_06_hanging_translation_and_undeclared_agent_blocks():
    """ROLE-06: 翻译悬空、重复行键或引用未声明的 Agent 必须明确报错。"""
    # 案例 A: 引用未声明的 Agent
    agent_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<head><metadata></metadata></head>'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000" itunes:key="L1" itunes:agent="unknown_singer">'
        '<span begin="00:00:01.000" end="00:00:05.000">Lyric</span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(agent_ttml)
    assert any(iss.code == "TRANSLATION_AGENT_MISMATCH" for iss in issues)

    # 案例 B: 翻译悬空 (for='L99' 不存在)
    hanging_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<head><metadata><iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal">'
        '<translations><translation xml:lang="zh" type="subtitle">'
        '<text for="L99">悬空译文</text>'
        '</translation></translations></iTunesMetadata></metadata></head>'
        '<body><div>'
        '<p begin="00:00:01.000" end="00:00:05.000" itunes:key="L1">'
        '<span begin="00:00:01.000" end="00:00:05.000">Lyric</span>'
        '</p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(hanging_ttml)
    assert any(iss.code == "TRANSLATION_HANGING_REFERENCE" for iss in issues)


if __name__ == "__main__":
    sys.exit(run(globals()))
