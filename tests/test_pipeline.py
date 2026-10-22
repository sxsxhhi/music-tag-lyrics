# -*- coding: utf-8 -*-
"""
流水线级回归测试（离线：不联网、不碰用户音频）

锁住 v1.2.4 里三条最关键的修复，防止后续重构静默回退：
1. P0-2 双语译文行必须先被剥离、再合成逐字（顺序颠倒会让译文被当歌词唱出来）
2. P0-3 校验失败（有效行为 0）必须返回 is_valid=False，调用方据此拒绝写盘
3. P0-4 ID3 解析失败时绝不覆盖原文件

外加去重批次的护栏：时间戳剥离统一实现、TagSet 空值归一、批量原子写。

运行：python tests/test_pipeline.py
"""

import hashlib
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import scripts.tag_editor as te
from core.converter import to_spl
from core.utils.io_safe import atomic_write_many
from core.utils.time_utils import strip_timing_tags
from scripts.lyric_validator import is_credit_line, validate_and_fix_lyrics
from scripts.tag_editor import TagSet, read_audio_tags, update_audio_tags
from mutagen.id3 import ID3, USLT


def _bilingual_candidate():
    """日文原曲 + 中文译文的行级候选（无逐字），模拟 LRCLIB 返回形态。"""
    return {
        "source": "lrclib",
        "lyric_type": "line",
        "tags": {"ti": "测试曲", "ar": "测试人"},
        "lines": [
            {"start": 1000, "end": 4000, "duration": 3000, "text": "僕が死のうと思ったのは", "words": []},
            {"start": 4000, "end": 7000, "duration": 3000, "text": "我曾经想过一了百了", "words": []},
            {"start": 8000, "end": 11000, "duration": 3000, "text": "海猫の鳴き声", "words": []},
            {"start": 11000, "end": 14000, "duration": 3000, "text": "海猫的啼哭声", "words": []},
        ],
    }


def test_bilingual_lines_paired_not_sung():
    """P0-2：4 行双语 → 2 行正文 + 2 条 translation，译文不再留在 text 里。"""
    ok, data, _ = validate_and_fix_lyrics(_bilingual_candidate(), audio_duration_ms=200000, is_non_zh=True)
    lines = data["lines"]
    assert ok is True, ok
    assert len(lines) == 2, [l["text"] for l in lines]
    assert [l["text"] for l in lines] == ["僕が死のうと思ったのは", "海猫の鳴き声"]
    assert [l["translation"] for l in lines] == ["我曾经想过一了百了", "海猫的啼哭声"]
    assert all("我曾经" not in l["text"] for l in lines)


def test_translation_emitted_as_subtitle_line():
    """译文必须以同一起始时间戳的副行出现（ELRC 双语），不能被拼进正文。"""
    _, data, _ = validate_and_fix_lyrics(_bilingual_candidate(), audio_duration_ms=200000, is_non_zh=True)
    out = to_spl(data, title="测试曲", artist="测试人")
    assert "[00:01.00]我曾经想过一了百了" in out, out[:300]
    # 正文行必须是逐字行（合成前没有 words 时这里是行级，由 process_song 负责合成）
    assert "[00:01.00]僕が死のうと思ったのは" in out


def test_empty_lyrics_is_invalid():
    """P0-3：空歌词/被剥空必须返回 False，调用方据此拒绝写盘。"""
    ok, data, warns = validate_and_fix_lyrics({"lines": []}, audio_duration_ms=200000, is_non_zh=False)
    assert ok is False, ok
    assert data["lines"] == []
    assert warns


def test_translation_only_track_is_invalid():
    """整首都是译文行（无原唱行）时，剥离后应为 0 行 → 判失败，而不是产出标题行文件。"""
    data = {
        "source": "x", "lyric_type": "line", "tags": {},
        "lines": [
            {"start": 1000, "end": 4000, "duration": 3000, "text": "第一句译文", "words": []},
            {"start": 5000, "end": 8000, "duration": 3000, "text": "第二句译文", "words": []},
        ],
    }
    ok, out, _ = validate_and_fix_lyrics(data, audio_duration_ms=200000, is_non_zh=True)
    assert ok is False, (ok, out["lines"])


