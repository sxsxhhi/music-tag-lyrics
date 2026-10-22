# -*- coding: utf-8 -*-
"""
M1 止血层回归测试（离线，不联网、不碰用户音频）

覆盖：年份归一、空值归一、乐队白名单、来源行过滤、原子写工具。
运行：python tests/test_m1_safety.py
"""

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
)
from core.fetchers.lrcshare import is_source_or_credit_line
from core.utils.io_safe import atomic_write_text


def test_year_normalisation():
    assert clean_year_string("2016-08-15") == "2016"
    assert clean_year_string("2016/10/01 00:00:00") == "2016"
    # X-12: 解析不出年份时必须返回空串，不能返回前 4 个字符
    assert clean_year_string("unknown") == ""
    assert clean_year_string("现场版") == ""


def test_empty_value_normalisation():
    # D-04: 空串/纯空白视为"未提供"，避免把原标签清空
    assert _txt("") is None
    assert _txt("   ") is None
    assert _txt("  x ") == "x"
    assert _yr("unknown") is None
    assert _yr("2005") == "2005"


def test_band_whitelist():
    # X-13: 带版本后缀的乐队名也要保护
    assert normalize_artist_string("Earth, Wind & Fire (Live)") == "Earth, Wind & Fire"
    assert normalize_artist_string("伍佰 & China Blue") == "伍佰 & China Blue"
    # 不能被白名单误伤：不是 "Beyond" 这一支乐队
    assert normalize_artist_string("Beyond the Black") == "Beyond the Black"
    # 普通多歌手仍然拆成 '/'
    assert normalize_artist_string("徐良 & 小凌") == "徐良/小凌"


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


def main():
    cases = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}  -> {e}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
