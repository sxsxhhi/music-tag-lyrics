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


def test_spl_word_offset_applied_once():
    """F-09/P1-4 修复：首个伪词的 offset 只叠一次，end 必须晚于 start。

    `[offset:500]` + `[00:01.00]Hello <00:01.40>World`
    → 行起点 1500，首词 1500~1900（修复前是 start=2000 / end=1900 / duration=0 的倒流）
    """
    data = parse_lrc(_read("spl_offset_reverse_test.lrc"))
    w0 = data["lines"][0]["words"][0]
    assert w0["start"] == 1500, w0
    assert w0["end"] == 1900, w0
    assert w0["end"] > w0["start"], w0
    assert w0["duration"] == 400, w0


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
    """行 dict 现为 6 键：补上了 translation（B4 修复 F-06/C-08）。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert sorted(data["lines"][0].keys()) == ["duration", "end", "start", "text", "translation", "words"]


def test_ttml_preserves_source_spaces():
    """F-19：源里 span 之间的空格是真实字符，解析后必须保留。

    源文本 = "A" + " " + "lu" + "na," + " " + "ca" + "ra" + " " + "can" + "ti" + "ca"
           = "A luna, cara cantica"
    """
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert data["lines"][0]["text"] == "A luna, cara cantica", data["lines"][0]["text"]


def test_ttml_render_no_syllable_split():
    """F-19 症状修复：输出层按 join 标记走，不再把音节当词插空格。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    out = to_spl(data, title=data["tags"].get("ti", ""), artist=data["tags"].get("ar", ""))
    # 取第一条带词级标签的行（时间戳可能因厘秒四舍五入与旧输出差 1cs，不作硬编码）
    lyric_lines = [l for l in out.splitlines() if l.startswith("[") and "<" in l]
    assert lyric_lines, "未找到逐字行"
    assert _rendered(lyric_lines[0]) == "A luna, cara cantica", _rendered(lyric_lines[0])


def test_ttml_translation_kept():
    """F-06：x-translation 译文必须落到 line["translation"] 并出现在输出里。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert data["lines"][0]["translation"] == "我的月亮呀，我亲爱的歌"
    assert sum(1 for l in data["lines"] if l["translation"]) == 41
    out = to_spl(data, title="x", artist="y")
    assert "我的月亮呀，我亲爱的歌" in out


def test_ttml_meta_multilang():
    """X-06：多语言重复 key 不再"后写覆盖"。ti/album 取首个非空，artists 合并去重。"""
    data = parse_ttml(_read("amll_ncm_0.ttml"))
    assert data["tags"]["ti"] == "Nod-Krai"
    assert data["tags"]["ar"] == "AURORA/HOYO-MiX"


# --------------------------------------------------------------------------
# 3. 时间戳工具
# --------------------------------------------------------------------------

def test_format_time_tag_no_rollover():
    """X-01 修复：59.995s 进位应正确进到分钟，不再出现非法的 [00:60.00]。"""
    assert format_time_tag(59990) == "[00:59.99]"
    assert format_time_tag(59996) == "[01:00.00]"
    assert format_time_tag(59999) == "[01:00.00]"
    assert format_time_tag(3599999) == "[60:00.00]"


def test_parse_time_three_digit_minutes():
    """X-02 修复：三位分钟不再被静默判为非法并返回 0。"""
    assert parse_time_string_to_ms("123:45.67") == 7425670
    assert parse_time_string_to_ms("99:59.999") == 5999999
    # 与格式化配套：超过 99 分钟能往返
    assert parse_time_string_to_ms(format_time_tag(6000000, "[]")[1:-1]) == 6000000


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
