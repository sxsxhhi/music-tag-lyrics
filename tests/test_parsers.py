# -*- coding: utf-8 -*-
"""
解析层回归测试（M0 建立）

设计原则：**先固化现状**。
M0 阶段源码零改动，因此下面凡是断言"缺陷值"的用例，都是刻意写的**特征化测试**
（characterization test）：它锁住当前行为，任何后续改动都会让它变红，从而强制
改动者显式修改测试并说明理由。用例名前缀 `KNOWN_BUG__` 表示"这条断言记录的是缺陷"。

M2 修复后，`KNOWN_BUG__*` 必须整体反转，其中：
  - F-19：首行渲染应从 "A lu na, ca ra can ti ca" 变成 "A luna, cara cantica"
    （源文件真实空格只有 3 处：A 后、na, 后、ra 后）
  - F-06/C-08：41 条 x-translation 译文应落到 line["translation"]
  - X-01：format_time_tag(59995) 应从 [00:60.00] 变成 [01:00.00]
  - X-02：parse_time_string_to_ms("123:45.67") 应从 0 变成 7425670
  - X-06：标题应从繁体"挪德卡萊"变成按 xml:lang 优先选择的简体/原文

运行方式（不依赖 pytest，本机未安装）：
    python tests/test_parsers.py
也兼容 pytest：
    pytest tests/test_parsers.py
"""

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.parsers.lrc_parser import parse_lrc            # noqa: E402
from core.parsers.ttml_parser import parse_ttml          # noqa: E402
from core.converter import to_spl                        # noqa: E402
from core.utils.time_utils import format_time_tag, parse_time_string_to_ms  # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")


def _read(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _rendered(line):
    """把一行 ELRC 还原成播放器实际显示的文字（去掉行标签与词级标签）。"""
    body = re.sub(r"^\[[^\]]*\]", "", line)
    return re.sub(r"<[^>]*>", "", body)


# --------------------------------------------------------------------------
# 1. SPL + [offset] 夹具
# --------------------------------------------------------------------------

def test_spl_fixture_shape():
    """夹具本身不能漂移：1 行 2 词。"""
    data = parse_lrc(_read("spl_offset_reverse_test.lrc"))
    assert len(data["lines"]) == 1
    assert len(data["lines"][0]["words"]) == 2


def KNOWN_BUG__spl_word_offset_double_added():
    """F-09：首个伪词的起点被叠加两次 offset，终点只叠一次 → end < start。

    M2 修复后本用例应改为：w0.start == 1500 且 w0.end > w0.start。
    """
    data = parse_lrc(_read("spl_offset_reverse_test.lrc"))
    w0 = data["lines"][0]["words"][0]
    assert w0["start"] == 2000, w0
    assert w0["end"] == 1900, w0
    assert w0["end"] < w0["start"], w0
    assert w0["duration"] == 0, w0


# --------------------------------------------------------------------------
# 2. AMLL TTML 夹具
# --------------------------------------------------------------------------

def test_ttml_shape():
    """41 行 / 381 词 —— 这两个数字是全部后续改动的锚点，永不允许漂移。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    lines = data["lines"]
    assert len(lines) == 41, len(lines)
    assert sum(len(l.get("words") or []) for l in lines) == 381


def test_ttml_line_keys():
    """行 dict 的键集合当前是 5 键（无 translation）。M2 补 translation 后本用例需更新。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert sorted(data["lines"][0].keys()) == ["duration", "end", "start", "text", "words"]


def KNOWN_BUG__ttml_line_text_loses_source_spaces():
    """F-19 根因：`"".join(w["text"])` 丢掉 span 之间的原始空格。

    源文件首行真实文本 = "A" + " " + "lu" + "na," + " " + "ca" + "ra" + " " + "can" + "ti" + "ca"
                     = "A luna, cara cantica"
    当前解析结果 = "Aluna,caracantica"（一个空格都不剩）
    M2 修复后本用例应断言 text == "A luna, cara cantica"。
    """
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert data["lines"][0]["text"] == "Aluna,caracantica", data["lines"][0]["text"]


def KNOWN_BUG__ttml_render_splits_syllables():
    """F-19 症状：铁律 14 在相邻 ASCII 之间补空格，把音节当成词。

    正确渲染应为 "A luna, cara cantica"；
    当前渲染为 "A lu na, ca ra can ti ca"（4 个多出来的空格）。
    """
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    out = to_spl(data, title=data["tags"].get("ti", ""), artist=data["tags"].get("ar", ""))
    lyric_lines = [l for l in out.splitlines() if l.startswith("[00:00.83]")]
    assert len(lyric_lines) == 1
    assert _rendered(lyric_lines[0]) == "A lu na, ca ra can ti ca", _rendered(lyric_lines[0])


def KNOWN_BUG__ttml_translation_dropped():
    """F-06 + C-08：41 条 ttm:role="x-translation" 译文全部未落到 translation 字段。

    M2 修复后本用例应断言：至少 41 行 translation 非空，且首行译文
    包含 "我的月亮呀，我亲爱的歌"。
    """
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert all("translation" not in l for l in data["lines"])
    out = to_spl(data, title="x", artist="y")
    assert "我的月亮呀，我亲爱的歌" not in out


def KNOWN_BUG__ttml_meta_multilang_overwritten():
    """X-06：<amll:meta> 多语言重复 key 后写覆盖，取到的是最后一个。

    fixture 里 musicName 出现 5 次、album 6 次，实测标题取到繁体。
    M2 修复后应按 xml:lang 优先级选择。
    """
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert data["tags"]["ti"] == "挪德卡萊", data["tags"]["ti"]


# --------------------------------------------------------------------------
# 3. 时间戳工具
# --------------------------------------------------------------------------

def KNOWN_BUG__format_time_tag_rounds_to_sixty_seconds():
    """X-01：秒的 59.996~59.999 进位成 [00:60.00]，非法时间戳。

    实测触发区间是 59996~59999ms（59995 及以下因浮点表示不触发），
    同理 3599999ms -> [59:60.00]。
    M2 修复后：format_time_tag(59996) == "[01:00.00]"、format_time_tag(3599999) == "[01:00:00.00]"。
    """
    assert format_time_tag(59996) == "[00:60.00]"
    assert format_time_tag(59999) == "[00:60.00]"
    assert format_time_tag(3599999) == "[59:60.00]"
    # 对照组：区间外正常
    assert format_time_tag(59995) == "[00:59.99]"


def KNOWN_BUG__parse_time_rejects_three_digit_minutes():
    """X-02：分钟位限 2 位，三位分钟被静默判为非法并返回 0。

    M2 修复后：parse_time_string_to_ms("123:45.67") == 7425670。
    """
    assert parse_time_string_to_ms("123:45.67") == 0
    # 对照组：两位分钟是正常的
    assert parse_time_string_to_ms("99:59.999") == 5999999


# --------------------------------------------------------------------------
# 本地 runner（无 pytest 也能跑）
# --------------------------------------------------------------------------

def main():
    cases = [(n, f) for n, f in sorted(globals().items())
             if n.startswith(("test_", "KNOWN_BUG__")) and callable(f)]
    failed = []
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append((name, e))
            print(f"  FAIL  {name}  -> {e}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
