# -*- coding: utf-8 -*-
"""v1.8.4 Multi-track & Multi-language Lyric Preservation Regression Suite.

Tests cover FLAC, MP3, and WAV container tags for:
1. Complete preservation of secondary/multivalue lyrics (FLAC lyrics, ID3 USLT, SYLT).
2. Proper enumeration and digest hashing in source_inventory (zero raw lyric leaks).
3. Exact readback verification and rollback on secondary track tampering.
4. Fail-closed behavior on unresolvable conflicting secondary lyric identifiers.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT, SYLT, TXXX
from mutagen.wave import WAVE

from core.adapters.container_tag_adapter import (
    read_container_tags,
    write_verified_standardized_container,
)
from scripts.standardize_song import standardize_single_song
from tests._testkit import run
from tests.test_standard_container_tags import (
    _create_minimal_flac,
    _create_minimal_mp3,
    _create_minimal_wav,
    _TEST_ELRC,
    _TEST_TTML,
)

EXTRA_LYRIC_1 = "[00:01.000]Secondary language translation (synthetic line 1)\n"
EXTRA_LYRIC_2 = "[00:02.000]Third language translation (synthetic line 2)\n"
EXTRA_TTML_1 = _TEST_TTML.replace("唱词", "副轨歌词")


def test_01_flac_multivalue_lyrics_write_container():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "src.flac"
        dst = Path(td) / "dst.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Song Title"]
        f["artist"] = ["Artist Name"]
        f["lyrics"] = [_TEST_ELRC, EXTRA_LYRIC_1, EXTRA_LYRIC_2]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        res = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert res["status"] == "PASS", res

        after = FLAC(str(dst))
        lyrics = after.get("lyrics", [])
        assert len(lyrics) == 3, f"Expected 3 lyrics entries, got {len(lyrics)}"
        assert lyrics[0] == _TEST_ELRC
        assert lyrics[1] == EXTRA_LYRIC_1
        assert lyrics[2] == EXTRA_LYRIC_2

        rb = read_container_tags(str(dst))
        assert len(rb["extra_lyrics"]) == 2
        assert rb["extra_lyrics"][0]["sha256"] == hashlib.sha256(EXTRA_LYRIC_1.encode("utf-8")).hexdigest()
        assert rb["extra_lyrics"][1]["sha256"] == hashlib.sha256(EXTRA_LYRIC_2.encode("utf-8")).hexdigest()


def test_02_flac_multivalue_lyrics_standardize_write_tags():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        out_dir = Path(td) / "out"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Song Title"]
        f["artist"] = ["Artist Name"]
        f["lyrics"] = [_TEST_ELRC, EXTRA_LYRIC_1]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        res = standardize_single_song(str(src), mode="standardize", output_dir=str(out_dir), write_tags=True)
        assert res["status"] == "PASS", res
        assert "WRITE_VERIFIED_CONTAINER_TAGS" in res["actions_taken"]

        target = out_dir / src.name
        after = FLAC(str(target))
        assert EXTRA_LYRIC_1 in after.get("lyrics", [])
        assert res["secondary_lyrics_count"] == 1
        assert len(res["source_inventory"]["embedded_secondary_lyrics"]) == 1


def test_03_flac_multivalue_lyrics_audit_mode():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Song Title"]
        f["artist"] = ["Artist Name"]
        f["lyrics"] = [_TEST_ELRC, EXTRA_LYRIC_1]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        res = standardize_single_song(str(src), mode="audit")
        assert res["status"] == "PASS", res
        assert res["secondary_lyrics_count"] == 1
        sec = res["source_inventory"]["embedded_secondary_lyrics"][0]
        assert sec["container"] == "FLAC"
        assert sec["kind"] == "lyrics"
        assert sec["sha256"] == hashlib.sha256(EXTRA_LYRIC_1.encode("utf-8")).hexdigest()
        assert "raw_text" not in sec and "text" not in sec


def test_04_mp3_secondary_uslt_and_sylt_write_container():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp3"
        dst = Path(td) / "output.mp3"
        _create_minimal_mp3(str(src))
        f = ID3(str(src))
        f.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        f.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        f.add(SYLT(encoding=3, lang="eng", desc="Karaoke", format=2, type=1, text=[("唱", 1000), ("词", 1500)]))
        f.save(str(src))

        res = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert res["status"] == "PASS", res

        after_id3 = ID3(str(dst))
        uslt_list = after_id3.getall("USLT")
        assert len(uslt_list) == 2, f"Expected 2 USLT frames, got {len(uslt_list)}"
        assert any(x.desc == "Translation" and x.lang == "chi" and x.text == EXTRA_LYRIC_1 for x in uslt_list)

        sylt_list = after_id3.getall("SYLT")
        assert len(sylt_list) == 1, f"Expected 1 SYLT frame, got {len(sylt_list)}"
        assert sylt_list[0].desc == "Karaoke"
        assert sylt_list[0].text == [("唱", 1000), ("词", 1500)]


def test_05_mp3_secondary_uslt_and_sylt_standardize_write_tags():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp3"
        out_dir = Path(td) / "out"
        _create_minimal_mp3(str(src))
        f = ID3(str(src))
        f.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        f.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        f.add(SYLT(encoding=3, lang="eng", desc="", format=2, type=1, text=[("test", 500)]))
        f.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
        f.save(str(src))

        res = standardize_single_song(str(src), mode="standardize", output_dir=str(out_dir), write_tags=True)
        assert res["status"] == "PASS", res

        target = out_dir / src.name
        after = ID3(str(target))
        assert any(x.desc == "Translation" and x.text == EXTRA_LYRIC_1 for x in after.getall("USLT"))
        assert len(after.getall("SYLT")) == 1
        assert res["secondary_lyrics_count"] == 2


def test_06_mp3_secondary_uslt_and_sylt_audit_mode():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp3"
        _create_minimal_mp3(str(src))
        f = ID3(str(src))
        f.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        f.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        f.add(SYLT(encoding=3, lang="eng", desc="", format=2, type=1, text=[("synced", 100)]))
        f.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
        f.save(str(src))

        res = standardize_single_song(str(src), mode="audit")
        assert res["status"] == "PASS", res
        assert res["secondary_lyrics_count"] == 2
        sec_list = res["source_inventory"]["embedded_secondary_lyrics"]
        kinds = {s["kind"] for s in sec_list}
        assert kinds == {"USLT", "SYLT"}


def test_07_wav_secondary_uslt_and_sylt_write_container():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.wav"
        dst = Path(td) / "output.wav"
        _create_minimal_wav(str(src))
        w = WAVE(str(src))
        if w.tags is None:
            w.add_tags()
        w.tags.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        w.tags.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        w.tags.add(SYLT(encoding=3, lang="eng", desc="SyncWav", format=2, type=1, text=[("wav", 200)]))
        w.save()

        res = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert res["status"] == "PASS", res

        after_w = WAVE(str(dst))
        uslt_list = after_w.tags.getall("USLT")
        assert len(uslt_list) == 2
        assert any(x.desc == "Translation" and x.text == EXTRA_LYRIC_1 for x in uslt_list)
        sylt_list = after_w.tags.getall("SYLT")
        assert len(sylt_list) == 1
        assert sylt_list[0].desc == "SyncWav"


def test_08_wav_secondary_uslt_and_sylt_standardize_write_tags():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.wav"
        out_dir = Path(td) / "out"
        _create_minimal_wav(str(src))
        w = WAVE(str(src))
        if w.tags is None:
            w.add_tags()
        w.tags.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        w.tags.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        w.tags.add(SYLT(encoding=3, lang="eng", desc="", format=2, type=1, text=[("wav", 100)]))
        w.tags.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
        w.save()

        res = standardize_single_song(str(src), mode="standardize", output_dir=str(out_dir), write_tags=True)
        assert res["status"] == "PASS", res

        target = out_dir / src.name
        after = WAVE(str(target))
        assert any(x.desc == "Translation" and x.text == EXTRA_LYRIC_1 for x in after.tags.getall("USLT"))
        assert len(after.tags.getall("SYLT")) == 1


def test_09_wav_secondary_uslt_and_sylt_audit_mode():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.wav"
        _create_minimal_wav(str(src))
        w = WAVE(str(src))
        if w.tags is None:
            w.add_tags()
        w.tags.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        w.tags.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        w.tags.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
        w.save()

        res = standardize_single_song(str(src), mode="audit")
        assert res["status"] == "PASS", res
        assert res["secondary_lyrics_count"] == 1


def test_10_flac_multivalue_ttml_preserved():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        dst = Path(td) / "output.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Title"]
        f["artist"] = ["Artist"]
        f["lyrics"] = [_TEST_ELRC]
        f["ttmllyric"] = [_TEST_TTML, EXTRA_TTML_1]
        f.save()

        res = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert res["status"] == "PASS", res

        after = FLAC(str(dst))
        ttmls = after.get("ttmllyric", [])
        assert len(ttmls) == 2
        assert ttmls[0] == _TEST_TTML
        assert ttmls[1] == EXTRA_TTML_1


def test_11_id3_secondary_lyrics_conflict_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp3"
        _create_minimal_mp3(str(src))
        f = ID3(str(src))
        # Two competing primary USLT tracks with empty desc and conflicting content
        f.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        f.add(USLT(encoding=3, lang="chi", desc="", text=EXTRA_LYRIC_1))
        f.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
        f.save(str(src))

        res = standardize_single_song(str(src), mode="standardize", output_dir=str(Path(td) / "out"))
        assert res["status"] == "REVIEW_REQUIRED", res
        assert any(x["code"] == "SOURCE_LYRICS_CONFLICT" for x in res["issues"])


def test_12_flac_conflicting_ttml_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Title"]
        f["artist"] = ["Artist"]
        f["lyrics"] = [_TEST_ELRC]
        divergent_ttml = _TEST_TTML.replace("Hello", "DivergentText")
        f["ttmllyric"] = [_TEST_TTML, divergent_ttml]
        f.save()

        res = standardize_single_song(str(src), mode="standardize", output_dir=str(Path(td) / "out"))
        assert res["status"] == "REVIEW_REQUIRED", res
        assert any(x["code"] == "SOURCE_LYRICS_CONFLICT" for x in res["issues"])


def test_13_tampered_secondary_lyrics_readback_rollback():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        dst = Path(td) / "output.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Title"]
        f["artist"] = ["Artist"]
        f["lyrics"] = [_TEST_ELRC, EXTRA_LYRIC_1]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        # Monkey-patch FLAC.save temporarily to simulate secondary lyric drop
        orig_save = FLAC.save
        def fake_save(self, *args, **kwargs):
            # simulate dropping secondary lyric during write
            self["lyrics"] = [_TEST_ELRC]
            return orig_save(self, *args, **kwargs)

        try:
            FLAC.save = fake_save
            try:
                write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            except RuntimeError as exc:
                assert "PROTECTED_METADATA_CHANGED:extra_lyrics" in str(exc)
            else:
                raise AssertionError("Tampered secondary lyrics were not caught by readback verification")
            assert not dst.exists(), "Target file must not exist after rollback"
        finally:
            FLAC.save = orig_save


def test_14_source_inventory_never_leaks_raw_lyrics():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Title"]
        f["artist"] = ["Artist"]
        secret_lyric = "[00:01.000]TopSecretLyricContentDoNotLeakInReport\n"
        f["lyrics"] = [_TEST_ELRC, secret_lyric]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        res = standardize_single_song(str(src), mode="audit")
        report_str = str(res)
        assert "TopSecretLyricContentDoNotLeakInReport" not in report_str
        sec_items = res["source_inventory"]["embedded_secondary_lyrics"]
        assert len(sec_items) == 1
        assert "raw_text" not in sec_items[0]
        assert "text" not in sec_items[0]


def test_15_multitrack_repro_integration():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.flac"
        output_dir = Path(td) / "output"
        _create_minimal_flac(str(src))
        f = FLAC(str(src))
        f["title"] = ["Synthetic Song"]
        f["artist"] = ["Synthetic Artist"]
        f["lyrics"] = [_TEST_ELRC, EXTRA_LYRIC_1]
        f["ttmllyric"] = [_TEST_TTML]
        f.save()

        result = standardize_single_song(str(src), mode="standardize", output_dir=str(output_dir), write_tags=True)
        assert result["status"] == "PASS", result
        after = FLAC(str(output_dir / src.name))["lyrics"]
        assert EXTRA_LYRIC_1 in after

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.mp3"
        dst = Path(td) / "output.mp3"
        _create_minimal_mp3(str(src))
        f = ID3(str(src))
        f.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
        f.add(USLT(encoding=3, lang="chi", desc="Translation", text=EXTRA_LYRIC_1))
        f.save(str(src))

        result = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert result["status"] == "PASS", result
        after = ID3(str(dst)).getall("USLT")
        assert any(x.lang == "chi" and x.text == EXTRA_LYRIC_1 for x in after)


if __name__ == "__main__":
    sys.exit(run(globals()))
