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
from core.parsers.lrc_parser import parse_lrc
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
    make_char_words,
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


def test_standard_prelude_lines_are_char_verbatim():
    """标准化前奏标题行必须字符级逐字平均分配，粒度与日常入口一致。"""
    line = _stamped_line("正文甲")
    c = classify_and_clean_all_lines(
        {"tags": {"title": TITLE, "artist": ARTIST}, "lines": [line]}
    )
    plan = plan_prelude_ab(c)
    elrc = render_elrc_v182(c)
    first = next(l for l in elrc.splitlines() if l.strip())
    stags = [int(_ms(x)) for x in re.findall(r"<(\d{1,3}:\d{1,2}(?:\.\d{1,3})?)>", first)]
    # 起点 + 每字符一个时间片 + 行尾终点
    assert len(stags) == len(EXPECT) + 1, (first, stags)
    assert stags[0] == plan.title_window[0] and stags[-1] == plan.title_window[1]
    # 均匀分配：相邻时间片差值恒定（允许 ±1ms 取整误差）
    gaps = [b - a for a, b in zip(stags, stags[1:])]
    assert max(gaps) - min(gaps) <= 1, gaps
    # 粒度与日常入口一致（同一算法：字符数 + 行尾 = 相同 tag 数）
    daily = to_spl(
        {"tags": {}, "lines": [_stamped_line("正文甲")], "time_precision": 3},
        title=TITLE, artist=ARTIST,
    )
    drow = next(
        l for l in daily.splitlines()
        if EXPECT in re.sub(r"[\[<]\d{1,3}:\d{1,2}(?:\.\d{1,3})?[\]>]", "", l)
    )
    dtags = [int(_ms(x)) for x in re.findall(r"<(\d{1,3}:\d{1,2}(?:\.\d{1,3})?)>", drow)]
    assert len(dtags) == len(stags), (len(dtags), len(stags))


def _ms(tag: str) -> int:
    parts = tag.split(":")
    sec = parts[-1].split(".")
    total = int(parts[0]) * 60 + int(sec[0])
    frac = (sec[1] if len(sec) > 1 else "0").ljust(3, "0")[:3]
    return total * 1000 + int(frac)


def test_char_words_fail_closed_on_nonpositive_window():
    """非正窗口/空文本必须返回空列表，绝不返回 0ms 或负长度字块（铁律 8）。"""
    assert make_char_words("甲乙", 1000, 1000) == []
    assert make_char_words("甲乙", 1000, 500) == []
    assert make_char_words("", 0, 5000) == []
    words = make_char_words("甲乙", 1000, 1002)
    assert [(w["text"], w["start"], w["end"]) for w in words] == [
        ("甲", 1000, 1001), ("乙", 1001, 1002)]
    assert all(w["end"] > w["start"] for w in words)


def test_daily_dedup_uses_shared_matcher():
    """日常入口识别既有标题行必须走共享 matcher：真实变体不得重复合成标题行。"""
    data = {"tags": {}, "time_precision": 3}
    for first in VARIANTS + (TITLE,):
        rows = [
            l for l in to_spl(
                {**data, "lines": [_stamped_line(first)]},
                title=TITLE, artist=ARTIST,
            ).splitlines() if l.strip()
        ]
        # [ti:]/[ar:] + 首行；不得多出一条合成标题行
        assert len(rows) == 3, (first, rows)


def test_long_title_and_feat_variants():
    """超长曲名与 feat./ft. 合唱写法也必须被共享 matcher 识别。"""
    long_title = "测试曲" * 40          # 120 字符，超过旧 80 字符上限
    assert match_display_title(f"{long_title} - {ARTIST}", long_title, ARTIST)
    assert match_display_title(f"{long_title} - {ARTIST} (Live)", long_title, ARTIST)
    # feat./ft. 写法：艺人证据按 feat. 再拆分
    for text in (f"{TITLE} - 歌手甲 feat. 歌手乙",
                 f"{TITLE} - 歌手甲 ft. 歌手乙",
                 f"{TITLE} - 歌手甲 Feat. 歌手乙"):
        assert match_display_title(text, TITLE, "歌手甲"), text
    # 反向：feat. 里的名字不能反证与 title 无关的行
    assert not match_display_title("无关曲 - 无关人 feat. 歌手甲", TITLE, "歌手甲")


def test_display_title_normalizes_control_whitespace():
    """标题/歌手内的换行与制表符必须折叠为空格：单行结构不能内嵌换行。"""
    assert format_display_title("曲\n名", "人\t名") == "曲 名 - 人 名"
    assert "\n" not in format_display_title("曲\r\n名", "人名")


def test_r1_rejects_incomplete_title():
    """有 artist 却只写纯歌名时，R1 必须报 TITLE_DISPLAY_CONTRACT。"""
    c = _canonical(TITLE, ARTIST, TITLE)
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    codes = [i.code for i in res.issues]
    assert "TITLE_DISPLAY_CONTRACT" in codes, codes


def _prelude_canonical(elrc: str, title: str = TITLE, artist: str = ARTIST,
                       force: str = ""):
    c = classify_and_clean_all_lines(parse_lrc(elrc), song_title=title, song_artist=artist)
    c.song["extracted_lyricist"] = "作词人甲"
    c.song["extracted_composer"] = "作曲人乙"
    plan = plan_prelude_ab(c, force_title=force)
    return c, plan


def test_r1_accepts_merged_prelude_first_segment():
    """合并展示行（PRELUDE_SLOTS_MERGED）只要首段仍是契约标题就必须通过 R1。"""
    c, plan = _prelude_canonical(
        "[00:00.002]<00:00.002>甲<00:00.004>乙<00:00.006>\n"
        "[00:05.000]<00:05.000>正<00:06.000>文<00:07.000>\n")
    assert " | " in plan.display_title, plan.display_title
    assert plan.display_title.split(" | ")[0] == EXPECT
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    assert res.passed, [(i.code, i.message) for i in res.issues]


def test_r1_rejects_merged_prelude_with_plain_title():
    """合并展示行首段退回纯歌名时仍必须阻断。"""
    c, _plan = _prelude_canonical(
        "[00:00.002]<00:00.002>甲<00:00.004>乙<00:00.006>\n"
        "[00:05.000]<00:05.000>正<00:06.000>文<00:07.000>\n")
    c.prelude.display_title = TITLE + " | 词：作词人甲"
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    assert "TITLE_DISPLAY_CONTRACT" in [i.code for i in res.issues]


def test_r1_accepts_force_title_basis():
    """force_title 覆盖歌名时 R1 按 plan 记录的 basis 核对，不得误判违约。"""
    c, plan = _prelude_canonical(
        "[00:25.000]<00:25.000>正<00:26.000>文<00:27.000>\n",
        title="原曲名", force="替换曲名")
    assert plan.title_basis == ("替换曲名", ARTIST), plan.title_basis
    assert c.song["title"] == "原曲名"
    res = verify_canonical_exports(c, render_elrc_v182(c), render_ttml_v182(c))
    assert res.passed, [(i.code, i.message) for i in res.issues]


if __name__ == "__main__":
    sys.exit(run(globals()))
