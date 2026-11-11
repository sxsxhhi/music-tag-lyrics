# -*- coding: utf-8 -*-
"""v1.8.4 R12 independent release gates: audio semantics, lyric tracks and rollback.

Includes deterministic synthetic Ogg and M4A fixtures; no FFmpeg, internet,
private media, or extra codec packages required at test time.
"""
from __future__ import annotations

import hashlib
import struct
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT, SYLT
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest, write_verified_standardized_container,
)
from core.utils.io_safe import atomic_write_many
from scripts.tag_editor import update_audio_tags
from tests.test_standard_container_tags import (
    _create_minimal_mp3, _create_minimal_wav, _create_minimal_flac,
    _TEST_ELRC, _TEST_TTML,
)
from tests._testkit import run

MEDIA = Path(__file__).resolve().parent / "fixtures" / "media"


def copied_fixture(tmp: str, ext: str) -> Path:
    target = Path(tmp) / ("audio." + ext)
    shutil.copy2(MEDIA / ("fixture." + ext), target)
    return target


class R12SecurityCases(unittest.TestCase):
    def test_01_multilingual_id3_preserves_unnamed_nonenglish(self):
        for container in ("mp3", "wav"):
            with self.subTest(container=container), tempfile.TemporaryDirectory() as td:
                p = Path(td) / ("song." + container)
                (_create_minimal_mp3 if container == "mp3" else _create_minimal_wav)(str(p))
                audio = ID3(str(p)) if container == "mp3" else WAVE(str(p))
                tags = audio if container == "mp3" else audio.tags
                if tags is None:
                    audio.add_tags(); tags = audio.tags
                tags.add(USLT(encoding=3, lang="eng", desc="", text="old main"))
                tags.add(USLT(encoding=3, lang="chi", desc="", text="secondary Chinese"))
                tags.add(USLT(encoding=3, lang="jpn", desc="", text="secondary Japanese"))
                tags.add(SYLT(encoding=3, lang="eng", desc="Sync", format=2, type=1,
                              text=[("test", 100)]))
                audio.save(str(p)) if container == "mp3" else audio.save()
                self.assertTrue(update_audio_tags(str(p), lyrics="new main"))
                rb = ID3(str(p)) if container == "mp3" else WAVE(str(p)).tags
                self.assertEqual(rb["USLT::eng"].text, "new main")
                self.assertEqual(rb["USLT::chi"].text, "secondary Chinese")
                self.assertEqual(rb["USLT::jpn"].text, "secondary Japanese")
                self.assertEqual(len(rb.getall("SYLT")), 1)

    def test_02_invalid_mp3_must_not_be_tagged(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "fake.mp3"
            contents = b"NOT_MPEG_AUDIO" + b"junk" * 200
            p.write_bytes(contents)
            self.assertFalse(update_audio_tags(str(p), lyrics="x"))
            self.assertEqual(p.read_bytes(), contents)

    def test_03_wav_fmt_tamper_rejected_by_standardizer(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "source.wav", Path(td) / "out.wav"
            _create_minimal_wav(str(src))
            orig = WAVE.save
            def tamper(obj, *args, **kw):
                val = orig(obj, *args, **kw)
                p = Path(obj.filename)
                d = bytearray(p.read_bytes())
                start = d.find(b"fmt ") + 8
                self.assertGreaterEqual(start, 8)
                d[start + 4:start + 8] = (22050).to_bytes(4, "little")
                d[start + 8:start + 12] = (44100).to_bytes(4, "little")
                p.write_bytes(d)
                return val
            with patch.object(WAVE, "save", tamper):
                with self.assertRaises((RuntimeError, ValueError)):
                    write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(dst.exists())

    def test_04_flac_streaminfo_tamper_rejected_by_standardizer(self):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "source.flac", Path(td) / "out.flac"
            _create_minimal_flac(str(src))
            orig = FLAC.save
            def tamper(obj, *args, **kw):
                val = orig(obj, *args, **kw)
                p = Path(obj.filename)
                d = bytearray(p.read_bytes())
                start = 4 + 4 + 10
                raw = int.from_bytes(d[start:start + 8], "big")
                rate = (raw >> 44) & 0xfffff
                d[start:start + 8] = ((raw & ~(0xfffff << 44)) | ((rate // 2) << 44)).to_bytes(8, "big")
                p.write_bytes(d)
                return val
            with patch.object(FLAC, "save", tamper):
                with self.assertRaises((RuntimeError, ValueError)):
                    write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(dst.exists())

    def test_05_m4a_mdat_bit_flip_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "m4a")
            before = p.read_bytes()
            orig = MP4.save
            def tamper(obj, *args, **kw):
                result = orig(obj, *args, **kw)
                q = Path(obj.filename)
                d = bytearray(q.read_bytes())
                marker = d.find(b"mdat")
                self.assertGreater(marker, 0)
                d[marker + 24] ^= 1
                q.write_bytes(d)
                return result
            with patch.object(MP4, "save", tamper):
                self.assertFalse(update_audio_tags(str(p), lyrics="new"))
            self.assertEqual(p.read_bytes(), before)

    def test_06_ogg_crc_corruption_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "ogg")
            before = p.read_bytes()
            orig = OggVorbis.save
            def tamper(obj, *args, **kw):
                val = orig(obj, *args, **kw)
                q = Path(obj.filename)
                d = bytearray(q.read_bytes())
                i = d.rfind(b"OggS")
                self.assertGreater(i, 0)
                d[i + 27 + d[i + 26] + 20] ^= 1
                q.write_bytes(d)
                return val
            with patch.object(OggVorbis, "save", tamper):
                self.assertFalse(update_audio_tags(str(p), lyrics="new"))
            self.assertEqual(p.read_bytes(), before)

    def test_07_double_commit_failure_keeps_backup(self):
        with tempfile.TemporaryDirectory() as td:
            p, q = Path(td) / "a.lrc", Path(td) / "a.spl"
            p.write_text("OLD_A"); q.write_text("OLD_B")
            native = os.replace
            n = [0]
            def fault(a, b):
                n[0] += 1
                if n[0] in (2, 3):
                    raise OSError("SIMULATED_COMMIT_AND_ROLLBACK_FAILURE")
                return native(a, b)
            with patch("core.utils.io_safe.os.replace", fault):
                with self.assertRaisesRegex(OSError, "SIDECAR_ROLLBACK_INCOMPLETE"):
                    atomic_write_many({str(p): "NEW_A", str(q): "NEW_B"})
            backups = list(Path(td).glob(".tmp_bak*"))
            self.assertTrue(any(x.read_text() == "OLD_A" for x in backups))

    def test_08_normal_ogg_m4a_metadata_roundtrip(self):
        for ext in ("ogg", "m4a"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as td:
                p = copied_fixture(td, ext)
                status, digest = compute_audio_stream_digest(str(p))
                self.assertEqual(status, "VERIFIED")
                self.assertTrue(update_audio_tags(str(p), title="Synthetic Track", lyrics="New primary lyric"))
                self.assertEqual(compute_audio_stream_digest(str(p)), ("VERIFIED", digest))

    def test_09_large_vorbis_comment_does_not_break_audio_digest(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "ogg")
            before = compute_audio_stream_digest(str(p))
            self.assertEqual(before[0], "VERIFIED")
            a = OggVorbis(str(p))
            a["comment"] = ["SYNTHETIC-" * 16000]
            a.save()
            self.assertEqual(compute_audio_stream_digest(str(p)), before)
            self.assertTrue(update_audio_tags(str(p), lyrics="X"))
            self.assertEqual(compute_audio_stream_digest(str(p)), before)

    def test_10_large_mp4_tag_does_not_break_audio_digest(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "m4a")
            before = compute_audio_stream_digest(str(p))
            self.assertEqual(before[0], "VERIFIED")
            audio = MP4(str(p))
            audio["\xa9cmt"] = ["SYNTHETIC-" * 16000]
            audio.save()
            self.assertEqual(compute_audio_stream_digest(str(p)), before)
            self.assertTrue(update_audio_tags(str(p), lyrics="X"))
            self.assertEqual(compute_audio_stream_digest(str(p)), before)

    def test_11_mp4_chunk_offset_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "m4a")
            before = p.read_bytes()
            orig = MP4.save
            def tamper(obj, *args, **kw):
                result = orig(obj, *args, **kw)
                q = Path(obj.filename)
                d = bytearray(q.read_bytes())
                start = d.find(b"stco")
                if start < 0:
                    self.skipTest("fixture has no stco")
                # stco 4-byte fullbox flags + 4-byte count + first offset.
                offset = start + 4 + 8
                old = int.from_bytes(d[offset:offset + 4], "big")
                d[offset:offset + 4] = (old + 1).to_bytes(4, "big")
                q.write_bytes(d)
                return result
            with patch.object(MP4, "save", tamper):
                self.assertFalse(update_audio_tags(str(p), lyrics="X"))
            self.assertEqual(p.read_bytes(), before)

    def test_12_source_ogg_corrupted_crc_must_be_refused(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "ogg")
            d = bytearray(p.read_bytes())
            i = d.rfind(b"OggS")
            d[i + 27 + d[i + 26] + 20] ^= 1
            p.write_bytes(d)
            before = p.read_bytes()
            self.assertNotEqual(compute_audio_stream_digest(str(p))[0], "VERIFIED")
            self.assertFalse(update_audio_tags(str(p), lyrics="X"))
            self.assertEqual(p.read_bytes(), before)

    def test_13_primary_only_english_lyric_replacement_is_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audio.mp3"
            _create_minimal_mp3(str(p))
            tags = ID3(str(p))
            tags.add(USLT(encoding=3, lang="chi", desc="", text="Chinese"))
            tags.add(USLT(encoding=3, lang="eng", desc="", text="English"))
            tags.save(str(p))
            self.assertTrue(update_audio_tags(str(p), lyrics="Canonical"))
            tags2 = ID3(str(p))
            self.assertEqual(tags2["USLT::chi"].text, "Chinese")
            self.assertEqual(tags2["USLT::eng"].text, "Canonical")

    def test_14_failed_second_replace_ordinary_rollback_clean(self):
        with tempfile.TemporaryDirectory() as td:
            p, q = Path(td)/"a.lrc", Path(td)/"a.spl"
            p.write_text("A"); q.write_text("B")
            original = os.replace
            count = [0]
            def fault(a,b):
                count[0] += 1
                if count[0] == 2:
                    raise OSError("commit failed")
                return original(a,b)
            with patch("core.utils.io_safe.os.replace", fault):
                with self.assertRaises(OSError):
                    atomic_write_many({str(p): "A2", str(q): "B2"})
            self.assertEqual((p.read_text(), q.read_text()), ("A", "B"))
            self.assertFalse(list(Path(td).glob(".tmp_bak*")))


    def test_15_valid_dsf_first_id3_roundtrip_preserves_dsd(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "synthetic.dsf"
            payload = b"\x69" * 4096
            fmt = struct.pack("<IIIIIIQII", 1, 0, 1, 1, 2822400, 1,
                              len(payload) * 8, 4096, 0)
            total = 28 + 52 + 12 + len(payload)
            p.write_bytes(b"DSD " + struct.pack("<QQQ", 28, total, 0)
                          + b"fmt " + struct.pack("<Q", 52) + fmt
                          + b"data" + struct.pack("<Q", 12 + len(payload)) + payload)
            original = compute_audio_stream_digest(str(p))
            self.assertEqual(original[0], "VERIFIED")
            self.assertTrue(update_audio_tags(str(p), lyrics="Synthetic DSD"))
            self.assertEqual(compute_audio_stream_digest(str(p)), original)

    def test_16_concurrent_source_change_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "ogg")
            original_save = OggVorbis.save
            def inject_change(obj, *args, **kwargs):
                result = original_save(obj, *args, **kwargs)
                # External writer changes the original *after* the private
                # copy has been made; never overwrite their newer file.
                with open(p, "ab") as source:
                    source.write(b"SYNTHETIC_CONCURRENT_WRITE")
                return result
            with patch.object(OggVorbis, "save", inject_change):
                self.assertFalse(update_audio_tags(str(p), lyrics="new"))
            self.assertTrue(p.read_bytes().endswith(b"SYNTHETIC_CONCURRENT_WRITE"))

    def test_17_mp4_audio_track_header_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = copied_fixture(td, "m4a")
            before = p.read_bytes()
            original_save = MP4.save
            def mutate(obj, *args, **kwargs):
                result = original_save(obj, *args, **kwargs)
                q = Path(obj.filename)
                d = bytearray(q.read_bytes())
                i = d.find(b"tkhd")
                self.assertGreater(i, 0)
                # Flip a tkhd matrix byte without touching the mdat payload.
                d[i + 44] ^= 1
                q.write_bytes(d)
                return result
            with patch.object(MP4, "save", mutate):
                self.assertFalse(update_audio_tags(str(p), lyrics="new"))
            self.assertEqual(p.read_bytes(), before)


from tests._testkit import run, skip

def _wrap(test_name):
    def case():
        obj = R12SecurityCases()
        try:
            getattr(obj, test_name)()
        except unittest.SkipTest as e:
            skip(str(e))
    case.__name__ = test_name
    return case

for _name in dir(R12SecurityCases):
    if _name.startswith("test_"):
        globals()[_name] = _wrap(_name)

if __name__ == "__main__":
    sys.exit(run(globals()))
