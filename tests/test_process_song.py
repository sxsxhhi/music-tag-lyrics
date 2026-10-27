# -*- coding: utf-8 -*-
"""scripts/process_song.process_single_song 冒烟测试（v1.5.1-E5）。

这个 300 行的主流程函数此前**零测试**——P0-1（CLI 落盘崩溃）、`--lang zh` 失效、
封面误写三个问题能同时存活，就是因为这条主干没有回归锁。

这里用 mock 切断网络与写盘副作用，只验证编排逻辑：给定候选歌词，能否
产出正确的 .lrc/.spl、内嵌是否按 lyrics_only 走不同分支、无候选时是否优雅退出。
"""

import os
import sys
import tempfile
import shutil
import unittest.mock as mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import scripts.process_song as ps


_TAGS = {
    "title": "测试曲",
    "artist": "测试人",
    "album": "测试专辑",
    "year": "2010",
    "duration": 240000,
    "lyricist": "词人甲",
    "composer": "曲人乙",
    "lyrics": "",
    "cover": None,
}


def _candidate():
    """一份最小可用的候选歌词（含逐字，走 to_spl 的 verbatim 分支）。"""
    lines = []
    for i, text in enumerate(("傍晚六点下班", "换掉药厂的衣裳")):
        st = 46000 + i * 5000
        words = [{"text": c, "start": st + k * 260, "end": st + k * 260 + 260, "duration": 260}
                 for k, c in enumerate(text)]
        lines.append({
            "start": st, "end": st + 3000, "duration": 3000,
            "text": text, "translation": "", "words": words,
        })
    return {"tags": {"ti": "测试曲", "ar": "测试人"}, "lines": lines}


def _tmp_dir():
    d = tempfile.mkdtemp(prefix="mtl_ps_")
    return d


def test_process_single_song_writes_lrc_spl():
    """主流程冒烟：有候选歌词时应产出 .lrc/.spl，且内嵌按 lyrics_only 分支写入。"""
    out = _tmp_dir()
    try:
        src = os.path.join(out, "测试曲 - 测试人.wav")
        open(src, "wb").write(b"RIFF" + b"\x00" * 100)  # 只需存在即可，标签读取被 mock

        with mock.patch.object(ps, "read_audio_tags", return_value=dict(_TAGS)), \
             mock.patch.object(ps.NetEaseFetcher, "lookup_year", return_value=None), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics",
                               return_value=_candidate()) as m_fetch, \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as m_upd, \
             mock.patch("core.utils.lang_detect.detect_language",
                        return_value={"lang": "zh", "score": 1.0}), \
             mock.patch("builtins.input", side_effect=AssertionError("不应弹交互")):
            ok = ps.process_single_song(
                src,
                update_tags=True,
                output_dir=out,
                lyrics_only=True,
                non_interactive=True,
                no_cover=True,
                lang="auto",
            )

        assert ok is True, "主流程应返回 True"
        assert m_fetch.called, "没有调用取词"
        lrc = os.path.join(out, "测试曲 - 测试人.lrc")
        spl = os.path.join(out, "测试曲 - 测试人.spl")
        assert os.path.isfile(lrc) and os.path.isfile(spl), "落盘产物缺失"
        assert os.path.getsize(lrc) > 0 and os.path.getsize(spl) > 0
        # 铁律 6：.lrc 与 .spl 必须逐字节一致
        assert open(lrc, "rb").read() == open(spl, "rb").read(), ".lrc 与 .spl 不一致"

        # lyrics_only 分支必须只传 lyrics 一个键（其余元数据不许动）
        assert m_upd.called
        kwargs = m_upd.call_args.kwargs
        assert set(kwargs) == {"lyrics"}, f"lyrics_only 分支应只写 lyrics，实为 {set(kwargs)}"
        # 产物是 Enhanced LRC 逐字形态（<00:46.42>傍<00:46.68>晚…），
        # 判正文必须先剥时间戳，否则子串永远匹配不到。
        from core.utils.time_utils import strip_timing_tags
        plain = strip_timing_tags(kwargs["lyrics"])
        assert "傍晚六点下班" in plain, plain[:200]
        assert "换掉药厂的衣裳" in plain, plain[:200]
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_process_single_song_no_lyrics_returns_false():
    """无候选歌词时应优雅返回 False，而不是抛异常或空转。"""
    out = _tmp_dir()
    try:
        src = os.path.join(out, "无歌词.mp3")
        open(src, "wb").write(b"ID3" + b"\x00" * 32)

        with mock.patch.object(ps, "read_audio_tags", return_value=dict(_TAGS)), \
             mock.patch.object(ps.NetEaseFetcher, "lookup_year", return_value=None), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=None), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as m_upd, \
             mock.patch("core.utils.lang_detect.detect_language",
                        return_value={"lang": "zh", "score": 1.0}):
            ok = ps.process_single_song(
                src, update_tags=False, output_dir=out, non_interactive=True, no_cover=True,
            )

        assert ok is False
        assert not m_upd.called, "无歌词时不应写标签"
        assert not [f for f in os.listdir(out) if f.endswith((".lrc", ".spl"))], "无歌词不应落盘"
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_process_single_song_missing_and_unsupported():
    """缺失文件与不支持的扩展名都必须返回 False 且不抛。"""
    assert ps.process_single_song("/no/such/file.wav") is False
    out = _tmp_dir()
    try:
        bad = os.path.join(out, "xx.txt")
        open(bad, "w", encoding="utf-8").write("x")
        assert ps.process_single_song(bad) is False
    finally:
        shutil.rmtree(out, ignore_errors=True)


