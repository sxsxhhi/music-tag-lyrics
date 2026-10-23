# -*- coding: utf-8 -*-
"""流水线离线回归：双语剥离、同一版整包、ELRC 副行无逐字、ID3 安全。运行：python tests/test_pipeline.py"""

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
from core.acoustic_aligner import enrich_track_with_acoustic_words
from scripts.lyric_validator import is_credit_line, pair_plain_bilingual_lyrics, validate_and_fix_lyrics
from scripts.tag_editor import TagSet, read_audio_tags, update_audio_tags
from mutagen.id3 import ID3, USLT


def test_validate_does_not_mutate_input():
    """P1-3：校验必须深拷贝，不得改脏调用方手里的 data。"""
    data = {"lines": [
        {"start": 5000, "end": 9000, "duration": 4000, "text": "第一句", "translation": "", "words": []},
        {"start": 4000, "end": 8000, "duration": 4000, "text": "第二句倒流", "translation": "", "words": []},
    ]}
    before = [l["start"] for l in data["lines"]]
    ok, out, _warns = validate_and_fix_lyrics(data, audio_duration_ms=0)
    assert ok
    assert [l["start"] for l in data["lines"]] == before, "入参被就地修改"
    assert out["lines"][1]["start"] == 5100, out["lines"][1]["start"]


def test_validate_word_no_overlap_after_repair():
    """P1-3：超短词拉长后不得越过下一个词的起点。"""
    data = {"lines": [{
        "start": 10000, "end": 11000, "duration": 1000, "text": "甲乙", "translation": "",
        "words": [
            {"start": 10000, "end": 10040, "text": "甲", "duration": 40},
            {"start": 10040, "end": 11000, "text": "乙", "duration": 960},
        ],
    }]}
    ok, out, _warns = validate_and_fix_lyrics(data, audio_duration_ms=0)
    ws = out["lines"][0]["words"]
    assert ws[0]["end"] <= ws[1]["start"], f"重叠：{ws[0]} / {ws[1]}"


def test_validate_keeps_real_end_duration():
    """P1-3：只给 start/end 没有 duration 时，必须用 end-start，不能被改成 150ms。"""
    data = {"lines": [{
        "start": 1000, "end": 4000, "duration": 3000, "text": "长句", "translation": "",
        "words": [{"start": 1000, "end": 4000, "text": "长句"}],
    }]}
    ok, out, _warns = validate_and_fix_lyrics(data, audio_duration_ms=0)
    assert out["lines"][0]["words"][0]["duration"] == 3000


def test_acoustic_gate_thresholds_do_not_contradict():
    """P1-3：兜底 100ms 之后，不该再因为「恰好 80ms」触发 <80ms 告警。"""
    data = {"lines": [{
        "start": 1000, "end": 3000, "duration": 2000, "text": "甲乙", "translation": "",
        "words": [
            {"start": 1000, "end": 1040, "text": "甲", "duration": 40},
            {"start": 2000, "end": 2040, "text": "乙", "duration": 40},
        ],
    }]}
    ok, out, warns = validate_and_fix_lyrics(data, audio_duration_ms=0)
    for w in out["lines"][0]["words"]:
        assert w["duration"] == 100, w
    assert not [w for w in warns if "Natural Acoustic Alert" in w], warns


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
    data = enrich_track_with_acoustic_words(data)
    orig = data["lines"][0]
    assert orig.get("words"), orig
    assert "words" not in (orig.get("translation") or {}) if isinstance(orig.get("translation"), dict) else True
    out = to_spl(data, title="测试曲", artist="测试人")
    assert "[00:01.00]我曾经想过一了百了" in out, out[:300]
    assert "<00:" in out  # 原文有逐字
    trans_line = "[00:01.00]我曾经想过一了百了"
    assert trans_line in out
    # 译文副行不得带 <> 逐字
    for raw in out.splitlines():
        if "我曾经想过一了百了" in raw or "海猫的啼哭声" in raw:
            assert "<" not in raw, raw
    assert "[00:01.00]僕が死のうと思ったのは" in out or "<00:" in out


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


def test_id3_write_replaces_all_lyric_frames():
    """写入逐字时清掉旧 USLT（含其它语言）与 SYLT，只留一帧 Enhanced LRC。"""
    work = tempfile.mkdtemp(prefix="mtl_pipe_")
    try:
        p = os.path.join(work, "tagged.mp3")
        tag = ID3()
        tag.add(USLT(encoding=3, lang="eng", desc="", text="OLD"))
        tag.add(USLT(encoding=3, lang="chi", desc="", text="中文逐行"))
        tag.save(p)

        assert update_audio_tags(p, lyrics="<00:01.00>新歌词") is True
        t = ID3(p)
        frames = {k: str(t[k].text) for k in t.keys() if k.startswith("USLT")}
        assert list(frames) == ["USLT::eng"], frames
        assert frames["USLT::eng"] == "<00:01.00>新歌词", frames
        assert not any(k.startswith("SYLT") for k in t.keys())
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


