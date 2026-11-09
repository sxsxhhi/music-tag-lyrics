# -*- coding: utf-8 -*-
"""TTML 时间三元组与主唱段分母 H09 / H10 验收。

覆盖：
- 合法短词（10ms / 39ms / 40ms）保留原生身份与原始正时长，只记 short_word_count
- 0ms / 负时长 / 显式非法 end / 缺失 begin → 剔除并计入 invalid_word_count
- 只有 end 真正缺失才推断；已有但不可解析的值不得当作缺失
- 5 字 1 坏 / 5 字全坏 → 行标 words_incomplete、raw_main_text 保留原文字
- 主唱段账本 raw/invalid/accepted 恒等式，非法段不再悄悄缩小分母
- lrc.red 门禁拒绝任何含残缺词的候选（解析器单独调用不自动受此保护）
"""

import json
import os
import sys
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import core.fetchers.lrc_red as lr_mod  # noqa: E402
from core.parsers.lrc_red_ttml import parse_lrc_red_ttml  # noqa: E402
from core.fetchers.lrc_red import LrcRedFetcher  # noqa: E402
from core.utils.http import HttpResult  # noqa: E402

lr_mod.MIN_REQ_INTERVAL_S = 0.0

_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>\n'
)
_TAIL = "</div></body></tt>"


def _ttml(*paragraphs: str) -> str:
    return _HEAD + "\n".join(paragraphs) + "\n" + _TAIL


def _p(spans: str, begin="1.000", end="9.000", extra="") -> str:
    attrs = f'begin="{begin}"' if begin is not None else ""
    if end is not None:
        attrs += f' end="{end}"'
    if extra:
        attrs += " " + extra
    return f"<p {attrs}>{spans}</p>"


def _all_words(res):
    return [w for line in res["lines"] for w in line.get("words", [])]


# --------------------------------------------------------------------------- #
# 1. 合法短词：保留原生身份与原始正时长
# --------------------------------------------------------------------------- #
def test_short_words_keep_native_identity():
    """10ms / 39ms / 40ms 均为合法短词：保留原时长、不变原生日、三元组恒等。"""
    xml = _ttml(
        _p(
            '<span begin="1.000" end="1.010">A</span>'
            '<span begin="1.100" end="1.139">B</span>'
            '<span begin="1.200" end="1.240">C</span>'
        )
    )
    res = parse_lrc_red_ttml(xml)
    words = _all_words(res)
    assert len(words) == 3, words
    durations = [w["duration"] for w in words]
    assert durations == [10, 39, 40], durations
    for w in words:
        assert w["duration"] == w["end"] - w["start"], w
        assert w["start"] >= 0 and w["end"] > w["start"], w
    assert res["short_word_count"] == 2, res["short_word_count"]
    assert res["invalid_word_count"] == 0
    assert res["invalid_word_lines"] == 0
    assert res["native_word_lines"] == 1


def test_short_word_does_not_touch_repair_counter():
    """短词只记账，不算改过时间端点。"""
    res = parse_lrc_red_ttml(_ttml(_p('<span begin="1.000" end="1.005">A</span>')))
    assert res["short_word_count"] == 1
    assert res["timestamp_repair_count"] == 0


# --------------------------------------------------------------------------- #
# 2. 非法词：三种来源都不得作为原生逐字交付
# --------------------------------------------------------------------------- #
def test_zero_and_negative_duration_words_dropped():
    """0ms 与负时长不再被钳成 40ms，而是剔除并标记行不完整。"""
    xml = _ttml(
        _p(
            '<span begin="1.000" end="1.000">A</span>'
            '<span begin="2.000" end="1.500">B</span>'
            '<span begin="3.000" end="3.500">C</span>'
        )
    )
    res = parse_lrc_red_ttml(xml)
    words = _all_words(res)
    assert [w["text"].strip() for w in words] == ["C"], words
    assert res["invalid_word_count"] == 2
    assert res["words_incomplete_lines"] == 1
    assert res["invalid_word_lines"] == 1
    assert res["lines"][0]["words_incomplete"] is True
    assert res["lines"][0]["has_invalid_span"] is True
    for w in words:
        assert w["duration"] == w["end"] - w["start"] > 0


