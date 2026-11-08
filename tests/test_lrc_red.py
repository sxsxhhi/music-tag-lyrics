# -*- coding: utf-8 -*-
"""lrc.red 歌词客户端、解析适配器与 ISRC 工具单元测试 (v1.8.1)。

覆盖用例矩阵：
- C01: 缓存隔离（跨歌曲不同时长重新仲裁）
- C02: 杜绝错误负缓存锁死
- C03: 缓存命中时重复执行验证
- C04: 网络异常后恢复重试
- M01: ISRC 直查 Live 版本互斥拦截
- M02: ISRC 直查 Remix 版本互斥拦截
- M03: match / search 裁决与排序一致性
- M04: 远端缺失元数据拒绝伪造 strict
- M05: 远端艺人不同拦截
- M06: 多个等分且无法消歧候选不强制乱选
- Q01: byModel=True 拒绝作为 T1 strict 逐字候选
- Q02: byModel=False 仍需完整门禁验证
- Q03: byModel 缺失标记 unknown / unverified_origin
- Q04: 演唱行逐字覆盖率不足 95% 拦截
- A06: .song.json 404 时降级到 .ttml + .json
- A07: .song.json 200 缺 source 时尝试备用端点
- A08: 429 / 503 / 体积超限受控失败
- T01: 英文 Hel + lo (无空格) 保持 Hello
- T02: 英文 This + hit (有空格) 保持 This hit
- T03: 中文相邻字不额外插空格
- T04: XML 实体正确转义解码
- T05: 多 <div> 交错时间行排序
- T06: 和声、音译与译文分离
- T07: 逆序时间戳质量审计拦截
"""

import json
import os
import sys
import time
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:
    from tests import _testkit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.isrc import normalize_isrc, is_valid_isrc
from core.parsers.lrc_red_ttml import parse_lrc_red_ttml, normalize_lrc_red_namespaces
import core.fetchers.lrc_red as lr_mod
from core.fetchers.lrc_red import LrcRedFetcher
from core.utils.http import HttpResult
from core.converter import format_verbatim_words_line

lr_mod.MIN_REQ_INTERVAL_S = 0.0

# 模拟标准 lrc.red 样本 TTML
SAMPLE_LRC_RED_TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:lrc="http://lrc.red/lyric-ttml-internal" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" lrc:timing="Word" xml:lang="en">
<head>
  <metadata>
    <ttm:agent type="person" xml:id="v1"/>
    <sourceMetadata xmlns="http://lrc.red/lyric-ttml-internal" leadingSilence="0.140">
      <translations>
        <translation xml:lang="zh-Hans">这是中文译文</translation>
      </translations>
      <songwriters>
        <songwriter>Mark Ronson</songwriter>
        <songwriter>Bruno Mars</songwriter>
      </songwriters>
    </sourceMetadata>
  </metadata>
</head>
<body dur="4:29.658">
  <div begin="16.906" end="21.036">
    <p begin="16.906" end="18.766" lrc:key="L1" ttm:agent="v1">
      <span begin="16.906" end="17.266">This</span> <span begin="17.266" end="17.687">hit,</span> <span begin="17.687" end="18.010">that</span> <span begin="18.010" end="18.418">ice</span> <span begin="18.418" end="18.766">cold</span>
      <span ttm:role="x-bg" begin="18.500" end="18.766">cold</span>
    </p>
    <p begin="18.766" end="21.036" lrc:key="L2" ttm:agent="v1">
      <span begin="18.766" end="19.324">Michelle</span> <span begin="19.324" end="19.772">Pfeiffer</span>
      <span ttm:role="x-translation">米歇尔·菲佛</span>
    </p>
  </div>
