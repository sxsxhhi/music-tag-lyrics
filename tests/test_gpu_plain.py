# -*- coding: utf-8 -*-
"""GPU 精度优先与纯文本搜集离线回归。运行：python tests/test_gpu_plain.py"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import builtins

from core.plain_lyric_search import (
    fetch_plain_lyric_text,
    github_blob_to_raw,
    looks_like_lyrics,
    lrc_or_plain_to_text,
)
import core.plain_lyric_search as pls
import core.acoustic_aligner as aa
import scripts.process_song as ps


def _line_only_track():
    return {
        "source": "lrclib",
        "lyric_type": "line",
        "lines": [
            {"start": 1000, "end": 4000, "duration": 3000, "text": "第一行歌词啊啊", "translation": "trans1", "words": []},
            {"start": 4000, "end": 7000, "duration": 3000, "text": "第二行歌词啊啊", "translation": "trans2", "words": []},
        ],
    }


def _word_track():
    return {
        "source": "kugou",
        "lines": [
            {
                "start": 0, "text": "焚情如火",
                "words": [
                    {"start": 0, "end": 400, "text": "焚情", "duration": 400},
                    {"start": 400, "end": 800, "text": "如火", "duration": 400},
                ],
            }
        ],
    }


def test_lrc_or_plain_strips_meta_and_times():
    raw = "[ti:广场]\n[ar:李志]\n[00:12.00]夜色覆盖了城市\n广场中央没有人"
    text = lrc_or_plain_to_text(raw)
    assert "ti:" not in text
    assert "[00:" not in text
    assert "夜色覆盖了城市" in text
    assert "广场中央没有人" in text


def test_looks_like_lyrics_rejects_short():
    assert looks_like_lyrics("a\nb\nc") is False
    ok = "广场\n夜色覆盖了城市的灯火\n我走在无人的街道\n把沉默交给晚风\n把回忆交给细雨\n把名字轻轻交给你"
    assert looks_like_lyrics(ok, "广场") is True


def test_github_blob_to_raw():
    blob = "https://github.com/lizhi/lyrics/blob/main/广场.txt"
    assert github_blob_to_raw(blob) == "https://raw.githubusercontent.com/lizhi/lyrics/main/广场.txt"
    raw = "https://raw.githubusercontent.com/lizhi/lyrics/main/广场.txt"
    assert github_blob_to_raw(raw) == raw
    assert github_blob_to_raw("https://example.com/x") is None


def test_web_search_prefers_github_raw():
    lyrics = (
        "广场\n夜色覆盖了城市的灯\n我走在无人的街\n把沉默交给风\n"
        "把回忆交给雨\n把名字交给你"
    )
    hits = []

    def fake_get(url, **kwargs):
        hits.append(url)
        if "bing.com" in url or "duckduckgo" in url or "github.com/search" in url:
            return '<a href="https://github.com/lizhi/lyrics/blob/main/广场.txt">x</a>'
        if "raw.githubusercontent.com/lizhi/lyrics/main/" in url:
            return lyrics
        return None

    orig_get, orig_five = pls.http_get_text, pls._from_five_sources
    pls.http_get_text = fake_get
    pls._from_five_sources = lambda *a, **k: None
    try:
        text = fetch_plain_lyric_text("广场", "李志")
        assert text and "夜色覆盖了城市的灯" in text
        assert any("raw.githubusercontent.com" in u for u in hits)
    finally:
        pls.http_get_text = orig_get
        pls._from_five_sources = orig_five


def test_line_level_refuse_falls_back_numpy():
    orig_input = builtins.input
    builtins.input = lambda *a, **k: "n"
    try:
        out = ps.enrich_missing_words(
            _line_only_track(), filepath="x.flac", title="Hello", artist="World",
            non_interactive=False,
        )
    finally:
        builtins.input = orig_input
    assert out.get("synthesized_words") is True
    assert len(out["lines"][0].get("words") or []) >= 2
    assert out["lines"][0].get("translation") == "trans1"


def test_line_level_noninteractive_uses_numpy():
    orig_input = builtins.input

    def boom(*a, **k):
        raise AssertionError("non-interactive must not call input()")

    builtins.input = boom
    try:
        out = ps.enrich_missing_words(
            _line_only_track(), filepath="x.flac", title="Hello", artist="World",
            non_interactive=True,
        )
    finally:
        builtins.input = orig_input
    assert out.get("synthesized_words") is True
    assert len(out["lines"][0].get("words") or []) >= 2


def test_partial_verbatim_track_fills_remaining_lines():
    """P0-2：部分行有逐字时，其余无词行必须补齐，不得整首跳过。

    旧条件是 any(len(words) >= 2)：只要一行凑够 2 个词，整首剩余无词的
    正文行都被跳过，产出「逐字行 + 纯文本行」混排。
    """
    data = {
        "source": "lrcshare",
        "lyric_type": "word",
        "lines": [
            {"start": 1000, "end": 3000, "duration": 2000, "text": "第一句有逐字", "translation": "",
             "words": [
                 {"start": 1000, "end": 2000, "text": "第一", "duration": 1000},
                 {"start": 2000, "end": 3000, "text": "句有逐字", "duration": 1000},
             ]},
            {"start": 3000, "end": 5000, "duration": 2000, "text": "第二句没有逐字", "translation": "", "words": []},
            {"start": 5000, "end": 7000, "duration": 2000, "text": "第三句也没有", "translation": "", "words": []},
        ],
    }
    out = ps.enrich_missing_words(
        data, filepath="x.flac", title="T", artist="A", non_interactive=True
    )
    empty = [l.get("text") for l in out["lines"] if (l.get("text") or "").strip() and not l.get("words")]
    assert not empty, f"仍有未补齐的正文行：{empty}"


def test_all_verbatim_track_skips_enrichment():
    """全部正文行都已有逐字时，必须原样返回，不得再触发合成。"""
    data = {
        "source": "kugou",
        "lyric_type": "word",
        "lines": [
            {"start": 1000, "end": 3000, "duration": 2000, "text": "甲句有字", "translation": "",
             "words": [
                 {"start": 1000, "end": 2000, "text": "甲句", "duration": 1000},
                 {"start": 2000, "end": 3000, "text": "有字", "duration": 1000},
             ]},
            {"start": 3000, "end": 5000, "duration": 2000, "text": "乙句有字", "translation": "",
             "words": [
                 {"start": 3000, "end": 4000, "text": "乙句", "duration": 1000},
                 {"start": 4000, "end": 5000, "text": "有字", "duration": 1000},
             ]},
        ],
    }
    out = ps.enrich_missing_words(
        data, filepath="x.flac", title="T", artist="A", non_interactive=True
    )
    assert not out.get("synthesized_words"), "全部逐字时不应触发 numpy 合成"
    assert out["lines"][0]["words"][0]["text"] == "甲句"


def test_credit_line_does_not_block_enrichment():
    """署名行没有 words 不应拖累正文行的补齐判断。"""
    data = {
        "source": "lrcshare",
        "lyric_type": "word",
        "lines": [
            {"start": 0, "end": 2000, "duration": 2000, "text": "作词 : 方文山", "translation": "", "words": []},
            {"start": 3000, "end": 5000, "duration": 2000, "text": "正文一句", "translation": "", "words": []},
        ],
    }
    out = ps.enrich_missing_words(
        data, filepath="x.flac", title="T", artist="A", non_interactive=True
    )
    body = [l for l in out["lines"] if (l.get("text") or "").strip() and "作词" not in (l.get("text") or "")]
    assert body and all(l.get("words") for l in body), "正文行未被补齐"


def test_line_level_yes_passes_hints_and_keeps_translation():
    captured = {}

    def fake_align(cls, audio_path, lyrics_text, line_timing_hints=None, device="cuda", scratch_dir=None):
        captured["hints"] = line_timing_hints
        captured["text"] = lyrics_text
        return {
            "lines": [
                {
                    "start": 1000, "text": "第一行歌词啊啊",
                    "words": [
                        {"start": 1000, "end": 2000, "text": "第一行", "duration": 1000},
                        {"start": 2000, "end": 3000, "text": "歌词啊啊", "duration": 1000},
                    ],
                },
                {
                    "start": 4000, "text": "第二行歌词啊啊",
                    "words": [
                        {"start": 4000, "end": 5000, "text": "第二行", "duration": 1000},
                        {"start": 5000, "end": 6000, "text": "歌词啊啊", "duration": 1000},
                    ],
                },
            ]
        }

    orig_align = aa.AcousticAligner.align_lyrics
    orig_input = builtins.input
    aa.AcousticAligner.align_lyrics = classmethod(fake_align)
    builtins.input = lambda *a, **k: "y"
    try:
        out = ps.enrich_missing_words(
            _line_only_track(), filepath="x.flac", title="Hello", artist="World",
            non_interactive=False,
        )
    finally:
        aa.AcousticAligner.align_lyrics = orig_align
        builtins.input = orig_input

    assert captured["hints"] and captured["hints"][0][0] == 1.0
    assert "第一行歌词啊啊" in captured["text"]
    assert out.get("gpu_aligned") is True
    assert out["lines"][0]["translation"] == "trans1"
    assert not out.get("synthesized_words")


def test_existing_words_skip_gpu_and_numpy():
    orig_input = builtins.input
    orig_synth = ps.apply_numpy_word_synth

    def boom_input(*a, **k):
        raise AssertionError("must not prompt GPU when words exist")

    def boom_synth(*a, **k):
        raise AssertionError("must not numpy-synth when words exist")

    builtins.input = boom_input
    ps.apply_numpy_word_synth = boom_synth
    try:
        src = _word_track()
        out = ps.enrich_missing_words(
            src, filepath="x.flac", title="焚情", artist="黄凯芹", non_interactive=False
        )
        assert out is src
    finally:
        builtins.input = orig_input
        ps.apply_numpy_word_synth = orig_synth


def test_plain_fallback_no_text_is_redline():
    orig_collect = ps.collect_plain_lyrics
    orig_input = builtins.input
    ps.collect_plain_lyrics = lambda *a, **k: None
    builtins.input = lambda *a, **k: "y"
    try:
        out = ps.prompt_demucs_fallback("广场", "李志", "x.flac", None, False)
        assert out is None
    finally:
        ps.collect_plain_lyrics = orig_collect
        builtins.input = orig_input


def test_plain_refuse_cannot_numpy():
    orig_input = builtins.input
    orig_align = ps.run_acoustic_align
    builtins.input = lambda *a, **k: "n"
    ps.run_acoustic_align = lambda *a, **k: (_ for _ in ()).throw(AssertionError("refused GPU must not align"))
    try:
        out = ps.prompt_demucs_fallback(
            "广场", "李志", "x.flac", "广场\n一行\n二行\n三行\n四行", False
        )
        assert out is None
    finally:
        builtins.input = orig_input
        ps.run_acoustic_align = orig_align


def test_prepare_payload_hints_from_lines():
    text, trans, hints = ps._prepare_align_payload(
        "Hello", "World", "", existing_lines=_line_only_track()["lines"]
    )
    assert text.splitlines() == ["第一行歌词啊啊", "第二行歌词啊啊"]
    assert trans == ["trans1", "trans2"]
    assert hints[0] == (1.0, "第一行歌词啊啊")


def test_studio_rejects_live_github_filename():
    lyrics = (
        "广场\n夜色覆盖了城市的灯\n我走在无人的街\n把沉默交给风\n"
        "把回忆交给雨\n把名字交给你"
    )

    def fake_get(url, **kwargs):
        if "bing.com" in url or "duckduckgo" in url or "github.com/search" in url:
            return '<a href="https://github.com/lizhi/lyrics/blob/main/广场_live.txt">x</a>'
        if "广场_live.txt" in url:
            return lyrics
        return None

    orig_get, orig_five = pls.http_get_text, pls._from_five_sources
    pls.http_get_text = fake_get
    pls._from_five_sources = lambda *a, **k: None
    try:
        text = fetch_plain_lyric_text("广场", "李志")
        assert text is None
    finally:
        pls.http_get_text = orig_get
        pls._from_five_sources = orig_five


def test_live_rejects_studio_github_filename():
    lyrics = (
        "起风了\n某一天\n脚步开始变得轻盈\n心中如阳光般灿烂\n"
        "我抬头看一看\n天空是彩色的"
    )

    def fake_get(url, **kwargs):
        if "bing.com" in url or "duckduckgo" in url or "github.com/search" in url:
            return '<a href="https://github.com/foo/lyrics/blob/main/起风了.txt">x</a>'
        if url.endswith("起风了.txt") or "/起风了.txt" in url:
            return lyrics
        return None

    orig_get, orig_five = pls.http_get_text, pls._from_five_sources
    pls.http_get_text = fake_get
    pls._from_five_sources = lambda *a, **k: None
    try:
        text = fetch_plain_lyric_text("起风了 (Live)", "买辣椒也用券")
        assert text is None
    finally:
        pls.http_get_text = orig_get
        pls._from_five_sources = orig_five


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
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"  ERROR {name}  -> {type(e).__name__}: {e}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