def test_explicit_bad_end_not_treated_as_missing():
    """已有但无法解析的 end 不得被当成缺失去做推断。"""
    before = parse_lrc_red_ttml(_ttml(_p('<span begin="1.000" end="abc">A</span>')))
    assert before["invalid_word_count"] == 1
    assert before["inferred_end_count"] == 0, "非法 end 被伪装成缺失去推断了"

    missing = parse_lrc_red_ttml(_ttml(_p('<span begin="1.000">A</span>')))
    assert missing["inferred_end_count"] == 1
    assert missing["invalid_word_count"] == 0
    assert _all_words(missing)[0]["duration"] > 0


def test_missing_begin_word_is_invalid():
    """缺失 begin 的词不可交付，且必须计入 invalid，而不是静默消失。"""
    res = parse_lrc_red_ttml(
        _ttml(_p('<span end="2.000">A</span><span begin="2.000" end="3.000">B</span>'))
    )
    assert res["invalid_word_count"] == 1
    assert [w["text"].strip() for w in _all_words(res)] == ["B"]


# --------------------------------------------------------------------------- #
# 3. 5 字 1 坏 / 5 字全坏：原文保留，不被剩余词覆盖
# --------------------------------------------------------------------------- #
def test_partial_bad_word_keeps_original_text():
    """5 个词坏 1 个：行标不完整，raw_main_text 仍是完整的 5 个字。"""
    xml = _ttml(
        _p(
            '<span begin="1.0" end="1.2">一</span>'
            '<span begin="1.2" end="1.0">二</span>'
            '<span begin="1.4" end="1.6">三</span>'
            '<span begin="1.6" end="1.8">四</span>'
            '<span begin="1.8" end="2.0">五</span>'
        )
    )
    res = parse_lrc_red_ttml(xml)
    line = res["lines"][0]
    assert res["invalid_word_count"] == 1
    assert line["words_incomplete"] is True
    assert line["raw_main_text"] == "一二三四五", line["raw_main_text"]
    assert len(line["words"]) == 4
    assert res["invalid_word_lines"] == 1
    assert res["native_word_lines"] == 0


def test_all_bad_words_line_not_native():
    """5 个词全坏：行保留文本与异常标记，但不计入高质量原生逐字。"""
    xml = _ttml(
        _p(
            '<span begin="1.0" end="1.0">一</span>'
            '<span begin="1.2" end="1.0">二</span>'
            '<span begin="1.4" end="1.4">三</span>'
        )
    )
    res = parse_lrc_red_ttml(xml)
    line = res["lines"][0]
    assert "words" not in line, "全部为非法词时不应产出 words"
    assert line["raw_main_text"] == "一二三"
    assert line["has_invalid_span"] is True
    assert res["invalid_word_lines"] == 1
    assert res["native_word_lines"] == 0
    assert res["has_verbatim"] is False


# --------------------------------------------------------------------------- #
# 4. 全局 offset 后仍保持三元组恒等
# --------------------------------------------------------------------------- #
def test_offset_preserves_triple_identity():
    """正负 offset 下 duration 始终等于 end-start，且 start 不为负。"""
    xml = _ttml(_p('<span begin="1.000" end="1.500">A</span>'))
    for adjust in (-5000, -1000, 0, 1500):
        res = parse_lrc_red_ttml(xml, adjust=adjust)
        for w in _all_words(res):
            assert w["start"] >= 0, (adjust, w)
            assert w["duration"] == w["end"] - w["start"], (adjust, w)