</body>
</tt>"""


# ==============================================================================
# 1. 编码与命名空间基础测试
# ==============================================================================

def test_isrc_normalization():
    """测试 ISRC 提取、分隔符清洗与大写格式化。"""
    assert normalize_isrc("GBARL1401524") == "GBARL1401524"
    assert normalize_isrc("gb-arl-14-01524") == "GBARL1401524"
    assert normalize_isrc("US-UM7-14-13645") == "USUM71413645"
    assert normalize_isrc(" cn-a01-02-304-00 ") == "CNA010230400"

    assert normalize_isrc("") is None
    assert normalize_isrc("GBARL140152") is None       # 11 位
    assert normalize_isrc("GBARL14015240") is None     # 13 位
    assert normalize_isrc("12ARL1401524") is None      # 国家码前两位必须是字母
    assert normalize_isrc(None) is None


def test_isrc_validation():
    """测试 ISRC 有效性判定布尔函数。"""
    assert is_valid_isrc("GBARL1401524") is True
    assert is_valid_isrc("invalid_isrc") is False
    assert is_valid_isrc(None) is False


def test_lrc_red_namespace_normalization():
    """测试将 xmlns:lrc 转为 xmlns:itunes 命名空间。"""
    norm = normalize_lrc_red_namespaces(SAMPLE_LRC_RED_TTML)
    assert 'xmlns:lrc=' not in norm
    assert 'xmlns:itunes="http://music.apple.com/lyric-ttml-internal"' in norm
    assert 'itunes:timing="Word"' in norm
    assert 'itunes:key="L1"' in norm


# ==============================================================================
# 2. TTML 解析与词边界测试 (T01 - T07)
# ==============================================================================

def test_t01_t02_english_word_boundaries_and_join():
    """T01 & T02: 英文音节切分 (Hel+lo) 无空格 vs 词间 (This+hit) 有空格。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>
      <p begin="0.0" end="2.0">
        <span begin="0.0" end="0.5">Hel</span><span begin="0.5" end="1.0">lo</span> <span begin="1.0" end="1.5">world</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    lines = parsed["lines"]
    assert len(lines) == 1
    words = lines[0]["words"]
    assert len(words) == 3
    assert words[0]["text"] == "Hel" and words[0]["join"] == "none"
    assert words[1]["text"] == "lo " and words[1]["join"] == "space"
    assert words[2]["text"] == "world" and words[2]["join"] == "none"

    # 验证 converter 格式化不把 Hello 拆成两个单词
    from core.utils.time_utils import strip_timing_tags
    formatted = format_verbatim_words_line(words, 0, 2000, precision=2)
    plain = strip_timing_tags(formatted)
    assert plain == "Hello world"


def test_t03_cjk_adjacent_characters_no_extra_space():
    """T03: 中文相邻字符之间即使带有排版换行也不得插入空格。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>
      <p begin="0.0" end="2.0">
        <span begin="0.0" end="0.5">你</span>\n  <span begin="0.5" end="1.0">好</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    words = parsed["lines"][0]["words"]
    assert len(words) == 2
    assert words[0]["text"] == "你"
    assert words[0]["join"] == "none"
    lines_txt = parsed["lines"][0]["text"]
    assert lines_txt == "你好"


def test_t04_xml_entities_unescaped():
    """T04: XML 实体 &amp; / &quot; / &apos; / &lt; 正确解码。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <head><metadata>
      <songwriters><songwriter>Tom &amp; Jerry</songwriter></songwriters>
    </metadata></head>
    <body><div>
      <p begin="0.0" end="2.0">
        <span begin="0.0" end="1.0">Rock &amp; Roll</span> <span begin="1.0" end="2.0">&quot;Special&quot;</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    assert "Tom & Jerry" in parsed["songwriters"]
    assert "Rock & Roll" in parsed["lines"][0]["text"]
    assert '"Special"' in parsed["lines"][0]["text"]