def test_better_align_symmetric_default():
    """v1.5.2-D5: 择优缺字段时两侧同口径（旧实现 a 默认 1.0 / b 默认 0.0，位置决定结论）。"""
    # 两侧都缺字段 → 平手 → 保留首次 a
    a = {"lines": [{"text": "a"}]}
    b = {"lines": [{"text": "b"}]}
    assert ps._better_align(a, b) is a
    assert ps._better_align(b, a) is b  # 交换参数也保留「首次」，不再因默认值翻盘

    # 正常比较：高覆盖胜
    hi = {"acoustic_coverage": 0.9}
    lo = {"acoustic_coverage": 0.8}
    assert ps._better_align(hi, lo) is hi
    assert ps._better_align(lo, hi) is hi

    # 非数值覆盖按 0.0 处理，不抛异常
    bad = {"acoustic_coverage": None}
    assert ps._better_align(hi, bad) is hi
    assert ps._better_align(bad, hi) is hi


def test_numpy_reentry_preserves_engine_and_demotions():
    """v1.5.2 复审：gpu_aligned 重入时，whisper 行保持 gpu_whisper，门禁降级行不被复活。"""
    data = {
        "align_engine": "dual_whisper",
        "gpu_aligned": True,
        "lines": [
            {"start": 1000, "end": 3000, "duration": 2000, "text": "第一句有真逐字",
             "words": [{"text": "第", "start": 1000, "end": 1500},
                       {"text": "一", "start": 1500, "end": 2000},
                       {"text": "句", "start": 2000, "end": 2500},
                       {"text": "有", "start": 2500, "end": 2800},
                       {"text": "真", "start": 2800, "end": 2900},
                       {"text": "逐", "start": 2900, "end": 2950},
                       {"text": "字", "start": 2950, "end": 3000}]},
            {"start": 3000, "end": 5000, "duration": 2000, "text": "第二句缺逐字", "words": []},
            {"start": 5000, "end": 7000, "duration": 2000, "text": "第三句被门禁降级", "words": []},
        ],
        "demoted_line_indices": [2],
    }
    out = ps.apply_numpy_word_synth(data)
    lines = out["lines"]
    assert lines[0]["words_source"] == "gpu_whisper", lines[0]["words_source"]
    assert lines[1]["words_source"] == "numpy_synth", lines[1]["words_source"]
    assert lines[2]["words_source"] == "gate_demoted", lines[2]["words_source"]
    assert lines[2]["words"] == [], "门禁降级行不得被 numpy 复活"
    assert out["synthesized_words"] is False
    assert sum(out["words_source_stats"].values()) == len(lines)


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