# --------------------------------------------------------------------------- #
# 5. H10：主唱段账本与分母
# --------------------------------------------------------------------------- #
def test_main_p_ledger_identity():
    """raw == invalid + accepted，且非法主唱段拉低覆盖率而不是缩小分母。"""
    good = [_p('<span begin="%d.0" end="%d.5">词</span>' % (i, i), begin=f"{i}.0", end=f"{i}.5") for i in range(1, 21)]
    bad = [
        _p('<span begin="99.0" end="99.5">坏</span>', begin=None, end="99.5"),
        _p('<span begin="bad" end="99.5">坏</span>', begin="bad", end="99.5"),
        # 非主唱干扰段：不得进入分母
        '<p ttm:role="x-bg" begin="30.0" end="31.0">和声</p>',
        '<p begin="31.0" end="32.0">作词 : 某某</p>',
        '<p begin="32.0" end="33.0"></p>',
    ]
    res = parse_lrc_red_ttml(_ttml(*(good + bad)))
    assert res["raw_main_p_count"] == 22, res["raw_main_p_count"]
    assert res["invalid_main_p_count"] == 2
    assert res["accepted_main_p_count"] == 20
    assert res["missing_begin_count"] == 1
    assert res["invalid_begin_count"] == 1
    assert (
        res["raw_main_p_count"]
        == res["invalid_main_p_count"] + res["accepted_main_p_count"]
    )
    assert res["native_word_lines"] <= res["accepted_main_p_count"]
    assert res["singing_lines"] == 20
    assert res["word_coverage_ratio"] < 1.0, "非法主唱段未拉低覆盖率（分母被缩小了）"


def test_clean_sample_coverage_unchanged():
    """干净样本上 raw_main_p_count 与 singing_lines 相等，覆盖率不因 H10 改变。"""
    xml = _ttml(
        _p('<span begin="1.0" end="1.5">A</span>'),
        _p('<span begin="2.0" end="2.5">B</span>'),
    )
    res = parse_lrc_red_ttml(xml)
    assert res["raw_main_p_count"] == res["singing_lines"] == 2
    assert res["invalid_main_p_count"] == 0
    assert res["word_coverage_ratio"] == 1.0


def test_credit_translation_bg_excluded_from_denominator():
    """非主唱段、制作署名与空段都不得进入三计数。"""
    xml = _ttml(
        _p('<span begin="1.0" end="1.5">正文</span>'),
        '<p ttm:role="x-translation" begin="1.0" end="1.5">译文</p>',
        '<p ttm:role="x-bg" begin="1.6" end="2.0">和声</p>',
        '<p begin="2.1" end="2.5">作曲 : 某某</p>',
        '<p begin="2.6" end="3.0">   </p>',
    )
    res = parse_lrc_red_ttml(xml)
    assert res["raw_main_p_count"] == 1, res["raw_main_p_count"]


# --------------------------------------------------------------------------- #
# 6. lrc.red 门禁：残缺词候选整条拒绝
# --------------------------------------------------------------------------- #
def _candidate_with(ttml_text: str):
    payload = json.dumps(
        {"title": "T", "artist": "A", "duration": 30.0, "source": ttml_text}
    )

    def fake_http(url, **kwargs):
        return HttpResult(
            status=200, text=payload, bytes_len=len(payload), elapsed_ms=5, error=""
        )

    LrcRedFetcher.clear_cache()
    try:
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=fake_http):
            return LrcRedFetcher.fetch_by_isrc(
                "USUM71413645", duration_ms=30000, title="T", artist="A"
            )
    finally:
        LrcRedFetcher.clear_cache()


def test_gate_rejects_partial_bad_words():
    """含 1 个坏词的原生候选必须被整条拒绝，绝不交付丢词语料。"""
    ttml = _ttml(
        _p(
            '<span begin="1.0" end="1.2">一</span>'
            '<span begin="1.2" end="1.2">二</span>'
            '<span begin="1.4" end="1.6">三</span>'
        )
    )
    assert _candidate_with(ttml) is None


def test_gate_rejects_invalid_main_paragraph():
    """begin 非法的附加主唱段同样让整条候选被拒绝。"""
    ttml = _ttml(
        _p('<span begin="1.0" end="1.2">一</span>'),
        _p('<span begin="1.3" end="1.5">二</span>', begin=None),
    )
    assert _candidate_with(ttml) is None


def test_gate_accepts_clean_candidate():
    """干净候选仍应通过门禁（回归保护）。"""
    ttml = _ttml(
        _p(
            '<span begin="1.0" end="1.2">一</span>'
            '<span begin="1.2" end="1.4">二</span>'
        )
    )
    cand = _candidate_with(ttml)
    assert cand is not None
    assert cand["match"]["word_coverage_ratio"] == 1.0


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