def test_t05_multi_div_chronological_sorting():
    """T05: 多 <div> 交错时间轴按照实际开始时间排序。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body>
      <div begin="10.0" end="20.0">
        <p begin="10.0" end="15.0"><span begin="10.0" end="15.0">Second line</span></p>
      </div>
      <div begin="1.0" end="5.0">
        <p begin="1.0" end="4.0"><span begin="1.0" end="4.0">First line</span></p>
      </div>
    </body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    lines = parsed["lines"]
    assert len(lines) == 2
    assert lines[0]["text"] == "First line"
    assert lines[1]["text"] == "Second line"


def test_t06_bg_vocals_roman_stripped_translation_kept():
    """T06: 和声 x-bg 与音译 x-roman 剥离，译文 x-translation 保留。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>
      <p begin="1.0" end="5.0">
        <span begin="1.0" end="2.0">Main lyric</span>
        <span ttm:role="x-bg" begin="1.5" end="2.0">harmony</span>
        <span ttm:role="x-roman" begin="1.0" end="2.0">roman</span>
        <span ttm:role="x-translation">主歌词译文</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    assert parsed["lines"][0]["text"] == "Main lyric"
    assert parsed["lines"][0]["translation"] == "主歌词译文"
    assert parsed["has_translation"] is True


def test_t07_inverted_timestamps_audit_metric():
    """T07: 时间戳逆序时正确被审计指标捕捉。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>
      <p begin="1.0" end="5.0">
        <span begin="3.0" end="2.0">Bad time</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    assert parsed["invalid_word_lines"] >= 1


def test_t08_missing_span_end_timestamp_recovery():
    """T08: span 缺失 end 属性时，按下一个 span begin 或行尾安全兜底恢复。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>
      <p begin="0.0" end="2.0">
        <span begin="0.0">Hello</span> <span begin="1.0">world</span>
      </p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    assert parsed["has_verbatim"] is True
    assert len(parsed["lines"][0]["words"]) == 2
    w0 = parsed["lines"][0]["words"][0]
    w1 = parsed["lines"][0]["words"][1]
    assert w0["start"] == 0 and w0["end"] == 1000
    assert w1["start"] == 1000 and w1["end"] == 2000


