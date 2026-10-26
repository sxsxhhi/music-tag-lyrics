# -*- coding: utf-8 -*-
"""粤拼转换器 (Jyutping Converter) 单元测试。"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.jyutping import (
    to_jyutping,
    text_to_jyutping_tokens,
    get_token_disambiguation_ratio,
    is_cantonese_slang,
    CANTONESE_IMAGERY_WORDS,
)


def test_basic_characters():
    """测试常见单字在简繁体下的粤拼准确性。"""
    # 繁体
    t_tokens = text_to_jyutping_tokens("海闊天空")
    assert t_tokens == ["hoi", "fut", "tin", "hung"], f"Got: {t_tokens}"

    # 简体
    s_tokens = text_to_jyutping_tokens("海阔天空")
    assert s_tokens == ["hoi", "fut", "tin", "hung"], f"Got: {s_tokens}"

    # 常用字
    single_res = text_to_jyutping_tokens("我")
    assert single_res == ["ngo"], f"Got: {single_res}"


def test_polyphone_disambiguation():
    """测试多音字基于最长匹配词组消歧。"""
    # 1. 「重」: cung vs zung
    tokens_cung = text_to_jyutping_tokens("重新出发")
    assert tokens_cung[0] == "cung", f"Expected 'cung', got: {tokens_cung}"

    tokens_zung = text_to_jyutping_tokens("重要事情")
    assert tokens_zung[0] == "zung", f"Expected 'zung', got: {tokens_zung}"

    # 2. 「弹」: daan vs taan
    tokens_daan = text_to_jyutping_tokens("子弹飞")
    assert tokens_daan[1] == "daan", f"Expected 'daan', got: {tokens_daan}"

    tokens_taan = text_to_jyutping_tokens("弹琴")
    assert tokens_taan[0] == "taan", f"Expected 'taan', got: {tokens_taan}"

    # 3. 「行」: haang vs hong
    tokens_haang = text_to_jyutping_tokens("行街买嘢")
    assert tokens_haang[0] == "haang", f"Expected 'haang', got: {tokens_haang}"

    tokens_hong = text_to_jyutping_tokens("银行存钱")
    assert tokens_hong[1] == "hong", f"Expected 'hong', got: {tokens_hong}"


def test_arabic_digits():
    """测试阿拉伯数字转粤语音节。"""
    digits_tokens = text_to_jyutping_tokens("0123456789")
    expected = ["ling", "jat", "ji", "saam", "sei", "ng", "luk", "cat", "baat", "gau"]
    assert digits_tokens == expected, f"Got: {digits_tokens}"

    line_with_year = text_to_jyutping_tokens("1997年")
    assert line_with_year[:4] == ["jat", "gau", "gau", "cat"], f"Got: {line_with_year}"
    assert line_with_year[4] == "nin", f"Got: {line_with_year}"


def test_text_fidelity_a3():
    """v1.5.1-A3：token 拼接必须逐字等于原文（NFKC 只能用于查表）。

    旧版对整段做 NFKC 后当输出用，`…`→`...`（1 字变 3 字）、全角→半角，
    导致 converter 的一致性校验判「逐字 != 行文本」，**整行丢逐字**。
    """
    for text in (
        "…再见",            # U+2026 会被 NFKC 展开成三个点
        "ＡＢＣ",            # 全角字母
        "１２３",            # 全角数字
        "我 love you",       # 中英混排 + 空格
        "——分隔",           # 破折号
        "《标题》",          # 书名号
        "１２点\u3000收工",  # 全角空格
    ):
        toks = to_jyutping(text)
        joined = "".join(t["char"] for t in toks)
        assert joined == text, f"{text!r} -> {joined!r}（文本被改写）"

    # 全角数字仍应拿到数字读音，而不是 latin/unknown
    toks = to_jyutping("１２３")
    assert [t["type"] for t in toks] == ["digit", "digit", "digit"]
    assert [t["jyutping"] for t in toks] == ["jat", "ji", "saam"]

    # `…` 应作为单个标点 token，而不是三个点
    toks = to_jyutping("…再见")
    assert toks[0]["char"] == "…" and toks[0]["type"] == "punct"


def test_polyphone_simplified_a4():
    """v1.5.1-A4：简体字条首读音被词频带偏，需逐字校准。

    证据来自本仓词典：種→['zung'] 而 种→['cung','zung']；各種→['gok','zung']；
    著→['zoek','zyu'] 而 着→['ziu','zoek']；葉→['jip','sip'] 而 叶→['hip']。
    """
    cases = [
        ("台风", 0, "toi"),   # 台→['ji','toi']，应为 toi4
        ("睡着", 1, "zoek"),  # 着→['ziu','zoek']，应为 zoek6
        ("一种", 1, "zung"),  # 种→['cung','zung']，应为 zung2
        ("叶子", 0, "jip"),   # 叶→['hip']，字典里根本没有 jip，走覆盖表
        ("行走", 0, "hang"),  # 行→['haang','hang','hong']，书面语应 hang4
    ]
    for text, idx, expect in cases:
        toks = to_jyutping(text)
        got = toks[idx]["jyutping"]
        assert got == expect, f"{text}[{idx}] 得到 {got}，期望 {expect}"

    # 原有校准不得被覆盖
    for text, expect in (("会议", "wui"), ("说话", "syut"), ("还有", "waan")):
        assert to_jyutping(text)[0]["jyutping"] == expect, text


def test_mixed_latin_and_punctuation():
    """测试混合英文、数字、标点符号的结构保留。"""
    parsed = to_jyutping("Baby, hold住我！")
    # 标点和英文应被识别且不丢字
    types = [p["type"] for p in parsed]
    assert "latin" in types
    assert "punct" in types
    assert "hanzi" in types

    # 纯声学 target 应只包含含音节的 Hanzi/Digit
    syllables = text_to_jyutping_tokens("Baby, hold住我！")
    assert syllables == ["zyu", "ngo"], f"Got: {syllables}"


def test_famous_cantopop_lyrics():
    """测试经典粤语名曲唱词粤拼。"""
    # 《海阔天空》
    line1 = text_to_jyutping_tokens("今天我 寒夜里看雪飘过")
    assert line1 == ["gam", "tin", "ngo", "hon", "je", "leoi", "hon", "syut", "piu", "gwo"], f"Got: {line1}"

    # 《月半小夜曲》
    line2 = text_to_jyutping_tokens("月半小夜曲")
    assert line2 == ["jyut", "bun", "siu", "je", "kuk"], f"Got: {line2}"

    # 《一生何求》
    line3 = text_to_jyutping_tokens("一生何求")
    assert line3 == ["jat", "sang", "ho", "kau"], f"Got: {line3}"


def test_cantonese_slang_and_imagery():
    """测试口语字与书面意象词判定。"""
    assert is_cantonese_slang("呢个系乜嘢嚟嘅")
    assert not is_cantonese_slang("普通话歌词")

    assert "海阔天空" in CANTONESE_IMAGERY_WORDS
    assert "这一刹" in CANTONESE_IMAGERY_WORDS
    assert "一生何求" in CANTONESE_IMAGERY_WORDS


def test_disambiguation_ratio():
    """测试消歧率计算。"""
    # 词组匹配全消歧
    tokens1 = to_jyutping("重新出发，重要事情")
    ratio1 = get_token_disambiguation_ratio(tokens1)
    assert ratio1 >= 0.8, f"Got: {ratio1}"


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