def test_netease_same_version_package_replaces_kugou_without_trans():
    """外语：禁止跨源挂载；酷狗无译文且网易有原文+tlyric 时整包换成网易；无 tlyric 不覆盖酷狗。"""
    import core.aggregator as agg

    kugou = {
        "source": "kugou",
        "lyric_type": "verbatim",
        "lines": [
            {"start": 1000, "end": 4000, "duration": 3000, "text": "Hello world", "words": [{"text": "Hello", "start": 1000, "end": 2500}]},
            {"start": 4000, "end": 7000, "duration": 3000, "text": "See you", "words": []},
            {"start": 8000, "end": 11000, "duration": 3000, "text": "Once more", "words": []},
            {"start": 12000, "end": 15000, "duration": 3000, "text": "Forever", "words": []},
        ],
    }
    ne_lines = [
        {"start": 1100, "end": 4100, "duration": 3000, "text": "Hello world", "words": []},
        {"start": 4100, "end": 7100, "duration": 3000, "text": "See you", "words": []},
        {"start": 8100, "end": 11100, "duration": 3000, "text": "Once more", "words": []},
        {"start": 12100, "end": 15100, "duration": 3000, "text": "Forever", "words": []},
    ]
    tlyric = (
        "[00:01.00]你好世界\n"
        "[00:04.00]再见\n"
        "[00:08.00]再一次\n"
        "[00:12.00]永远\n"
    )

    grafted = agg.enrich_foreign_translation(dict(kugou), "Hello", "Western Artist")
    assert grafted.get("translation_source") != "netease"
    assert not any((l.get("translation") or "").strip() for l in grafted["lines"])

    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            return {**kugou, "lines": [dict(x) for x in kugou["lines"]]}

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    class FakeNE:
        @classmethod
        def fetch(cls, *a, **k):
            return {"source": "netease", "lyric_type": "line", "lines": [dict(x) for x in ne_lines], "tlyric": tlyric}

    class BoomLS:
        @classmethod
        def fetch(cls, *a, **k):
            raise AssertionError("LRCSHARE should not run after NetEase whole-package attach")

    reals = {
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }
    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeNone
    agg.NetEaseFetcher = FakeNE
    agg.LrcShareFetcher = BoomLS
    agg.LRCLIBFetcher = FakeNone
    try:
        out = agg.LyricAggregator.fetch_best_lyrics("Hello", "Western Artist")
    finally:
        for name, real in reals.items():
            setattr(agg, name, real)

    assert out["source"] == "netease", out
    assert out.get("translation_source") == "netease"
    assert [l.get("translation") for l in out["lines"]] == ["你好世界", "再见", "再一次", "永远"]
    assert out["lines"][0]["start"] == 1100

    class FakeNENoTrans:
        @classmethod
        def fetch(cls, *a, **k):
            return {"source": "netease", "lyric_type": "line", "lines": [dict(x) for x in ne_lines]}

    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeNone
    agg.NetEaseFetcher = FakeNENoTrans
    agg.LrcShareFetcher = FakeNone
    agg.LRCLIBFetcher = FakeNone
    try:
        out2 = agg.LyricAggregator.fetch_best_lyrics("Hello", "Western Artist")
    finally:
        for name, real in reals.items():
            setattr(agg, name, real)

    assert out2["source"] == "kugou", out2
    assert not any((l.get("translation") or "").strip() for l in out2["lines"])


def test_chinese_kugou_not_replaced_by_netease_tlyric():
    """华语：酷狗已命中时网易即使带 tlyric 也不得覆盖。"""
    import core.aggregator as agg

    kugou = {
        "source": "kugou",
        "lyric_type": "verbatim",
        "lines": [
            {"start": 1000, "end": 4000, "duration": 3000, "text": "焚情如火", "words": [{"text": "焚", "start": 1000, "end": 1500}]},
            {"start": 4000, "end": 7000, "duration": 3000, "text": "第二句", "words": []},
            {"start": 8000, "end": 11000, "duration": 3000, "text": "第三句", "words": []},
            {"start": 12000, "end": 15000, "duration": 3000, "text": "第四句", "words": []},
        ],
    }

    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            return {**kugou, "lines": [dict(x) for x in kugou["lines"]]}

    class BoomNE:
        @classmethod
        def fetch(cls, *a, **k):
            raise AssertionError("华语酷狗已命中时不应再探网易")

    reals = {
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeNone
    agg.NetEaseFetcher = BoomNE
    agg.LrcShareFetcher = FakeNone
    agg.LRCLIBFetcher = FakeNone
    try:
        out = agg.LyricAggregator.fetch_best_lyrics("焚情", "黄凯芹")
    finally:
        for name, real in reals.items():
            setattr(agg, name, real)

    assert out["source"] == "kugou", out
    assert out["lines"][0]["text"] == "焚情如火"


def test_pair_plain_bilingual_lyrics_keeps_translation_line_level():
    """GPU 打轴前：原文与译文拆开，译文不进入 align 文本。"""
    text = "Hello world\n你好世界\nSee you\n再见"
    orig, trans = pair_plain_bilingual_lyrics(text)
    assert orig.splitlines() == ["Hello world", "See you"]
    assert trans == ["你好世界", "再见"]


def test_dsf_write_uses_tagset_not_legacy_args():
    """v1.2.5 P1：DSF 写入必须走 _apply_id3_tags(audio.tags, tags)，不能用已删除的散参数签名。"""
    import inspect
    src = inspect.getsource(update_audio_tags)
    assert "_apply_id3_tags(audio.tags, tags)" in src
    assert "title, artist, album, year, tracknumber, composer, lyricist, lyrics, albumartist" not in src


def test_dsf_is_supported_extension():
    """v1.2.5：目录扫描必须包含 .dsf，否则 DSF 曲目会被整批跳过。"""
    from scripts.process_song import SUPPORTED_EXTS
    assert ".dsf" in SUPPORTED_EXTS


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