def test_t09_paragraph_level_roles_and_translation_attach():
    """T09: 段落级 ttm:role="x-translation" 附着到前一行，x-bg 整段过滤，不产生幽灵行。"""
    ttml = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">
    <body><div>
      <p begin="1.0" end="3.0"><span begin="1.0" end="3.0">Hello world</span></p>
      <p begin="1.0" end="3.0" ttm:role="x-translation">你好世界</p>
      <p begin="3.0" end="4.0" ttm:role="x-bg"><span>(Background)</span></p>
    </div></body></tt>"""
    parsed = parse_lrc_red_ttml(ttml)
    assert len(parsed["lines"]) == 1
    assert parsed["lines"][0]["text"] == "Hello world"
    assert parsed["lines"][0]["translation"] == "你好世界"
    assert parsed["singing_lines"] == 1


def test_t10_safe_empty_or_none_ttml():
    """T10: None 或空字符串 TTML 输入安全返回空结构，严防崩溃。"""
    res1 = parse_lrc_red_ttml(None)
    assert res1["has_verbatim"] is False and len(res1["lines"]) == 0
    res2 = parse_lrc_red_ttml("")
    assert res2["has_verbatim"] is False and len(res2["lines"]) == 0


# ==============================================================================
# 3. 缓存安全性测试 (C01 - C04)
# ==============================================================================

def test_c01_cache_cross_contamination_prevented():
    """C01: 同 ISRC 下，音轨 A (270s) 成功后，音轨 B (230s) 查询重新仲裁并拒绝。"""
    LrcRedFetcher._raw_cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        cand_a = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_a is not None
        assert cand_a["match"]["confidence"] == "strict"

        # 音轨 B：时差 40s，门禁必须重新执行并拒绝
        cand_b = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=230000, title="Song A", artist="Artist A")
        assert cand_b is None


def test_c02_no_negative_cache_lockout():
    """C02: 音轨 A 因本地时长不对齐被拒，不得负缓存锁死后续正确的音轨 B。"""
    LrcRedFetcher._raw_cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        # 错误时长请求
        cand_a = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=100000, title="Song A", artist="Artist A")
        assert cand_a is None

        # 正确时长请求：应当成功
        cand_b = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_b is not None


def test_c03_cache_hit_avoids_network_but_revalidates():
    """C03: 缓存命中时不发生额外网络请求，但仲裁逻辑正常执行。"""
    LrcRedFetcher._raw_cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        cand1 = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand1 is not None
        assert mock_http.call_count == 1

        cand2 = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand2 is not None
        assert mock_http.call_count == 1  # 未发生二次网络请求


def test_c04_network_timeout_or_429_recovery():
    """C04: 临时网络错误/限流后恢复，后续查询能成功重试。"""
    LrcRedFetcher._raw_cache.clear()
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        # 第一次超时
        mock_http.return_value = HttpResult(status=0, text="", bytes_len=0, elapsed_ms=50, error="timeout")
        assert LrcRedFetcher.fetch_by_isrc("USUM71413645") is None

        # 第二次恢复 200
        mock_json = {"title": "Song A", "artist": "Artist A", "duration": 270.0, "source": SAMPLE_LRC_RED_TTML}
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error="")
        assert LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A") is not None


# ==============================================================================
# 4. 门禁与版本互斥测试 (M01 - M06)
# ==============================================================================

def test_m01_m02_isrc_version_exclusion_live_and_remix():
    """M01 & M02: ISRC 直查对 Live 与 Remix 互斥版本实施硬拦截。"""
    LrcRedFetcher._raw_cache.clear()
    live_json = {"title": "Hotel California (Live)", "artist": "Eagles", "duration": 390.0, "source": SAMPLE_LRC_RED_TTML}
    remix_json = {"title": "Uptown Funk (Remix)", "artist": "Mark Ronson", "duration": 270.0, "source": SAMPLE_LRC_RED_TTML}

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        # Live 互斥
        mock_http.return_value = HttpResult(status=200, text=json.dumps(live_json), bytes_len=100, elapsed_ms=30, error="")
        cand_live = LrcRedFetcher.fetch_by_isrc("USEE10000001", duration_ms=390000, title="Hotel California", artist="Eagles")
        assert cand_live is None

        # Remix 互斥
        LrcRedFetcher._raw_cache.clear()
        mock_http.return_value = HttpResult(status=200, text=json.dumps(remix_json), bytes_len=100, elapsed_ms=30, error="")
        cand_remix = LrcRedFetcher.fetch_by_isrc("USEE10000002", duration_ms=270000, title="Uptown Funk", artist="Mark Ronson")
        assert cand_remix is None


def test_m04_m05_remote_missing_meta_or_different_artist():
    """M04 & M05: 远端缺失元数据拒绝自我认证；艺人不同直接拒绝。"""
    LrcRedFetcher._raw_cache.clear()
    missing_json = {"duration": 270.0, "source": SAMPLE_LRC_RED_TTML}
    diff_art_json = {"title": "Song A", "artist": "Unknown Band", "duration": 270.0, "source": SAMPLE_LRC_RED_TTML}

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        # 远端缺失标题/歌手
        mock_http.return_value = HttpResult(status=200, text=json.dumps(missing_json), bytes_len=100, elapsed_ms=30, error="")
        cand_missing = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_missing is None

        # 艺人不同
        LrcRedFetcher._raw_cache.clear()
        mock_http.return_value = HttpResult(status=200, text=json.dumps(diff_art_json), bytes_len=100, elapsed_ms=30, error="")
        cand_diff = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_diff is None


def test_m06_ambiguous_candidates_not_arbitrary():
    """M06: 候选集无法消歧时不强制乱选。"""
    # 1. 冲突版本直接过滤
    hits_ver = [
        {"isrc": "ISRC1", "title": "Song (Live 1)", "artist": "Singer", "duration": 200.0},
        {"isrc": "ISRC2", "title": "Song (Remix 1)", "artist": "Singer", "duration": 200.0},
    ]
    best_ver = LrcRedFetcher._rank_and_filter_hits(hits_ver, title="Song", artist="Singer", duration_ms=200000)
    assert len(best_ver) == 0

    # 2. 等分且无法消歧候选（同歌名、同艺人、同时长但不同 ISRC）
    hits_tie = [
        {"isrc": "USUM71413645", "title": "Song", "artist": "Singer", "duration": 200.0},
        {"isrc": "GBARL1401524", "title": "Song", "artist": "Singer", "duration": 200.0},
    ]
    best_tie = LrcRedFetcher._rank_and_filter_hits(hits_tie, title="Song", artist="Singer", duration_ms=200000)
    assert len(best_tie) == 0, "等分冲突 ISRC 必须放弃选择，不得武断任取"

    # 3. 若有专辑信息可成功消歧，则允许放行
    hits_disambig = [
        {"isrc": "USUM71413645", "title": "Song", "artist": "Singer", "album": "Target Album", "duration": 200.0},
        {"isrc": "GBARL1401524", "title": "Song", "artist": "Singer", "album": "Other Album", "duration": 200.0},
    ]
    best_ok = LrcRedFetcher._rank_and_filter_hits(hits_disambig, title="Song", artist="Singer", duration_ms=200000, album="Target Album")
    assert len(best_ok) == 2 and best_ok[0]["isrc"] == "USUM71413645"


# ==============================================================================
# 5. byModel 与质量审计测试 (Q01 - Q04)
# ==============================================================================

def test_q01_q02_q03_by_model_handling():
    """Q01 - Q03: byModel 状态管理（True 拒 T1，False 需全验，None 为 unknown）。"""
    LrcRedFetcher._raw_cache.clear()

    # Q01: True 拒绝交付为 T1
    mock_true = {"title": "Song", "artist": "Art", "duration": 200.0, "byModel": True, "source": SAMPLE_LRC_RED_TTML}
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_true), bytes_len=100, elapsed_ms=30, error="")
        assert LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song", artist="Art") is None

    # Q02: False 允许且具备 ISRC 写回资格
    LrcRedFetcher._raw_cache.clear()
    mock_false = {"title": "Song", "artist": "Art", "duration": 200.0, "byModel": False, "source": SAMPLE_LRC_RED_TTML}
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_false), bytes_len=100, elapsed_ms=30, error="")
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song", artist="Art")
        assert cand is not None
        assert cand["match"]["confidence"] == "strict"
        assert cand["isrc_write_eligible"] is True

    # Q03: None / 缺失标记为 unknown，不给 ISRC 写回资格
    LrcRedFetcher._raw_cache.clear()
    mock_none = {"title": "Song", "artist": "Art", "duration": 200.0, "source": SAMPLE_LRC_RED_TTML}
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_none), bytes_len=100, elapsed_ms=30, error="")
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song", artist="Art")
        assert cand is not None
        assert cand["match"]["by_model"] == "unknown"
        assert cand["isrc_write_eligible"] is False


def test_q04_insufficient_word_coverage_rejected():
    """Q04: 演唱行逐字覆盖率不足 95% 时（如 50 行仅 1 行有逐字）拒绝。"""
    lines_xml = []
    # 1 行带逐字
    lines_xml.append('<p begin="1.0" end="2.0"><span begin="1.0" end="2.0">word</span></p>')
    # 49 行纯文本无逐字
    for i in range(2, 51):
        lines_xml.append(f'<p begin="{i}.0" end="{i+1}.0">Plain line {i}</p>')

    sparse_ttml = f"""<tt xmlns="http://www.w3.org/ns/ttml">
    <body><div>{"".join(lines_xml)}</div></body></tt>"""

    parsed = parse_lrc_red_ttml(sparse_ttml)
    assert parsed["singing_lines"] == 50
    assert parsed["native_word_lines"] == 1
    assert parsed["word_coverage_ratio"] < 0.95

    LrcRedFetcher._raw_cache.clear()
    mock_sparse = {"title": "Song", "artist": "Art", "duration": 100.0, "byModel": False, "source": sparse_ttml}
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_sparse), bytes_len=100, elapsed_ms=30, error="")
        assert LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=100000, title="Song", artist="Art") is None


# ==============================================================================
# 6. 公开接口降级与网络契约测试 (A06 - A08)
# ==============================================================================

def test_a06_a07_fallback_to_public_endpoints():
    """A06 & A07: .song.json 404 或无 source 时降级拉取 .ttml 与 .json。"""
    LrcRedFetcher._raw_cache.clear()
    meta_json = {"title": "Song A", "artist": "Artist A", "duration": 270.0, "byModel": False}

    def fake_http(url, **kwargs):
        if url.endswith(".song.json"):
            return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=20, error="")
        elif url.endswith(".json"):
            return HttpResult(status=200, text=json.dumps(meta_json), bytes_len=50, elapsed_ms=20, error="")
        elif url.endswith(".ttml"):
            return HttpResult(status=200, text=SAMPLE_LRC_RED_TTML, bytes_len=len(SAMPLE_LRC_RED_TTML), elapsed_ms=20, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=fake_http):
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand is not None
        assert cand["provenance"]["endpoint_kind"] == "ttml"


def test_a08_oversized_response_rejected():
    """A08: 响应体积超限时受控拦截。"""
    LrcRedFetcher._raw_cache.clear()
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        # 模拟超限返回
        mock_http.return_value = HttpResult(status=200, text="", bytes_len=3 * 1024 * 1024, elapsed_ms=50, error="oversized")
        assert LrcRedFetcher.fetch_by_isrc("USUM71413645") is None


def test_r04_isrc_write_requires_valid_local_artist():

    """R04: 本地艺人缺失、为泛称(群星/Unknown)或与远端无交集时，严禁赋予 ISRC 写入资格。"""
    LrcRedFetcher._raw_cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Adele",
        "duration": 200.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error="")

        # 1. 本地艺人缺失：即使标题与时长完全吻合，也绝对不得写回 ISRC
        LrcRedFetcher._raw_cache.clear()
        cand_no_artist = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist="")
        assert cand_no_artist is not None
        assert cand_no_artist["isrc_write_eligible"] is False, "本地缺失歌手时严禁写回 ISRC"
        assert cand_no_artist["match"]["confidence"] != "strict"

        # 2. 本地艺人为泛称 (群星/Various Artists/Unknown)
        for generic in ("群星", "Various Artists", "Unknown", "未知"):
            LrcRedFetcher._raw_cache.clear()
            cand_generic = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist=generic)
            assert cand_generic is not None
            assert cand_generic["isrc_write_eligible"] is False, f"本地歌手为泛称 '{generic}' 时严禁写回 ISRC"
            assert cand_generic["match"]["confidence"] != "strict"

        # 3. 本地艺人与远端不匹配（同歌名同长度不同歌手）
        LrcRedFetcher._raw_cache.clear()
        cand_diff_art = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist="Taylor Swift")
        # 艺人无交集会被直接丢弃候选
        assert cand_diff_art is None

        # 4. 本地艺人与远端正确匹配：正常放行
        LrcRedFetcher._raw_cache.clear()
        cand_ok = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist="Adele")
        assert cand_ok is not None
        assert cand_ok["isrc_write_eligible"] is True


def test_r05_by_model_false_never_claims_official():
    """R05: byModel=False 仅代表非模型生成，不可自我宣告为 official 官方认证。"""
    LrcRedFetcher._raw_cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Adele",
        "duration": 200.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error="")
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist="Adele")
        assert cand is not None
        assert cand["provenance"]["origin_status"] == "unverified_origin", "byModel=False 不可标注为 official"


def test_r06_version_symmetry_and_remaster_isrc_guard():
    """R06: 录音版本硬门禁（双向互斥）及 Remaster 版本差异禁止写回 ISRC。"""
    LrcRedFetcher._raw_cache.clear()
    mock_remaster = {
        "title": "Song A (Remastered 2020)",
        "artist": "Adele",
        "duration": 200.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_remaster), bytes_len=100, elapsed_ms=30, error="")
        # 本地为原版录音，远端为 Remaster 版：虽然时间轴可能兼容，但不可回写 ISRC
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000, title="Song A", artist="Adele")
        assert cand is not None
        assert cand["isrc_write_eligible"] is False, "Remaster 版本不对称时绝对不得写回 ISRC"


def test_r12_unified_429_circuit_breaker_cooling():
    """R12: 任意端点返回 429 均激活共享退避冷却，冷却期内不向远端发起多余请求。"""
    LrcRedFetcher.clear_cache()
    try:
        call_count = [0]

        def mock_429_http(*args, **kwargs):
            call_count[0] += 1
            return HttpResult(status=429, text="Too Many Requests", bytes_len=17, elapsed_ms=10, error="")

        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=mock_429_http):
            res1 = LrcRedFetcher._fetch_raw_record("USUM71413645")
            assert res1 is None
            assert call_count[0] == 1
            assert LrcRedFetcher._cooldown_until > time.perf_counter()

            # 再次发起请求，应当直接命中冷却期拦截，不再调用底层 HTTP
            res2 = LrcRedFetcher._fetch_raw_record("USUM71413646")
            assert res2 is None
            assert call_count[0] == 1, "冷却期内不得发起真实网络请求"
    finally:
        LrcRedFetcher.clear_cache()


def test_r13_cache_memory_budget_lru_eviction():
    """R13: _raw_cache 受控于总内存字节预算（32MB）与条目上限，超限执行 LRU 淘汰。"""
    LrcRedFetcher.clear_cache()
    try:
        # 临时将字节上限缩为小阈值以便确定性触发
        orig_max_bytes = LrcRedFetcher.MAX_CACHE_BYTES
        LrcRedFetcher.MAX_CACHE_BYTES = 1000

        mock_payload_small = {
            "title": "Song",
            "artist": "Artist",
            "duration": 180.0,
            "source": "<tt>Small Payload 600 bytes</tt>" + ("x" * 400),
        }
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
            mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_payload_small), bytes_len=600, elapsed_ms=10, error="")
            rec1 = LrcRedFetcher._fetch_raw_record("ISRC10000001")
            assert rec1 is not None
            assert "ISRC10000001" in LrcRedFetcher._raw_cache
            bytes_before = LrcRedFetcher._cache_bytes

            # 插入第二个记录，总字节将超过 1000 bytes，必然触发 LRU 淘汰 ISRC10000001
            rec2 = LrcRedFetcher._fetch_raw_record("ISRC10000002")
            assert rec2 is not None
            assert "ISRC10000002" in LrcRedFetcher._raw_cache
            assert "ISRC10000001" not in LrcRedFetcher._raw_cache, "首条记录应因内存预算超限被 LRU 淘汰"
            assert LrcRedFetcher._cache_bytes <= LrcRedFetcher.MAX_CACHE_BYTES
    finally:
        LrcRedFetcher.MAX_CACHE_BYTES = orig_max_bytes
        LrcRedFetcher.clear_cache()


def test_r06_version_album_live_studio_exclusion():
    """R06: 专辑名称含 Live/现场 版本标识时参与版本互斥，杜绝录音室原曲匹配现场版专辑。"""
    LrcRedFetcher.clear_cache()
    mock_live_album = {
        "title": "Hotel California",
        "artist": "Eagles",
        "album": "Hell Freezes Over (Live)",
        "duration": 390.0,
        "byModel": False,
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_live_album), bytes_len=100, elapsed_ms=30, error="")
        # 1. 本地为录音室专辑，远端为 Live 专辑 -> 必须被版本互斥门禁拦截
        cand = LrcRedFetcher.fetch_by_isrc("USEE10000001", duration_ms=390000, title="Hotel California", artist="Eagles", album="Hotel California")
        assert cand is None, "录音室专辑与远端 Live 专辑必须被版本互斥硬拒"

        # 2. 本地为 Live 专辑，远端也为 Live 专辑 -> 允许匹配
        LrcRedFetcher.clear_cache()
        cand_live = LrcRedFetcher.fetch_by_isrc("USEE10000001", duration_ms=390000, title="Hotel California", artist="Eagles", album="Hell Freezes Over (Live)")
        assert cand_live is not None
        assert cand_live["isrc_write_eligible"] is True


def test_r12_429_respects_retry_after_header():
    """R12: 429 响应带 Retry-After 头时，冷却时间应至少为 Retry-After 指定秒数。"""
    LrcRedFetcher.clear_cache()
    try:
        def mock_retry_after(*args, **kwargs):
            return HttpResult(status=429, text="Too Many Requests", bytes_len=17, elapsed_ms=10, error="", headers={"Retry-After": "120"})

        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=mock_retry_after):
            t_start = time.perf_counter()
            res = LrcRedFetcher._fetch_raw_record("USUM71413645")
            assert res is None
            # 应当解析出 120 秒冷却
            assert LrcRedFetcher._cooldown_until >= t_start + 119.0
    finally:
        LrcRedFetcher.clear_cache()


def test_r13_true_lru_access_preserves_accessed_entry():
    """R13: 验证真实 LRU 机制：缓存命中时刷新 recency，超限时淘汰最久未访问条目而非单纯 FIFO。"""
    LrcRedFetcher.clear_cache()
    try:
        orig_max_bytes = LrcRedFetcher.MAX_CACHE_BYTES
        # 允许容纳约 2 个条目
        LrcRedFetcher.MAX_CACHE_BYTES = 1300

        mock_payload = {
            "title": "Song",
            "artist": "Artist",
            "duration": 180.0,
            "source": "<tt>Payload 500 bytes</tt>" + ("y" * 400),
        }
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
            mock_http.return_value = HttpResult(status=200, text=json.dumps(mock_payload), bytes_len=550, elapsed_ms=10, error="")
            # 1. 插入 A 和 B
            LrcRedFetcher._fetch_raw_record("ISRC_A")
            LrcRedFetcher._fetch_raw_record("ISRC_B")
            assert "ISRC_A" in LrcRedFetcher._raw_cache
            assert "ISRC_B" in LrcRedFetcher._raw_cache

            # 2. 访问 A（使 A 成为最近使用，B 成为最久未用 LRU）
            rec_a = LrcRedFetcher._fetch_raw_record("ISRC_A")
            assert rec_a is not None

            # 3. 插入 C（超出 1300 bytes 预算，必须淘汰 B，保留 A！）
            LrcRedFetcher._fetch_raw_record("ISRC_C")
            assert "ISRC_A" in LrcRedFetcher._raw_cache, "最近访问过的 A 必须保留"
            assert "ISRC_C" in LrcRedFetcher._raw_cache, "新插入的 C 必须保留"
            assert "ISRC_B" not in LrcRedFetcher._raw_cache, "最久未访问的 B 必须被 LRU 淘汰"
    finally:
        LrcRedFetcher.MAX_CACHE_BYTES = orig_max_bytes
        LrcRedFetcher.clear_cache()


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
