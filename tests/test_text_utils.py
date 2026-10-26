# -*- coding: utf-8 -*-
"""文本工具与酷狗 KRC 解密单元测试（v1.5.1-E4）。

这两个模块此前**零直接测试**：
  · core/utils/text_utils.py —— 被 acoustic_aligner / acoustic_canto / aggregator /
    apple_ttml / kugou / lrcshare 六处 import，一个判断写反会同时污染六条链路；
  · core/decryptors/krc_xor.py —— 酷狗逐字解密的唯一入口，错了就是整源不可用。
"""

import os
import sys
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.text_utils import (
    has_hanzi,
    has_kana,
    has_hangul,
    is_foreign_track,
    is_translation_line,
    is_source_or_credit_line,
    is_instrumental_placeholder,
)


def test_script_detection():
    """has_hanzi / has_kana / has_hangul 必须互不串台。"""
    assert has_hanzi("海阔天空")
    assert not has_hanzi("hello world")
    assert not has_hanzi("1234")

    assert has_kana("あいうえお")
    assert has_kana("カタカナ")
    assert not has_kana("海阔天空")

    assert has_hangul("안녕하세요")
    assert not has_hangul("海阔天空")

    # 日文汉字 + 假名：两个判定应同时为真（不能因为假名就否定汉字）
    mixed = "東京の夜"
    assert has_hanzi(mixed) and has_kana(mixed)
    # CJK 扩展 B~I 生僻字必须被正确识别为汉字
    assert has_hanzi("𠮷")
    assert has_hanzi("𪚥")


def test_is_foreign_track():
    """外语判定：中文曲名不算外语，纯外语或无汉字才算。"""
    assert not is_foreign_track("海阔天空", "Beyond")
    assert is_foreign_track("Shape of You", "Ed Sheeran")
    assert is_foreign_track("夜に駆ける", "YOASOBI")  # 日文：含汉字但假名占主导
    assert not is_foreign_track("江南", "")
    assert not is_foreign_track("𠮷野家", "")  # 生僻字中文歌不误判为外语曲


def test_is_translation_line():
    """译文行判定：仅「含汉字 + 无假名 + 无逐字」才算（中文译文行）。"""
    # 纯英文原文不是译文行（无汉字）
    assert not is_translation_line("I love you so much", has_words=False)
    # 含汉字、无假名、无逐字 → 典型的中文译文行
    assert is_translation_line("我爱你", has_words=False)
    # 日文假名行不算（那是原文）
    assert not is_translation_line("東京の夜", has_words=False)
    # 有逐字的行永远不是译文行
    assert not is_translation_line("我爱你", has_words=True)
    assert not is_translation_line("", has_words=False)


def test_is_source_or_credit_line():
    """来源/署名行必须被识别，正文不能被误杀。

    v1.5.1: 旧实现只认「LRC: / URL / 歌词来源」，不认「作词 : 某某」这类署名行，
    与函数名不符——acoustic_aligner 声称「署名行不参与合成」，实际照样合成。
    """
    for line in (
        "作词：姬赓",
        "作曲 : 董亚千",
        "编曲：某某",
        "Lyrics by Someone",
        "词：小寒 曲：林俊杰",
        "歌词来源：酷狗音乐",
        "https://example.com/lrc",
        "LRC: someone",
        "",           # 空行按非正文处理
    ):
        assert is_source_or_credit_line(line), line
    for line in ("傍晚六点下班", "换掉药厂的衣裳", "鼓楼", "键盘上的泪", "作词人", "曲终人散"):
        assert not is_source_or_credit_line(line), line


