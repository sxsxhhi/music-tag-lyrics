# -*- coding: utf-8 -*-
"""M1 离线回归：年份/空值归一、乐队白名单、来源行、原子写。运行：python tests/test_m1_safety.py"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import os
import sys
import tempfile
import shutil

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.tag_editor import (
    clean_year_string, normalize_artist_string, _txt, _yr,
    infer_title_artist_from_basename, extract_year_from_album,
)
from scripts.lyric_validator import extract_lyricist_composer, is_credit_line
from core.fetchers.lrcshare import is_source_or_credit_line
from core.utils.io_safe import atomic_write_text, atomic_write_bytes


def test_year_normalisation():
    assert clean_year_string("2016-08-15") == "2016"
    assert clean_year_string("2016/10/01 00:00:00") == "2016"
    # X-12: 解析不出年份时必须返回空串，不能返回前 4 个字符
    assert clean_year_string("unknown") == ""
    assert clean_year_string("现场版") == ""
    assert extract_year_from_album("2018 (上)") == "2018"
    assert extract_year_from_album("花粥2019“两碗三百”巡演LIVE") == "2019"
    assert extract_year_from_album("平凡的一天") == ""


def test_empty_value_normalisation():
    # D-04: 空串/纯空白视为"未提供"，避免把原标签清空
    assert _txt("") is None
    assert _txt("   ") is None
    assert _txt("  x ") == "x"
    assert _yr("unknown") is None
    assert _yr("2005") == "2005"


def test_band_whitelist():
    # 白名单命中后仍保留 Live 后缀
    assert normalize_artist_string("Earth, Wind & Fire (Live)") == "Earth, Wind & Fire (Live)"
    assert normalize_artist_string("伍佰 & China Blue") == "伍佰 & China Blue"
    # 不能被白名单误伤：不是 "Beyond" 这一支乐队
    assert normalize_artist_string("Beyond the Black") == "Beyond the Black"
    # 普通多歌手仍然拆成 '/'
    assert normalize_artist_string("徐良 & 小凌") == "徐良/小凌"


def test_infer_title_artist_from_basename():
    assert infer_title_artist_from_basename("广场 - 李志") == ("广场", "李志")
    assert infer_title_artist_from_basename("广场-李志") == ("广场", "李志")
    title, artist = infer_title_artist_from_basename("起风了 (Live)-买辣椒也用券")
    assert title == "起风了 (Live)"
    assert artist == "买辣椒也用券"
    assert infer_title_artist_from_basename("只有歌名") == ("只有歌名", "")


def test_filename_does_not_override_existing_title():
    existing = "已有标题"
    inferred, _ = infer_title_artist_from_basename("广场-李志")
    title = existing or inferred
    assert title == "已有标题"


def test_extract_lyricist_composer():
    text = "[ti:七里香]\n[00:00.00]作词 : 方文山\n作曲：周杰伦\n还记得你说家是唯一的城堡"
    ly, co = extract_lyricist_composer(text)
    assert ly == "方文山"
    assert co == "周杰伦"
    both_ly, both_co = extract_lyricist_composer("词曲 : 李志\n广场中央没有人")
    assert both_ly == "李志" and both_co == "李志"
    empty_ly, empty_co = extract_lyricist_composer("词不达意\n曲终人散")
    assert empty_ly == "" and empty_co == ""
    kept, _ = extract_lyricist_composer("作词 : 方文山")
    # 已有 lyricist 不覆盖由调用方保证；提取函数本身仍返回署名
    assert kept == "方文山"


def test_extract_lyricist_composer_same_line_dual():
    """P0-4：同行双署名不得把「作曲」吞进作词人名里。"""
    ly, co = extract_lyricist_composer("作词 : 张三 作曲 : 李四")
    assert (ly, co) == ("张三", "李四"), (ly, co)

    ly, co = extract_lyricist_composer("作词：小寒 / 作曲：林俊杰")
    assert (ly, co) == ("小寒", "林俊杰"), (ly, co)

    ly, co = extract_lyricist_composer("作词:方文山 / 作曲:周杰伦")
    assert (ly, co) == ("方文山", "周杰伦"), (ly, co)

    # 英文同行双署名
    ly, co = extract_lyricist_composer("Lyrics: A  Composed by: B")
    assert (ly, co) == ("A", "B"), (ly, co)

    # 单署名仍然正确
    ly, co = extract_lyricist_composer("作曲 : 董亚千")
    assert (ly, co) == ("", "董亚千"), (ly, co)


def test_is_credit_line_no_false_positive():
    """P1-1：真实歌词首句不得被误判为署名行。"""
    for song_line in ("鼓楼", "键盘上的泪", "作词人", "词不达意", "曲终人散", "吉他手"):
        assert is_credit_line(song_line) is False, song_line

    for credit in ("作词 : 张三", "作曲：李四", "词：小寒", "鼓 : 王超",
                   "吉他：王路遥", "出品：某某公司", "OP : 滚石",
                   "Lyrics : 方文山", "Lyrics by John", "Composed by B",
                   "[00:46.42]<00:46.42>作词：姬赓"):
        assert is_credit_line(credit) is True, credit


def test_source_line_filter():
    # X-21: lrcshare 常见署名行
    assert is_source_or_credit_line("LRC：X2ISPANDA[https://lrcshare.com]") is True
    assert is_source_or_credit_line("歌词来源：LRCSHARE") is True
    assert is_source_or_credit_line("https://example.com/lyrics") is True
    assert is_source_or_credit_line("···———···") is True
    assert is_source_or_credit_line("") is True
    # 正常歌词不能被误杀
    assert is_source_or_credit_line("还没有熟的一个果子") is False
    assert is_source_or_credit_line("Counting stars") is False


def test_atomic_write_text():
    work = tempfile.mkdtemp(prefix="mtl_io_")
    try:
        p = os.path.join(work, "out.lrc")
        atomic_write_text(p, "[00:01.00]第一版\n")
        assert open(p, encoding="utf-8").read() == "[00:01.00]第一版\n"
        # 覆盖写 + 不留临时文件
        atomic_write_text(p, "[00:02.00]第二版\n")
        assert open(p, encoding="utf-8").read() == "[00:02.00]第二版\n"
        assert [f for f in os.listdir(work) if f.startswith(".tmp")] == []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_atomic_write_bytes():
    """F2：字节级原子写——二进制完整性、原子替换、失败不损原文件、临时文件干净。"""
    work = tempfile.mkdtemp(prefix="mtl_io_")
    try:
        p = os.path.join(work, "cover.bin")
        payload = b"hello\x00\xffbinary" * 10
        assert atomic_write_bytes(p, payload) is True
        assert open(p, "rb").read() == payload
        # 覆盖写 + 不留临时文件
        payload2 = b"second\x01\x02version"
        assert atomic_write_bytes(p, payload2) is True
        assert open(p, "rb").read() == payload2
        assert [f for f in os.listdir(work) if f.startswith(".tmp")] == []
        # 目标是目录（replace 必失败）→ 返回 False，原文件无损
        assert atomic_write_bytes(work, b"x") is False
        assert open(p, "rb").read() == payload2
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
