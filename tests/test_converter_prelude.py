# -*- coding: utf-8 -*-
"""to_spl 前奏区回归：署名注入不得压缩标题行、不得让标题重复、不得退化成静态行。

对应缺陷清单 v2.0 的 P0-1。运行：python tests/test_converter_prelude.py
"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.converter import to_spl

MIN_SPAN_MS = 200          # 铁律 4：人类单字通常 200ms~800ms


def _line(text, start, end):
    """构造一条带逐字词的正文行。"""
    n = len(text) or 1
    step = max(1, (end - start) // n)
    words = [
        {"text": c, "start": start + i * step, "end": start + (i + 1) * step}
        for i, c in enumerate(text)
    ]
    return {"start": start, "end": end, "duration": end - start,
            "text": text, "translation": "", "words": words}


def _tag_ms(tag):
    m = re.match(r"(\d+):(\d+)\.(\d+)", tag)
    mm, ss, frac = int(m.group(1)), int(m.group(2)), m.group(3)
    return mm * 60000 + ss * 1000 + int(round(int(frac) * 10 ** (3 - len(frac))))


def _tags(line):
    return [_tag_ms(t) for t in re.findall(r"<(\d+:\d+\.\d+)>", line)]


def _strip(line):
    return re.sub(r"[\[<][^\]>]*[\]>]", "", line).strip()


def _body_rows(out):
    """去掉 [ti:]/[ar:] 两行后的所有非空行。"""
    rows = [l for l in out.split("\n") if l.strip()]
    return [l for l in rows if not re.match(r"^\[(ti|ar|al|by|offset):", l)]


def test_title_span_meets_acoustic_floor():
    """有署名时，标题行必须按正文首唱排布，每字间隔 >= 200ms（旧版是 117ms）。"""
    data = {"tags": {}, "lines": [_line("傍晚六点下班", 46425, 54600)]}
    out = to_spl(data, title="杀死那个石家庄人", artist="万能青年旅店",
                 lyricist="姬赓", composer="董亚千")
    title_row = _body_rows(out)[0]
    gaps = [b - a for a, b in zip(_tags(title_row), _tags(title_row)[1:])]
    assert gaps, "标题行没有逐字标签"
    assert min(gaps) >= MIN_SPAN_MS, f"标题行字间隔 {min(gaps)}ms < {MIN_SPAN_MS}ms"
    # 标题行区间应是 1000 -> 5500
    t = _tags(title_row)
    assert t[0] == 1000, f"标题行起点 {t[0]} != 1000"
    assert t[-1] == 5500, f"标题行终点 {t[-1]} != 5500"


def test_title_not_duplicated_when_first_line_is_title():
    """首句本身就是「歌名 - 歌手」时，不得再合成一条标题行。"""
    data = {"tags": {}, "lines": [
        _line("测试曲 - 测试人", 46425, 50000),
        _line("正文一", 50000, 55000),
    ]}
    out = to_spl(data, title="测试曲", artist="测试人")
    rows = [_strip(l) for l in _body_rows(out)]
    assert rows.count("测试曲 - 测试人") == 1, f"标题重复：{rows}"
    # 合成标题行的起点是 1000；原首句是 46425，两者只能存在一个
    starts = [_tags(l)[0] for l in _body_rows(out)]
    assert 1000 not in starts, f"不应生成合成标题行，实际起点 {starts}"


def test_short_prelude_title_not_degraded_to_static():
    """首唱 13s + 有署名：标题必须是逐字行，不能退化成 [00:00.00] 静态行。"""
    data = {"tags": {}, "lines": [_line("正文甲", 13000, 18000), _line("正文乙", 18000, 22000)]}
    out = to_spl(data, title="测试曲", artist="测试人", lyricist="甲", composer="乙")
    assert "[00:00.00]" not in out, "标题退化成静态行"
    title_row = _body_rows(out)[0]
    assert _tags(title_row), "标题行没有逐字标签"


def _title_rows(out, title_line):
    """找出合成标题行（剥掉时间戳标签后文本等于「歌名 - 歌手」的那些行）。"""
    return [l for l in _body_rows(out) if _strip(l) == title_line]


def test_english_credit_at_zero_does_not_break_first_sing():
    """首行是 `Lyrics : X`@0 时，首唱时间必须取到真正正文，而不是 0。"""
    data = {"tags": {}, "lines": [
        _line("Lyrics : 方文山", 0, 2000),
        _line("真正的正文", 20000, 24000),
    ]}
    out = to_spl(data, title="T", artist="A")
    found = _title_rows(out, "T - A")
    assert found, "没有生成标题行（首唱被算成 0 导致退化）"
    t = _tags(found[0])
    assert t and t[0] == 1000, f"标题行起点异常：{t[:3]}"
    assert t[-1] <= 5500, f"标题行超出 5500：{t[-1]}"


def test_credit_lines_excluded_from_first_sing():
    """署名行（含中英文多种写法）不得被当成首唱锚点。"""
    for credit in ("作词：姬赓", "Lyrics : 方文山", "OP : 滚石", "出品：某某公司", "吉他：王路遥"):
        data = {"tags": {}, "lines": [
            _line(credit, 0, 2000),
            _line("正文", 30000, 34000),
        ]}
        out = to_spl(data, title="T", artist="A")
        found = _title_rows(out, "T - A")
        assert found, f"{credit!r} 被当成首唱，标题行未生成"
        assert _tags(found[0])[0] == 1000, f"{credit!r} 让首唱算错"


def test_no_credit_still_works():
    """不传署名时行为不变：标题行 1000->5500。"""
    data = {"tags": {}, "lines": [_line("正文", 46425, 50000)]}
    out = to_spl(data, title="测试曲", artist="测试人")
    title_row = _body_rows(out)[0]
    t = _tags(title_row)
    assert t[0] == 1000 and t[-1] == 5500, f"标题区间异常：{t[0]}->{t[-1]}"


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