def test_is_instrumental_placeholder():
    """纯音乐占位：短行 + 关键词 + 与曲长严重不符才判占位。"""
    target = 240_000
    assert is_instrumental_placeholder(
        [{"text": "纯音乐", "start": 0, "end": 3000}], target
    )
    assert is_instrumental_placeholder(
        [{"text": "Instrumental", "start": 0, "end": 3000}], target
    )
    # 正常歌词不得被误判
    normal = [
        {"text": f"第{i}句正常歌词内容", "start": i * 8000, "end": i * 8000 + 6000}
        for i in range(12)
    ]
    assert not is_instrumental_placeholder(normal, target)
    # 曲长未知（0）时不得凭关键词硬判
    assert not is_instrumental_placeholder([{"text": "纯音乐", "start": 0, "end": 3000}], 0)


def test_krc_xor_roundtrip():
    """KRC 解密：krc1 头 + XOR + zlib 往返必须还原原文。"""
    from core.decryptors.krc_xor import decrypt_krc, KRC_XOR_KEY

    plain = "[ti:测试歌曲]\n[00:01,000]<0,500,0>测<500,500,0>试\n"
    payload = zlib.compress(plain.encode("utf-8"))
    enc = bytes(b ^ KRC_XOR_KEY[i % 16] for i, b in enumerate(payload))

    assert decrypt_krc(b"krc1" + enc) == plain     # 带 magic
    assert decrypt_krc(enc) == plain               # 不带 magic


def test_krc_xor_empty_and_invalid():
    """空输入返回空串；损坏数据必须抛 ValueError 而不是静默返回垃圾。"""
    from core.decryptors.krc_xor import decrypt_krc

    assert decrypt_krc(b"") == ""
    assert decrypt_krc(b"krc1") == ""
    try:
        decrypt_krc(b"krc1" + b"\x00" * 64)  # XOR 后不是合法 zlib
    except ValueError:
        pass
    else:
        raise AssertionError("损坏的 KRC 数据应抛 ValueError")


def test_split_orig_tokens_cjk_ext_b_to_i():
    """CJK 扩展 B~I 生僻汉字分词：绝不与相邻汉字或拉丁字母粘连。"""
    from core.acoustic_aligner import split_orig_tokens, _is_cjk

    # 1. 验证 _is_cjk 覆盖扩展 B~I、兼容字等典型生僻字，且对空串/长串健壮防崩溃
    assert _is_cjk("𠮷")  # U+20BB7 (CJK Ext B)
    assert _is_cjk("𪚥")  # U+2A6A5 (CJK Ext B)
    assert _is_cjk("𫠠")  # U+2B820 (CJK Ext E)
    assert _is_cjk("﨎")  # U+FA0E (CJK Compatibility)
    assert not _is_cjk("")  # 防空串崩溃
    assert not _is_cjk("𠮷野家")  # 防多字符传参崩溃

    # 2. 验证分词：生僻汉字「𠮷野家」按单字切分
    m1, o1 = split_orig_tokens("𠮷野家")
    assert m1 == ["𠮷", "野", "家"], m1
    assert o1 == ["𠮷", "野", "家"], o1

    # 3. 连续生僻字「𠮷𠮷」绝不粘连
    m2, o2 = split_orig_tokens("𠮷𠮷")
    assert m2 == ["𠮷", "𠮷"], m2
    assert o2 == ["𠮷", "𠮷"], o2

    # 4. 生僻字与英文相邻「𠮷ABC」切为 ['𠮷', 'ABC']
    m3, o3 = split_orig_tokens("𠮷ABC")
    assert m3 == ["𠮷", "ABC"], m3
    assert o3 == ["𠮷", "ABC"], o3

    # 5. 常见歌词符号（省略号 …、分数 ½、商标 ™、合字 ﬃ）防 NFKC 展开 TypeError 崩溃
    m4, o4 = split_orig_tokens("Let it go…")
    assert m4 == ["Let", "it", "go", "..."], m4
    assert "".join(o4) == "Let it go…"
    assert len(m4) == len(o4)

    m5, o5 = split_orig_tokens("1½ cups")
    assert "".join(o5) == "1½ cups"

    m6, o6 = split_orig_tokens("")
    assert m6 == [] and o6 == []


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
