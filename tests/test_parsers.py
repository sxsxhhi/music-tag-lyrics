# -*- coding: utf-8 -*-
"""解析层离线回归：LRC/SPL/TTML 与时间戳。运行：python tests/test_parsers.py"""

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

def test_ttml_lines_sorted_by_start():
    """P0-3：文档顺序 ≠ 时间顺序时（对唱按 ttm:agent 分 <div> 的典型），
    输出必须按 start 升序，否则 validator 会把倒序行 +100ms 平移、把音节压到同一时刻。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
        "<body><div>"
        '<p begin="00:20.000" end="00:22.000"><span begin="00:20.000" end="00:22.000">后</span></p>'
        '<p begin="00:10.000" end="00:12.000"><span begin="00:10.000" end="00:12.000">先</span></p>'
        '<p begin="00:30.000" end="00:32.000"><span begin="00:30.000" end="00:32.000">更后</span></p>'
        "</div></body></tt>"
    )
    data = parse_ttml(ttml)
    starts = [l["start"] for l in data["lines"]]
    assert starts == sorted(starts), f"未按 start 排序：{starts}"
    texts = [l["text"] for l in data["lines"]]
    assert texts == ["先", "后", "更后"], texts


def test_ttml_pretty_format_no_space_injection():
    """P1-2：一拍一行的美化 TTML（span 之间是换行+缩进）不得给中文插空格。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="00:10.000" end="00:13.000">\n'
        '  <span begin="00:10.000" end="00:11.000">我</span>\n'
        '  <span begin="00:11.000" end="00:12.000">爱</span>\n'
        '  <span begin="00:12.000" end="00:13.000">你</span>\n'
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    line = data["lines"][0]
    assert line["text"] == "我爱你", repr(line["text"])
    assert all(w["join"] == "none" for w in line["words"]), [w["join"] for w in line["words"]]


def test_ttml_english_space_kept():
    """P1-2：英文单词之间的单个空格仍要判成词边界。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="00:10.000" end="00:12.000">'
        '<span begin="00:10.000" end="00:11.000">Hello</span> '
        '<span begin="00:11.000" end="00:12.000">World</span>'
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    assert data["lines"][0]["text"] == "Hello World", repr(data["lines"][0]["text"])


def test_ttml_empty_bg_only_paragraph_dropped():
    """P1-4：只有 x-bg 的 <p> 被抽空后，不得留下 text="" 的幽灵行。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>'
        '<p begin="00:10.000" end="00:12.000">'
        '<span ttm:role="x-bg" begin="00:10.000" end="00:12.000">(和声)</span>'
        "</p>"
        '<p begin="00:12.000" end="00:14.000">'
        '<span begin="00:12.000" end="00:14.000">正文</span>'
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    assert len(data["lines"]) == 1, [l["text"] for l in data["lines"]]
    assert data["lines"][0]["text"] == "正文"


def test_ttml_empty_bg_only_paragraph_dropped_single_quotes():
    """单引号 ttm:role='x-bg' 同样必须抽空，不得把和声留在主轨。"""
    ttml = (
        "<tt xmlns='http://www.w3.org/ns/ttml' xmlns:ttm='http://www.w3.org/ns/ttml#metadata'>"
        "<body><div>"
        "<p begin='00:10.000' end='00:12.000'>"
        "<span ttm:role='x-bg' begin='00:10.000' end='00:12.000'>(和声)</span>"
        "</p>"
        "<p begin='00:12.000' end='00:14.000'>"
        "<span begin='00:12.000' end='00:14.000'>正文</span>"
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    assert len(data["lines"]) == 1, [l["text"] for l in data["lines"]]
    assert data["lines"][0]["text"] == "正文"


def test_ttml_missing_span_end_uses_next_begin():
    """P1-4：span 缺 end 时优先取下一个 span 的 begin，不得被压成 500ms。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="00:10.000" end="00:14.000">'
        '<span begin="00:10.000">长音</span>'
        '<span begin="00:13.000" end="00:14.000">尾</span>'
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    w0 = data["lines"][0]["words"][0]
    assert w0["end"] == 13000, w0


def test_ttml_body_dur_parsed():
    """P1-A：<body dur> 要解析成 duration_ms，供离线场景跑时长门禁。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body dur="04:10.610"><div>'
        '<p begin="00:10.000" end="00:12.000">'
        '<span begin="00:10.000" end="00:12.000">词</span>'
        "</p></div></body></tt>"
    )
    data = parse_ttml(ttml)
    assert data.get("duration_ms") == 250610, data.get("duration_ms")


