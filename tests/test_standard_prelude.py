# -*- coding: utf-8 -*-
"""v1.8.2 R3 真实首唱确认与 A/B 前奏时序排版测试。

严格验证任务书 §4.4 AB01–AB12 数值矩阵及字块时间轴绝对不可变契约。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.models.canonical_lyrics import (
    CanonicalLine,
    CanonicalLyrics,
    CanonicalWord,
    IssueSeverity,
    LineRole,
)
from core.schedulers.prelude_scheduler import find_first_singing_time, plan_prelude_ab
from tests._testkit import run


def _make_sample_canonical(
    first_sing_ms: int,
    lyricist: str = "",
    composer: str = "",
    title: str = "示例歌曲",
    has_words: bool = True,
) -> CanonicalLyrics:
    """构造用于前奏时序测试的 CanonicalLyrics 样例。"""
    c = CanonicalLyrics()
    c.song = {
        "title": title,
        "extracted_lyricist": lyricist or None,
        "extracted_composer": composer or None,
    }
    words = []
    if has_words:
        words = [
            CanonicalWord(text="起", start_ms=first_sing_ms, end_ms=first_sing_ms + 500),
            CanonicalWord(text="唱", start_ms=first_sing_ms + 500, end_ms=first_sing_ms + 1000),
        ]
    line = CanonicalLine(
        stable_id="L1",
        source_index=0,
        role=LineRole.SINGING,
        start_ms=first_sing_ms,
        end_ms=first_sing_ms + 2000,
        text="起唱测试句",
        words=words,
    )
    c.lines = [line]
    return c


def test_ab01_30000ms_both_credits():
    """AB01: T=30000ms -> A模式 标题 1500-12000，词 12000-16000，曲 16000-20000。"""
    c = _make_sample_canonical(30000, lyricist="张三", composer="李四")
    h_before = c.singing_words_hash()
    plan = plan_prelude_ab(c)
    assert plan.mode == "A"
    assert plan.title_window == (1500, 12000)
    assert plan.lyricist_window == (12000, 16000)
    assert plan.composer_window == (16000, 20000)
    assert plan.display_lyricist == "词：张三"
    assert plan.display_composer == "曲：李四"
    assert c.singing_words_hash() == h_before


def test_ab02_20001ms_boundary_a():
    """AB02: T=20001ms -> 严格判定为 A 模式。"""
    c = _make_sample_canonical(20001, lyricist="张三", composer="李四")
    plan = plan_prelude_ab(c)
    assert plan.mode == "A"
    assert plan.title_window == (1500, 12000)


def test_ab03_20000ms_boundary_b():
    """AB03: T=20000ms -> 严格判定为 B 模式，0-12000, 12000-16000, 16000-20000。"""
    c = _make_sample_canonical(20000, lyricist="张三", composer="李四")
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 12000)
    assert plan.lyricist_window == (12000, 16000)
    assert plan.composer_window == (16000, 20000)


def test_ab04_15000ms_b():
    """AB04: T=15000ms -> B: 0-9000, 9000-12000, 12000-15000。"""
    c = _make_sample_canonical(15000, lyricist="张三", composer="李四")
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 9000)
    assert plan.lyricist_window == (9000, 12000)
    assert plan.composer_window == (12000, 15000)


def test_ab05_10001ms_b_integer_floor():
    """AB05: T=10001ms -> 整数取整 0-6000, 6000-8000, 8000-10001。"""
    c = _make_sample_canonical(10001, lyricist="张三", composer="李四")
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 6000)
    assert plan.lyricist_window == (6000, 8000)
    assert plan.composer_window == (8000, 10001)


def test_ab06_15000ms_only_lyricist():
    """AB06: T=15000ms，仅作词可信 -> 0-9000 标题，9000-15000 词，不输出曲。"""
    c = _make_sample_canonical(15000, lyricist="张三", composer="")
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 9000)
    assert plan.lyricist_window == (9000, 15000)
    assert plan.composer_window is None
    assert plan.display_composer is None


def test_ab07_30000ms_only_composer():
    """AB07: T=30000ms，仅作曲可信 -> 1500-12000 标题，12000-20000 曲，不输出词。"""
    c = _make_sample_canonical(30000, lyricist="", composer="李四")
    plan = plan_prelude_ab(c)
    assert plan.mode == "A"
    assert plan.title_window == (1500, 12000)
    assert plan.lyricist_window is None
    assert plan.composer_window == (12000, 20000)
    assert plan.display_lyricist is None


def test_ab08_15000ms_no_credits():
    """AB08: T=15000ms，词曲皆无 -> 仅 0-9000 标题，两项皆无不输出空行。"""
    c = _make_sample_canonical(15000, lyricist="", composer="")
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 9000)
    assert plan.lyricist_window is None
    assert plan.composer_window is None


def test_ab09_10ms_short_prelude():
    """AB09: T=10ms，极短前奏 -> 合法整数区间分配，真实演唱轴 10ms 保持不变。"""
    c = _make_sample_canonical(10, lyricist="甲", composer="乙")
    h_before = c.singing_words_hash()
    plan = plan_prelude_ab(c)
    assert plan.mode == "B"
    assert plan.title_window == (0, 6)
    assert plan.lyricist_window == (6, 8)
    assert plan.composer_window == (8, 10)
    assert c.singing_words_hash() == h_before
    assert c.lines[0].words[0].start_ms == 10


def test_ab10_zero_or_no_singing():
    """AB10: T=0ms 或无真实首唱 -> 报告无法排版，不平移唱词。"""
    c = _make_sample_canonical(0, lyricist="甲", composer="乙")
    plan = plan_prelude_ab(c)
    assert plan.mode == "NONE"
    issues = [iss for iss in c.issues if iss.code == "PRELUDE_NO_ROOM"]
    assert len(issues) >= 1


def test_ab11_first_line_lacks_word_axis_blocks():
    """AB11: 首句缺字轴但第二句在 15s -> 严禁借第二句充当 T，报告 FIRST_SING_UNVERIFIED。"""
    c = CanonicalLyrics()
    c.song = {"title": "测试"}
    l1 = CanonicalLine(
        stable_id="L1", source_index=0, role=LineRole.SINGING,
        start_ms=5000, end_ms=8000, text="首句唱词无字轴", words=[],
    )
    l2 = CanonicalLine(
        stable_id="L2", source_index=1, role=LineRole.SINGING,
        start_ms=15000, end_ms=18000, text="第二句有字轴",
        words=[CanonicalWord(text="二", start_ms=15000, end_ms=16000)],
    )
    c.lines = [l1, l2]
    plan = plan_prelude_ab(c)
    assert plan.mode == "NONE"
    issues = [iss for iss in c.issues if iss.code == "FIRST_SING_UNVERIFIED"]
    assert len(issues) >= 1
    assert issues[0].blocking is True


def test_ab12_outro_credits_planned_correctly():
    """AB12: 词曲从片尾提取后，前奏 A/B 正确规划，片尾名单不重复。"""
    raw_lines = [
        {"start": 25000, "duration": 5000, "text": "真实起唱歌词", "words": [{"text": "真实起唱歌词", "start": 25000, "end": 30000}]},
        {"start": 180000, "duration": 2000, "text": "作词：片尾作者"},
        {"start": 182000, "duration": 2000, "text": "作曲：片尾旋律"},
    ]
    data = {"lines": raw_lines, "tags": {"title": "尾部曲"}}
    c = classify_and_clean_all_lines(data)
    plan = plan_prelude_ab(c)
    assert plan.mode == "A"
    assert plan.t_sing_ms == 25000
    assert plan.display_lyricist == "词：片尾作者"
    assert plan.display_composer == "曲：片尾旋律"
    assert plan.lyricist_window == (12000, 16000)
    assert plan.composer_window == (16000, 20000)


if __name__ == "__main__":
    sys.exit(run(globals()))
