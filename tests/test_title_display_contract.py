# -*- coding: utf-8 -*-
"""前奏标题行格式契约：两入口一致 + 真实变体识别 + R1 内容断言。

夹具全部为结构等价的合成内容，不含任何曲库曲名、路径或歌词原文。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.auditors.elrc_ttml_comparator import verify_canonical_exports
from core.cleaners.content_classifier import classify_and_clean_all_lines, classify_line_role
from core.converter import to_spl
from core.models.canonical_lyrics import (
    CanonicalLine,
    CanonicalLyrics,
    CanonicalWord,
    LineRole,
    PreludePlan,
)
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from core.schedulers.prelude_scheduler import plan_prelude_ab
from core.utils.text_utils import (
    format_display_title,
    is_title_line,
    match_display_title,
)
from tests._testkit import run

TITLE = "测试曲"
ARTIST = "歌手甲/歌手乙"
EXPECT = "测试曲 - 歌手甲/歌手乙"

# 真实曲库常见变体（用合成内容复现形态，不含真实文本）
VARIANTS = (
    "测试曲 - 歌手甲/歌手乙",
    "测试曲 - 歌手甲 (别名)/歌手乙",
    "测试曲 - 歌手甲、歌手乙 (代号)",
    "歌手乙/歌手甲 - 测试曲",
    "测试曲－歌手甲/歌手乙",
    "测试曲  -  歌手甲 / 歌手乙",
)


def _stamped_line(text: str, start: int = 25000, end: int = 28000) -> dict:
    return {
        "start": start,
        "duration": end - start,
        "end": end,
        "text": text,
        "words": [{"text": text, "start": start, "end": end}],
    }


def test_format_single_source():
    """格式函数单一出处：带歌手 / 无歌手 / 无歌名三种兜底。"""
    assert format_display_title(TITLE, ARTIST) == EXPECT
    assert format_display_title(TITLE, "") == TITLE
    assert format_display_title("", ARTIST) == ""
    assert format_display_title("  测试曲  ", " 歌手甲 ") == "测试曲 - 歌手甲"


def test_daily_to_spl_title_matches_contract():
    """日常入口 to_spl 合成的前奏标题行必须是「歌名 - 歌手」。"""
    data = {
        "tags": {},
        "lines": [_stamped_line("正文甲")],
        "time_precision": 3,
    }
    out = to_spl(data, title=TITLE, artist=ARTIST)
    rows = [
        re.sub(r"[\[<]\d{1,3}:\d{1,2}(?:\.\d{1,3})?[\]>]", "", line)
        for line in out.splitlines()
        if line.startswith("[")
    ]
    assert EXPECT in rows, rows[:6]


def test_standard_prelude_title_matches_contract():
    """标准化入口 P_TITLE 的 display_title 必须与日常入口一致。"""
    data = {
        "tags": {"title": TITLE, "artist": ARTIST},
        "lines": [_stamped_line("正文甲")],
    }
    c = classify_and_clean_all_lines(data)
    plan = plan_prelude_ab(c)
    assert plan.display_title == EXPECT
    assert plan.display_title == format_display_title(TITLE, ARTIST)


def test_force_title_still_uses_shared_format():
    """force_title 只覆盖歌名，不得绕过统一格式函数产出纯歌名。"""
    data = {
        "tags": {"title": "其他曲", "artist": ARTIST},
        "lines": [_stamped_line("正文甲")],
    }
    c = classify_and_clean_all_lines(data)
    plan = plan_prelude_ab(c, force_title=TITLE)
    assert plan.display_title == EXPECT


def test_no_artist_falls_back_to_plain_title():
    """无歌手时退回纯歌名，不得产生悬空分隔符。"""
    data = {
        "tags": {"title": TITLE},
        "lines": [_stamped_line("正文甲")],
    }
    c = classify_and_clean_all_lines(data)
    plan = plan_prelude_ab(c)
    assert plan.display_title == TITLE
    assert " - " not in plan.display_title


def test_variants_recognized_by_shared_matcher():
    """真实曲库变体必须被共享判定函数识别（旧三形态精确等值只认 1 种）。"""
    for text in VARIANTS:
        assert match_display_title(text, TITLE, ARTIST), text
        assert is_title_line(text, TITLE, ARTIST), text


def test_classifier_accepts_variants_as_title():
    """R2 语义分类对同一批变体必须判为 TITLE，不得落到 SINGING。"""
    for text in VARIANTS:
        role, _extra = classify_line_role(
            text,
            song_title=TITLE,
            song_artist=ARTIST,
            has_words=False,
            explicit_display_title=True,
        )
        assert role == LineRole.TITLE, (text, role)


def test_non_title_lines_rejected():
    """制作名单、正文、纯歌名、无关组合都不得被误判为标题行。"""
    for text in (
        "词：歌手甲",
        "曲：歌手乙",
        "编曲：某工作室",
        "正文唱词一句",
        TITLE,
        "无关曲 - 无关人",
        "歌手甲/歌手乙 - 无关曲",
    ):
        assert not match_display_title(text, TITLE, ARTIST), text


def _canonical(title: str, artist: str, display: str) -> CanonicalLyrics:
    c = CanonicalLyrics()
    c.song = {"title": title, "artist": artist}
    words = [
        CanonicalWord(text="正", start_ms=25000, end_ms=26000),
        CanonicalWord(text="文", start_ms=26000, end_ms=27000),
    ]
    c.lines = [
        CanonicalLine(
            stable_id="L1",
            source_index=0,
            role=LineRole.SINGING,
            start_ms=25000,
            end_ms=27000,
            text="正文",
            words=words,
        )
    ]
    c.prelude = PreludePlan(
        mode="A",
        t_sing_ms=25000,
        title_window=(1500, 12000),
        display_title=display,
    )
    return c


def test_r1_accepts_contract_title():
    """符合格式的 P_TITLE 通过 R1 双校验。"""
    c = _canonical(TITLE, ARTIST, EXPECT)
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    assert res.passed, [(i.code, i.message) for i in res.issues]


def test_r1_rejects_incomplete_title():
    """有 artist 却只写纯歌名时，R1 必须报 TITLE_DISPLAY_CONTRACT。"""
    c = _canonical(TITLE, ARTIST, TITLE)
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    codes = [i.code for i in res.issues]
    assert "TITLE_DISPLAY_CONTRACT" in codes, codes


if __name__ == "__main__":
    sys.exit(run(globals()))