def test_broken_id3_never_overwrites():
    """P0-4：ID3 解析失败时必须放弃写入，原文件字节不变。"""
    work = tempfile.mkdtemp(prefix="mtl_pipe_")
    try:
        p = os.path.join(work, "tagged.mp3")
        tag = ID3()
        tag.add(USLT(encoding=3, lang="eng", desc="", text="原有歌词"))
        tag.save(p)

        sha_before = hashlib.sha256(open(p, "rb").read()).hexdigest()

        real_id3 = te.ID3

        def boom(*a, **k):
            raise RuntimeError("simulated ID3 parse failure")

        te.ID3 = boom
        try:
            res = update_audio_tags(p, title="不该写进去")
        finally:
            te.ID3 = real_id3

        sha_after = hashlib.sha256(open(p, "rb").read()).hexdigest()
        assert res is False, res
        assert sha_before == sha_after, "原文件被改动了"
        # 直接读 ID3：tag-only 的 mp3 没有音频帧，mutagen.File() 认不出来，
        # 但 ID3 块本身仍应完好无损。
        assert str(ID3(p)["USLT::eng"].text) == "原有歌词"
        assert [f for f in os.listdir(work) if f.startswith(".tmp")] == []
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_id3_write_keeps_other_language_frames():
    """P0-4/D-02：只覆盖 eng+空 desc 那一帧，chi 帧必须保留。"""
    work = tempfile.mkdtemp(prefix="mtl_pipe_")
    try:
        p = os.path.join(work, "tagged.mp3")
        tag = ID3()
        tag.add(USLT(encoding=3, lang="eng", desc="", text="OLD"))
        tag.add(USLT(encoding=3, lang="chi", desc="", text="中文帧"))
        tag.save(p)

        assert update_audio_tags(p, lyrics="<00:01.00>新歌词") is True
        t = ID3(p)
        frames = {k: str(t[k].text) for k in t.keys() if k.startswith("USLT")}
        assert frames["USLT::chi"] == "中文帧", frames
        assert frames["USLT::eng"] == "<00:01.00>新歌词", frames
    finally:
        shutil.rmtree(work, ignore_errors=True)


def test_tagset_blank_values_do_not_overwrite():
    """D-04/R5：空串/纯空白归一为 None；albumartist 空串保留删除语义。"""
    t = TagSet(title="  ", artist="", album="  专辑 ", year="unknown", albumartist="")
    n = t.normalized()
    assert n.title is None and n.artist is None, (n.title, n.artist)
    assert n.album == "专辑"
    assert n.year is None, n.year            # "unknown" 解析不出年份
    assert n.albumartist == ""               # 显式删除语义不能丢


def test_strip_timing_tags_three_digit_minutes():
    """P0-2 统一实现：三位分钟也要剥掉（旧内联正则固定两位，长音频会漏）。"""
    assert strip_timing_tags("[123:45.67]词：某人") == "词：某人"
    assert strip_timing_tags("<00:01.50>你好<00:02.00>") == "你好"
    assert is_credit_line("[123:45.67]词：某人") is True
    assert is_credit_line("[00:01.00]词不达意") is False


def test_atomic_write_many_keeps_pair():
    """P1-9：双文件一次性原子写，失败前不产生半对产物，且不留临时文件。"""
    work = tempfile.mkdtemp(prefix="mtl_pipe_")
    try:
        a = os.path.join(work, "x.lrc")
        b = os.path.join(work, "x.spl")
        atomic_write_many({a: "LRC", b: "SPL"})
        assert open(a, encoding="utf-8").read() == "LRC"
        assert open(b, encoding="utf-8").read() == "SPL"
        atomic_write_many({a: "LRC2", b: "SPL2"})
        assert open(a, encoding="utf-8").read() == "LRC2"
        assert open(b, encoding="utf-8").read() == "SPL2"
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
        except Exception as e:  # noqa: BLE001 - 测试报错也要逐条显示，不能中断整轮
            failed.append(name)
            print(f"  ERROR {name}  -> {type(e).__name__}: {e}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