def test_format_time_tag_precision():
    """v1.2.8：precision=3 保留毫秒，precision=2 走厘秒舍入（默认保持旧行为）。"""
    assert format_time_tag(46425) == "[00:46.42]"
    assert format_time_tag(46425, precision=3) == "[00:46.425]"
    assert format_time_tag(46425, "<>", 3) == "<00:46.425>"
    assert format_time_tag(0, "[]", 3) == "[00:00.000]"
    assert format_time_tag(3599999, "[]", 3) == "[59:59.999]"
    assert format_time_tag(599996, "[]", 3) == "[09:59.996]"


def test_parse_lrc_detects_precision():
    """v1.2.8：源里出现过 3 位小数就记 3，否则记 2。"""
    assert parse_lrc("[00:01.230]三<00:01.230>位").get("time_precision") == 3
    assert parse_lrc("[00:01.23]两<00:01.23>位").get("time_precision") == 2
    assert parse_lrc("[00:01]无小数").get("time_precision") == 2
    # 混装时只要出现 3 位就升到 3
    assert parse_lrc("[00:01.23]甲\n[00:02.345]乙").get("time_precision") == 3


def test_ttml_precision_is_three():
    """TTML 源本身是毫秒精度，解析结果固定 time_precision=3。"""
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="00:10.425" end="00:12.000">'
        '<span begin="00:10.425" end="00:12.000">词</span>'
        "</p></div></body></tt>"
    )
    assert parse_ttml(ttml).get("time_precision") == 3


def test_to_spl_respects_time_precision():
    """v1.2.8：time_precision=3 的 data 必须整篇输出 3 位时光轴。"""
    data = {
        "time_precision": 3,
        "tags": {},
        "lines": [{
            "start": 46425, "end": 54600, "duration": 8175, "text": "傍晚",
            "translation": "",
            "words": [
                {"start": 46425, "end": 46673, "text": "傍", "join": "none"},
                {"start": 46673, "end": 54000, "text": "晚", "join": "none"},
            ],
        }],
    }
    out = to_spl(data, title="T", artist="A")
    assert "<00:46.425>" in out, out
    assert "00:46.42>" not in out, "出现了 2 位时间戳"


def test_to_spl_defaults_to_two_digits():
    """没有 time_precision 时仍按 2 位输出（向后兼容既有产物与基线）。"""
    data = {
        "tags": {},
        "lines": [{
            "start": 46425, "end": 54600, "duration": 8175, "text": "傍晚",
            "translation": "",
            "words": [{"start": 46425, "end": 54000, "text": "傍晚", "join": "none"}],
        }],
    }
    out = to_spl(data, title="T", artist="A")
    assert "<00:46.42>" in out, out
    assert "00:46.425" not in out


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


def test_r07_strict_time_parsing_returns_none_for_invalid():
    """R07: parse_time_string_to_ms 遇非法或负时间戳严格返回 None，绝不伪装为合法 0ms。"""
    assert parse_time_string_to_ms("invalid") is None
    assert parse_time_string_to_ms("-01:00.00") is None
    assert parse_time_string_to_ms("") is None
    assert parse_time_string_to_ms(None) is None
    assert parse_time_string_to_ms("   ") is None
    # 合法零时刻严格保持为 0
    assert parse_time_string_to_ms("00:00.00") == 0
    assert parse_time_string_to_ms("00:00:00.000") == 0
    assert parse_time_string_to_ms("0") == 0
    assert parse_time_string_to_ms("0.0s") == 0


