# -*- coding: utf-8 -*-
"""CLI 入口与主流程端到端离线测试：ttml2lyrics 落盘、stdout、process_song 退出码、krc_parser 等。

运行方式：
    python tests/test_cli.py
"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import io
import os
import sys
import tempfile
import shutil
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import scripts.ttml2lyrics as ttml2lyrics
import scripts.process_song as process_song
from core.parsers.krc_parser import parse_krc, extract_krc_language_translation
from core.parsers.ttml_parser import parse_ttml

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def test_ttml2lyrics_disk_write_p0_1():
    """P0-1 核心验证：默认落盘模式（非 stdout）atomic_write_many 必须成功写入 .lrc 和 .spl。"""
    ttml_content = _read_fixture("apple_ttml_meta.ttml")
    with tempfile.TemporaryDirectory(prefix="mtl_test_cli_") as tmpdir:
        input_path = os.path.join(tmpdir, "test_song.ttml")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(ttml_content)

        out_dir = os.path.join(tmpdir, "output")
        ret = ttml2lyrics.main([input_path, "--out-dir", out_dir])
        assert ret == 0, f"ttml2lyrics 返回非 0 状态码: {ret}"

        lrc_file = os.path.join(out_dir, "test_song.lrc")
        spl_file = os.path.join(out_dir, "test_song.spl")
        assert os.path.isfile(lrc_file), f"未生成 .lrc 文件: {lrc_file}"
        assert os.path.isfile(spl_file), f"未生成 .spl 文件: {spl_file}"

        lrc_text = open(lrc_file, encoding="utf-8").read()
        spl_text = open(spl_file, encoding="utf-8").read()
        assert len(lrc_text) > 0
        assert lrc_text == spl_text
        assert "大石碎胸口" in lrc_text


def test_ttml2lyrics_stdout():
    """ttml2lyrics --stdout 模式输出完整 Enhanced LRC。"""
    ttml_content = _read_fixture("apple_ttml_meta.ttml")
    with tempfile.TemporaryDirectory(prefix="mtl_test_stdout_") as tmpdir:
        input_path = os.path.join(tmpdir, "test_song.ttml")
        with open(input_path, "w", encoding="utf-8") as f:
            f.write(ttml_content)

        old_stdout = sys.stdout
        buf = io.StringIO()
        try:
            sys.stdout = buf
            ret = ttml2lyrics.main([input_path, "--stdout"])
        finally:
            sys.stdout = old_stdout

        assert ret == 0
        output = buf.getvalue()
        assert "大石碎胸口" in output
        assert "[00:28.700]" in output


def test_ttml2lyrics_nonexistent_input():
    """输入不存在的文件应返回退出码 2。"""
    ret = ttml2lyrics.main(["nonexistent_song_12345.ttml"])
    assert ret == 2


def test_process_song_missing_file():
    """process_song 面对不存在的目标路径返回 2。"""
    old_stdout = sys.stdout
    buf = io.StringIO()
    try:
        sys.stdout = buf
        old_argv = sys.argv
        sys.argv = ["process_song.py", "nonexistent_target_path_xyz.flac"]
        ret = process_song.main()
    finally:
        sys.stdout = old_stdout
        sys.argv = old_argv
    assert ret == 2


def test_p1_17_pure_translation_no_ghost_lines():
    """P1-17 验证：纯 x-translation 段落不得作为原文被提取或合成假字。"""
    sample_ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
        '<body><div>'
        '<p begin="00:10.000" end="00:12.000"><span begin="00:10.000" end="00:12.000">Original Song</span></p>'
        '<p begin="00:10.000" end="00:12.000"><span ttm:role="x-translation">原歌曲译文</span></p>'
        '<p begin="00:20.000" end="00:22.000"><span begin="00:20.000" end="00:22.000">Next Verse</span></p>'
        '</div></body></tt>'
    )
    parsed = parse_ttml(sample_ttml)
    # 纯译文段落不应成为独立的原文行
    lines = parsed["lines"]
    assert len(lines) == 2, f"期望 2 行，实际得到 {len(lines)} 行: {[l['text'] for l in lines]}"
    assert lines[0]["text"] == "Original Song"
    assert lines[0]["translation"] == "原歌曲译文"
    assert lines[1]["text"] == "Next Verse"


def test_krc_parser_basics():
    """P2-9 补充：验证 KRC 基础解析与语义译文提取。"""
    krc_sample = (
        "[ti:测试歌曲]\n"
        "[ar:测试歌手]\n"
        "[al:测试专辑]\n"
        "[offset:500]\n"
        "[1000,2000]<0,500,0>测<500,500,0>试<1000,1000,0>歌词\n"
        "[3500,2000]<0,1000,0>第<1000,1000,0>二句\n"
    )
    data = parse_krc(krc_sample)
    assert data["tags"]["ti"] == "测试歌曲"
    assert data["tags"]["ar"] == "测试歌手"
    assert len(data["lines"]) == 2
    # offset: 500ms
    assert data["lines"][0]["start"] == 1500
    assert len(data["lines"][0]["words"]) == 3
    assert data["lines"][0]["text"] == "测试歌词"


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
