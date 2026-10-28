# -*- coding: utf-8 -*-
"""网易云 YRC 逐字通道（v1.6.1）：解析、尾部截断、fetcher 分支、聚合器接缝。

纯离线：所有网络调用都被 patch，不依赖外网、音频与声学环境。
运行：python tests/test_netease_yrc.py
"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import inspect
import os
import re
import sys
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.converter import to_spl                                   # noqa: E402
from core.fetchers.kugou import (                                   # noqa: E402
    OVERRUN_TOLERANCE_MS,
    parsed_duration_reject_reason,
)
from core.fetchers.netease import NetEaseFetcher                    # noqa: E402
from core.parsers.lrc_parser import parse_lrc                       # noqa: E402
from core.parsers.yrc_parser import (                               # noqa: E402
    OVERRUN_TOL_MS,
    parse_yrc,
    trim_tail_staff,
)
from core.utils.time_utils import compute_duration_axis             # noqa: E402

# ---------------------------------------------------------------------------
# 内联样本（不落 fixture 文件）
# ---------------------------------------------------------------------------

# 第 1 行：英文，含零时长标点块 `(2000,0,0), ` 与行尾空文本块 `(3000,0,0)`
# 第 2 行：中文，含纯空白块 `(4770,30,0) ` 与行尾空文本块
YRC_BASIC = (
    "[1000,2000](1000,500,0)But (1500,500,0)baby(2000,0,0), "
    "(2000,500,0)I've (2500,500,0)been(3000,0,0)\n"
    "[4000,2530](4000,470,0)整(4470,300,0)晚(4770,30,0) "
    "(4800,230,0)胡(5030,550,0)思(5580,470,0)乱(6050,480,0)想(6530,0,0)\n"
)

# 歌词文本自带半角括号（实测《夜空中最亮的星》署名行形态）
YRC_PAREN = (
    "[240000,1000](240000,263,0) 母带 : Patrick Weyer-Brown (FR) 法国SAE Paris 录音棚\n"
)


def _lines_sample(specs):
    """[(start, dur, text), ...] → 每行一个字块的 YRC 文本。"""
    return "".join(f"[{s},{d}]({s},{d},0){t}\n" for s, d, t in specs)


def _overrun_sample(normal=8, normal_dur=2500, staff=5, staff_start=36000):
    """前 normal 行正常歌词，后 staff 行 1000ms 等距名单（末行冲轴）。"""
    specs = [(i * normal_dur, normal_dur, f"歌词{i}") for i in range(normal)]
    specs += [(staff_start + i * 1000, 1000, f" 制作人{i}") for i in range(staff)]
    return _lines_sample(specs)


def _payload(lrc="", tlyric="", yrc="", ytlrc=""):
    """模拟 /api/song/lyric 返回体（与真实结构同形）。"""
    def node(text):
        return {"lyric": text}

    return {
        "lrc": node(lrc),
        "tlyric": node(tlyric),
        "yrc": node(yrc),
        "ytlrc": node(ytlrc),
        "code": 200,
    }


def _patched_fetch(payload, duration_ms=0):
    """patch 掉检索与 HTTP，直接跑 NetEaseFetcher.fetch。"""
    with mock.patch.object(NetEaseFetcher, "search_song_id", return_value=123456), \
         mock.patch("core.fetchers.netease.http_get_json", side_effect=lambda *a, **k: payload):
        return NetEaseFetcher.fetch("测试曲", "测试人", duration_ms=duration_ms)


def _rendered(line):
    """把一行 ELRC 还原成播放器实际显示的文字（去掉行标签与词级标签）。"""
    body = re.sub(r"^\[[^\]]*\]", "", line)
    body = re.sub(r"<[^>]*>", "", body)
    return body


def _is_verbatim(line):
    return bool(re.match(r"^\[\d{2}:\d{2}\.\d{3}\]<", line))


# ---------------------------------------------------------------------------
# 解析层
# ---------------------------------------------------------------------------

def test_yrc_paren_in_text_not_truncated():
    """歌词里的 (FR) 这类括号不得被当成字块起点截断文本。"""
    lines = parse_yrc(YRC_PAREN)["lines"]
    assert len(lines) == 1, lines
    text = lines[0]["text"]
    assert "(FR)" in text, text
    assert text.endswith("录音棚"), text


def test_yrc_zero_duration_punct_merged():
    """零时长标点块并入前字，不产出 duration<=0 的 word。"""
    lines = parse_yrc(YRC_BASIC)["lines"]
    words = lines[0]["words"]
    assert all(w["duration"] > 0 for w in words), words
    assert any(w["text"].endswith(", ") for w in words), words
    starts = [w["start"] for w in words]
    assert starts == sorted(starts), starts


def test_yrc_space_block_merged_keeps_text():
    """纯空白块并入前字，行文本保留原空格，且不产出纯空白 word。"""
    lines = parse_yrc(YRC_BASIC)["lines"]
    assert lines[1]["text"] == "整晚 胡思乱想", lines[1]["text"]
    for ln in lines:
        assert all(w["text"].strip() for w in ln["words"]), ln["words"]


def test_yrc_tail_blank_block_dropped():
    """行尾空文本块被丢弃，不制造空字，也不延长末字。"""
    line = parse_yrc(YRC_BASIC)["lines"][0]
    assert all(w["text"] for w in line["words"]), line["words"]
    # 末字 been 止于 3000，行尾空块 (3000,0) 未把末字拉长
    assert line["end"] == 3000, line


def test_yrc_lead_zero_and_end_identity():
    """行 start == 首字 start（lead 恒 0），行 end == 末字 end。"""
    for ln in parse_yrc(YRC_BASIC)["lines"]:
        assert ln["start"] == ln["words"][0]["start"], ln
        assert ln["end"] == ln["words"][-1]["end"], ln
        assert ln["duration"] == ln["end"] - ln["start"], ln


def test_yrc_text_equals_joined_words():
    """line.text 必须是 words 的原样拼接（converter 一致性校验的前提）。"""
    for ln in parse_yrc(YRC_BASIC)["lines"]:
        assert ln["text"] == "".join(w["text"] for w in ln["words"]), ln


def test_yrc_time_precision_is_three():
    """YRC 是毫秒整数，必须显式给 3；不能靠 detect_time_precision 回落 2。"""
    assert parse_yrc(YRC_BASIC)["time_precision"] == 3


def test_yrc_to_spl_no_degrade():
    """解析结果喂 converter 后全部是逐字行，不触发「字面不一致」降级。"""
    data = parse_yrc(YRC_BASIC)
    data["source"] = "netease"
    out = to_spl(data, title="测试曲", artist="测试人")
    verbatim = [ln for ln in out.splitlines() if _is_verbatim(ln)]
    assert len(verbatim) == 2, out
    joined = re.sub(r"\s+", "", "".join(_rendered(ln) for ln in verbatim))
    assert joined == "Butbaby,I'vebeen整晚胡思乱想", joined


# ---------------------------------------------------------------------------
# 尾部名单截断
# ---------------------------------------------------------------------------

def test_tail_staff_trimmed_when_overrun():
    """尾部 1000ms 等距名单被截掉，截后能过长门禁。"""
    parsed = parse_yrc(_overrun_sample(), duration_ms=30000)
    assert len(parsed["lines"]) == 8, len(parsed["lines"])
    axis = compute_duration_axis(parsed["lines"], 0)
    assert parsed_duration_reject_reason(axis, 30000) is None, axis


def test_overrun_without_trim_would_be_rejected():
    """反证：同一份文本不截断时会被判 overrun —— 截断确有必要。"""
    raw = parse_yrc(_overrun_sample(), duration_ms=0)["lines"]
    assert len(raw) == 13, len(raw)
    axis = compute_duration_axis(raw, 0)
    assert parsed_duration_reject_reason(axis, 30000) == "overrun", axis


def test_tail_trim_normal_outro_untouched():
    """正常尾奏（3000/2500/4000ms）不冲轴，一行不动。"""
    text = _lines_sample([
        (0, 3000, "第一句"), (3000, 2500, "第二句"), (5500, 4000, "第三句"),
    ])
    parsed = parse_yrc(text, duration_ms=30000)
    assert [ln["text"] for ln in parsed["lines"]] == ["第一句", "第二句", "第三句"]


def test_tail_trim_uniform_tail_inside_track_untouched():
    """末 3 行恰好都是 1000ms，但不冲轴 → 前置门不触发，原样保留。"""
    text = _lines_sample([
        (0, 3000, "第一句"), (3000, 1000, "甲"), (4000, 1000, "乙"), (5000, 1000, "丙"),
    ])
    parsed = parse_yrc(text, duration_ms=30000)
    assert len(parsed["lines"]) == 4, parsed["lines"]


def test_tail_trim_skipped_when_duration_unknown():
    """无曲长基准时不做截断（此时门禁本身也不生效）。"""
    parsed = parse_yrc(_overrun_sample(), duration_ms=0)
    assert len(parsed["lines"]) == 13, len(parsed["lines"])


def test_tail_trim_unrecoverable_returns_empty():
    """截断后不足 4 行 → 放弃 YRC，交回 lrc。"""
    text = _overrun_sample(normal=3)
    parsed = parse_yrc(text, duration_ms=30000)
    assert parsed["lines"] == [], parsed["lines"]


def test_tail_trim_still_overrun_returns_empty():
    """名单段截掉后仍冲轴（整体错版）→ 放弃 YRC，绝不硬砍真实歌词。"""
    text = _overrun_sample(normal=8, normal_dur=5000, staff_start=40000)
    parsed = parse_yrc(text, duration_ms=30000)
    assert parsed["lines"] == [], parsed["lines"]


def test_overrun_tolerance_matches_kugou():
    """与六源统一门禁的容忍值必须一致（防两处常量漂移）。"""
    assert OVERRUN_TOL_MS == OVERRUN_TOLERANCE_MS


# ---------------------------------------------------------------------------
# fetcher 分支
# ---------------------------------------------------------------------------

def test_fetch_prefers_yrc_marks_word():
    """有 yrc 时优先出逐字，并优先采用同轴的 ytlrc 作译文。"""
    ytlrc = "[00:01.00]译一\n[00:02.00]译二\n[00:03.00]译三\n[00:04.00]译四\n"
    got = _patched_fetch(_payload(lrc="[00:01.00]行级", tlyric="[00:01.00]旧译", yrc=YRC_BASIC, ytlrc=ytlrc))
    assert got is not None
    assert got["source"] == "netease"
    assert got["lyric_type"] == "word"
    assert got["time_precision"] == 3
    assert got["tlyric"] == ytlrc, "ytlrc 应优先于 tlyric"
    assert all(ln["words"] for ln in got["lines"]), got["lines"]


def test_fetch_yrc_absent_identical_to_parse_lrc():
    """无 yrc 时行为与升级前逐字段一致。"""
    lrc = "[00:01.00]第一句\n[00:04.00]第二句\n[00:07.00]第三句\n[00:10.00]第四句\n"
    tlyric = "[00:01.00]one\n[00:04.00]two\n[00:07.00]three\n[00:10.00]four\n"
    got = _patched_fetch(_payload(lrc=lrc, tlyric=tlyric))
    base = parse_lrc(lrc)
    assert got["lyric_type"] == "line"
    assert got["time_precision"] == base["time_precision"]
    assert [(ln["start"], ln["end"], ln["text"]) for ln in got["lines"]] == \
           [(ln["start"], ln["end"], ln["text"]) for ln in base["lines"]]
    assert not any(ln["words"] for ln in got["lines"])
    assert got["tlyric"] == tlyric


def test_fetch_allows_yrc_only_without_lrc():
    """lrc 为空但有 yrc（实测《成都》id 436514312 形态）不得整首丢弃。"""
    got = _patched_fetch(_payload(lrc="", yrc=YRC_BASIC))
    assert got is not None
    assert got["lyric_type"] == "word"
    assert len(got["lines"]) == 2


def test_fetch_empty_body_returns_none():
    """四个字段全空 → 与升级前一致返回 None。"""
    assert _patched_fetch(_payload()) is None


def test_fetch_malformed_yrc_falls_back_to_lrc():
    """yrc 是垃圾串时回落 lrc，不抛异常、不丢歌。"""
    lrc = "[00:01.00]第一句\n[00:04.00]第二句\n[00:07.00]第三句\n[00:10.00]第四句\n"
    got = _patched_fetch(_payload(lrc=lrc, yrc="这不是 yrc 文本\n随便两行\n"))
    assert got is not None
    assert got["lyric_type"] == "line"
    assert len(got["lines"]) == 4


def test_fetch_signature_unchanged():
    """fetch 的参数表不得变化（保护显式关键字签名的假体与调度层）。"""
    params = list(inspect.signature(NetEaseFetcher.fetch).parameters)
    assert params == ["title", "artist", "duration_ms", "album"], params


def test_fetch_yrc_parse_exception_falls_back():
    """解析器抛异常时必须回落 lrc（try/except 分支），不得把整首丢掉。"""
    lrc = "[00:01.00]第一句\n[00:04.00]第二句\n[00:07.00]第三句\n[00:10.00]第四句\n"
    with mock.patch.object(NetEaseFetcher, "search_song_id", return_value=1), \
         mock.patch("core.fetchers.netease.http_get_json",
                    side_effect=lambda *a, **k: _payload(lrc=lrc, yrc="[1000,2000](1000,500,0)x\n")), \
         mock.patch("core.fetchers.netease.parse_yrc", side_effect=RuntimeError("boom")):
        got = NetEaseFetcher.fetch("歌", "手")
    assert got is not None
    assert got["lyric_type"] == "line"
    assert len(got["lines"]) == 4


def test_lyric_field_tolerates_malformed_nodes():
    """接口把某字段返回成非 dict（字符串 / None）时不得崩，也不影响其他字段。"""
    payload = {
        "lrc": {"lyric": "[00:01.00]x"},
        "tlyric": "not-a-dict",
        "yrc": None,
        "ytlrc": {"lyric": ""},
    }
    with mock.patch.object(NetEaseFetcher, "search_song_id", return_value=1), \
         mock.patch("core.fetchers.netease.http_get_json", side_effect=lambda *a, **k: payload):
        raw = NetEaseFetcher.fetch_raw_lyrics("歌", "手")
    assert raw is not None
    assert raw["lrc"] == "[00:01.00]x"
    assert raw["tlyric"] == "" and raw["yrc"] == "" and raw["ytlrc"] == ""


def test_url_requests_yv_and_rv():
    """必须显式请求 yv=1（逐字）与 rv=1（罗马音），否则服务端不返回 yrc。"""
    captured = {}

    def _http(url, *a, **k):
        captured["url"] = url
        return _payload(lrc="[00:01.00]x")

    with mock.patch.object(NetEaseFetcher, "search_song_id", return_value=1), \
         mock.patch("core.fetchers.netease.http_get_json", side_effect=_http):
        NetEaseFetcher.fetch_raw_lyrics("歌", "手", duration_ms=200000)

    url = captured.get("url", "")
    assert "yv=1" in url, url
    assert "rv=1" in url, url
    assert "lv=1" in url and "tv=-1" in url, url


# ---------------------------------------------------------------------------
# aggregator 接缝（顺序与 _rank 均未改动）
# ---------------------------------------------------------------------------

def _fake_foreign_setup(calls):
    import core.aggregator as agg

    yrc4 = _lines_sample([(1000, 800, "one"), (2000, 800, "two"), (3000, 800, "three"), (4000, 800, "four")])
    tlyric4 = "[00:01.00]译一\n[00:02.00]译二\n[00:03.00]译三\n[00:04.00]译四\n"

    ne_data = parse_yrc(yrc4)
    ne_data["source"] = "netease"
    ne_data["lyric_type"] = "word"
    ne_data["tlyric"] = tlyric4

    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            calls.append("kugou")
            return {
                "source": "kugou",
                "lyric_type": "word",
                "lines": [
                    {"start": 1000 + i * 1000, "end": 1800 + i * 1000, "duration": 800,
                     "text": f"line{i}",
                     "words": [{"start": 1000 + i * 1000, "end": 1800 + i * 1000,
                                "duration": 800, "text": f"line{i}"}]}
                    for i in range(4)
                ],
            }

    class FakeAMLL:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    class FakeNE:
        @classmethod
        def fetch(cls, *a, **k):
            calls.append("netease")
            return {**ne_data, "lines": [dict(ln) for ln in ne_data["lines"]]}

    class FakeLS:
        @classmethod
        def fetch(cls, *a, **k):
            calls.append("lrcshare")
            return None

    class FakeLRCLIB:
        @classmethod
        def fetch(cls, *a, **k):
            calls.append("lrclib")
            return None

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeAMLL
    agg.NetEaseFetcher = FakeNE
    agg.LrcShareFetcher = FakeLS
    agg.LRCLIBFetcher = FakeLRCLIB
    return agg, reals


def test_aggregator_t4_adopts_netease_verbatim():
    """外语：酷狗逐字无译文(rank1) → 网易逐字+译文(rank3) 整包替换，LRCSHARE 不再探。"""
    calls = []
    agg, reals = _fake_foreign_setup(calls)
    try:
        out = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
    finally:
        for name, real in reals.items():
            setattr(agg, name, real)

    assert out is not None
    assert out["source"] == "netease", out.get("source")
    assert out.get("translation_source") == "netease"
    assert all(ln["words"] for ln in out["lines"]), out["lines"]
    assert "lrcshare" not in calls and "lrclib" not in calls, calls


def test_aggregator_chinese_tie_keeps_kugou():
    """华语：酷狗已出逐字(rank3) 与网易(rank3) 同级，顺序不变 → 仍用酷狗，且不再探网易。"""
    calls = []
    agg, reals = _fake_foreign_setup(calls)
    try:
        out = agg.LyricAggregator.fetch_best_lyrics("焚情", "黄凯芹", duration_ms=0)
    finally:
        for name, real in reals.items():
            setattr(agg, name, real)

    assert out is not None
    assert out["source"] == "kugou", out.get("source")
    assert "netease" not in calls, calls


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