def test_r08_reconstructed_word_text_integrity_check():
    """R08: 内部丢词或文本不完整时，标记 text_mismatch，降级为 invalid_word_lines。"""
    from core.parsers.lrc_red_ttml import parse_lrc_red_ttml
    # "Hello World" 中 World 缺少 span，导致 reconstructed_text 仅为 "Hello"
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml">
      <body>
        <div>
          <p begin="1.0" end="3.0"><span begin="1.0" end="2.0">Hello</span> World</p>
        </div>
      </body>
    </tt>"""
    res = parse_lrc_red_ttml(xml)
    assert len(res["lines"]) == 1
    line = res["lines"][0]
    assert line["raw_main_text"] == "Hello World"
    assert line["text_mismatch"] is True
    assert res["invalid_word_lines"] == 1
    assert res["native_word_lines"] == 0
    assert res["word_coverage_ratio"] == 0.0


def test_r09_real_quality_counters_increment():
    """R09: inferred_end_count 与 timestamp_repair_count 真实累加，杜绝恒为 0。"""
    from core.parsers.lrc_red_ttml import parse_lrc_red_ttml
    # p 缺 end (inferred_end_count +1), span 缺 end (inferred_end_count +1),
    # span 时长仅 10ms < 40ms 触发修复 (timestamp_repair_count +1)
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml">
      <body>
        <div>
          <p begin="1.0"><span begin="1.0" end="1.01">A</span><span begin="1.01">B</span></p>
        </div>
      </body>
    </tt>"""
    res = parse_lrc_red_ttml(xml)
    assert res["inferred_end_count"] >= 2, f"inferred_end_count was {res['inferred_end_count']}"
    assert res["timestamp_repair_count"] >= 1, f"timestamp_repair_count was {res['timestamp_repair_count']}"


def test_r10_ttml_translation_attachment_avoids_mismatched_lines():
    """R10: 译文多声部隔离与显式 target 绑定，避免盲目挂载 lines[-1]。"""
    from core.parsers.lrc_red_ttml import parse_lrc_red_ttml
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">
      <body>
        <div>
          <p xml:id="p1" ttm:agent="v1" begin="1.0" end="2.5">Lead singer line</p>
          <p xml:id="p2" ttm:agent="v2" begin="1.2" end="2.7">Backing singer line</p>
          <!-- 显式针对 p1 的译文 -->
          <p ttm:role="x-translation" target="p1" begin="1.0" end="2.5">主唱行中文译文</p>
        </div>
      </body>
    </tt>"""
    res = parse_lrc_red_ttml(xml)
    assert len(res["lines"]) == 2
    l1 = next(l for l in res["lines"] if l.get("id") == "p1")
    l2 = next(l for l in res["lines"] if l.get("id") == "p2")
    assert l1.get("translation") == "主唱行中文译文"
    assert l2.get("translation") is None, "声部不同且未指定 target 的行不得被误挂译文"


def test_r09_duet_simultaneous_lines_not_overlap():
    """R09: 区分合法双声部同时演唱（不同 agent）与主唱轴冲突重叠。"""
    from core.parsers.lrc_red_ttml import parse_lrc_red_ttml
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">
      <body>
        <div>
          <!-- 声部 1 与声部 2 在同一时间段合唱，属于合法合唱 -->
          <p ttm:agent="v1" begin="1.0" end="3.0">Singer A line</p>
          <p ttm:agent="v2" begin="1.5" end="3.5">Singer B line</p>
        </div>
      </body>
    </tt>"""
    res = parse_lrc_red_ttml(xml)
    assert res["timeline_overlap_count"] == 0, "不同 agent 的合唱不应计入主轴冲突错误"


# --------------------------------------------------------------------------
# 本地 runner（无 pytest 也能跑）
# --------------------------------------------------------------------------

def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
