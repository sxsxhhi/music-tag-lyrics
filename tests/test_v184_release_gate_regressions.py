# -*- coding: utf-8 -*-
"""Independent safety and release-gate regressions for v1.8.4.
Verifies routine multitrack lyric preservation, non-lyric metadata invariance,
audio stream corruption rejection, untagged MP3 initialization, and transactional sidecar rollback.
All audio/lyrics data are synthetic; no network access.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.test_standard_container_tags import (  # noqa: E402
    _create_minimal_flac,
    _create_minimal_mp3,
    _create_minimal_wav,
    _TEST_ELRC,
    _TEST_TTML,
)
from scripts.tag_editor import update_audio_tags
from core.adapters.container_tag_adapter import (
    write_verified_standardized_container,
    compute_audio_stream_digest,
)
from mutagen.flac import FLAC
from mutagen.id3 import ID3, PRIV, TCOM, TXXX, USLT, SYLT
from mutagen.wave import WAVE
from mutagen.oggvorbis import OggVorbis
from mutagen.mp4 import MP4

SECONDARY = "SYNTHETIC_SECONDARY_TRANSLATION_71531"


def make_mp3(path: Path) -> Path:
    _create_minimal_mp3(str(path))
    tags = ID3(str(path))
    tags.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
    tags.add(TXXX(encoding=3, desc="TTMLLYRIC", text=[_TEST_TTML]))
    tags.save(str(path))
    return path


def make_external_audio(path: Path, codec: str) -> None:
    subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
        "-c:a", codec, "-y", str(path),
    ], check=True, capture_output=True, timeout=20)


class ReleaseBlockingRegressions(unittest.TestCase):
    def test_01_flac_routine_preserves_secondary_lyrics(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.flac"
            _create_minimal_flac(str(source))
            audio = FLAC(str(source))
            audio["lyrics"] = [_TEST_ELRC, SECONDARY]
            audio["ttmllyric"] = [_TEST_TTML]
            audio.save()
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC))
            self.assertIn(SECONDARY, FLAC(str(source)).get("lyrics", []))

    def test_02_mp3_routine_preserves_secondary_uslt_and_sylt(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_mp3(Path(td) / "input.mp3")
            tags = ID3(str(source))
            tags.add(USLT(encoding=3, lang="chi", desc="Translation", text=SECONDARY))
            tags.add(SYLT(encoding=3, lang="eng", desc="Synced", format=2, type=1,
                          text=[("test", 100)]))
            tags.save(str(source))
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC))
            final = ID3(str(source))
            self.assertTrue(any(x.desc == "Translation" and x.text == SECONDARY
                                for x in final.getall("USLT")))
            self.assertTrue(final.getall("SYLT"))

    def test_03_wav_routine_preserves_secondary_uslt_and_sylt(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.wav"
            _create_minimal_wav(str(source))
            audio = WAVE(str(source))
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=_TEST_ELRC))
            audio.tags.add(USLT(encoding=3, lang="chi", desc="Translation", text=SECONDARY))
            audio.tags.add(SYLT(encoding=3, lang="eng", desc="Synced", format=2, type=1,
                                text=[("test", 100)]))
            audio.save()
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC))
            final = WAVE(str(source)).tags
            self.assertTrue(any(x.desc == "Translation" and x.text == SECONDARY
                                for x in final.getall("USLT")))
            self.assertTrue(final.getall("SYLT"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
    def test_04_ogg_routine_preserves_secondary_lyrics(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.ogg"
            make_external_audio(source, "libvorbis")
            audio = OggVorbis(str(source))
            audio["lyrics"] = [_TEST_ELRC, SECONDARY]
            audio.save()
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC))
            self.assertIn(SECONDARY, OggVorbis(str(source)).get("lyrics", []))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not available")
    def test_05_m4a_routine_preserves_secondary_lyrics(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.m4a"
            make_external_audio(source, "aac")
            audio = MP4(str(source))
            audio["\xa9lyr"] = [_TEST_ELRC, SECONDARY]
            audio.save()
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC))
            self.assertIn(SECONDARY, MP4(str(source)).get("\xa9lyr", []))

    def test_06_verified_writer_must_reject_private_id3_frame_loss(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_mp3(Path(td) / "input.mp3")
            target = Path(td) / "target.mp3"
            tags = ID3(str(source))
            tags.add(PRIV(owner="private.local", data=b"SYNTHETIC_PRIVATE_PAYLOAD"))
            tags.save(str(source))
            original_save = ID3.save

            def corrupt(self, *a, **kw):
                self.delall("PRIV")
                return original_save(self, *a, **kw)

            with patch.object(ID3, "save", corrupt):
                with self.assertRaises((RuntimeError, ValueError)):
                    write_verified_standardized_container(
                        str(source), str(target), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(target.exists())

    def test_07_verified_writer_must_reject_unrequested_credit_modification(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_mp3(Path(td) / "input.mp3")
            target = Path(td) / "target.mp3"
            tags = ID3(str(source))
            tags.add(TCOM(encoding=3, text=["ORIGINAL_COMPOSER"]))
            tags.save(str(source))
            original_save = ID3.save

            def corrupt(self, *a, **kw):
                self.get("TCOM").text = ["TAMPERED_COMPOSER"]
                return original_save(self, *a, **kw)

            with patch.object(ID3, "save", corrupt):
                with self.assertRaises((RuntimeError, ValueError)):
                    write_verified_standardized_container(
                        str(source), str(target), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(target.exists())

    def test_08_verified_writer_must_reject_custom_flac_tag_loss(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "input.flac"
            target = Path(td) / "target.flac"
            _create_minimal_flac(str(source))
            audio = FLAC(str(source))
            audio["lyrics"] = [_TEST_ELRC]
            audio["ttmllyric"] = [_TEST_TTML]
            audio["custom_opaque_note"] = ["KEEP_THIS_METADATA"]
            audio.save()
            original_save = FLAC.save

            def corrupt(self, *a, **kw):
                if "custom_opaque_note" in self:
                    del self["custom_opaque_note"]
                return original_save(self, *a, **kw)

            with patch.object(FLAC, "save", corrupt):
                with self.assertRaises((RuntimeError, ValueError)):
                    write_verified_standardized_container(
                        str(source), str(target), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(target.exists())

    def test_09_routine_must_not_commit_modified_mp3_audio_stream(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_mp3(Path(td) / "input.mp3")
            before_status, before_digest = compute_audio_stream_digest(str(source))
            self.assertEqual(before_status, "VERIFIED")
            original_bytes = source.read_bytes()
            original_save = ID3.save

            def corrupt(self, *a, **kw):
                result = original_save(self, *a, **kw)
                filename = a[0] if a else kw.get("filething")
                if filename:
                    with open(filename, "r+b") as fh:
                        header = fh.read(10)
                        size = sum((header[i] & 0x7f) << (7 * (9 - i))
                                   for i in (6, 7, 8, 9))
                        fh.seek(10 + size + 15)
                        value = fh.read(1)
                        self_outer.assertTrue(value)
                        fh.seek(10 + size + 15)
                        fh.write(bytes([value[0] ^ 1]))
                return result

            self_outer = self
            with patch.object(ID3, "save", corrupt):
                ok = update_audio_tags(str(source), lyrics=_TEST_ELRC)
            after_status, after_digest = compute_audio_stream_digest(str(source))
            self.assertFalse(ok, "Audio-stream-corrupted write was reported successful")
            self.assertEqual(before_status, after_status)
            self.assertEqual(before_digest, after_digest)
            self.assertEqual(source.read_bytes(), original_bytes)

    def test_10_standardizer_positive_control_rejects_audio_corruption(self):
        with tempfile.TemporaryDirectory() as td:
            source = make_mp3(Path(td) / "input.mp3")
            target = Path(td) / "target.mp3"
            original_save = ID3.save

            def corrupt(self, *a, **kw):
                result = original_save(self, *a, **kw)
                filename = a[0] if a else kw.get("filething")
                if filename:
                    with open(filename, "r+b") as fh:
                        header = fh.read(10)
                        size = sum((header[i] & 0x7f) << (7 * (9 - i))
                                   for i in (6, 7, 8, 9))
                        fh.seek(10 + size + 15)
                        byte = fh.read(1)
                        fh.seek(10 + size + 15)
                        fh.write(bytes([byte[0] ^ 1]))
                return result

            with patch.object(ID3, "save", corrupt):
                with self.assertRaisesRegex(RuntimeError, "AUDIO_STREAM_CORRUPTED"):
                    write_verified_standardized_container(
                        str(source), str(target), _TEST_ELRC, _TEST_TTML)
            self.assertFalse(target.exists())

    def test_11_routine_can_add_first_id3_tag_to_valid_mp3(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "untagged.mp3"
            frame = b"\xff\xfb\x90\x00" + (b"\x00" * 413)
            source.write_bytes(frame * 4)
            from mutagen.mp3 import MP3
            self.assertGreater(MP3(str(source)).info.length, 0)
            self.assertTrue(update_audio_tags(str(source), lyrics=_TEST_ELRC),
                            "Untagged but valid MP3 should accept new ID3 tags")
            self.assertEqual(ID3(str(source)).get("USLT::eng").text, _TEST_ELRC)

    def test_12_two_sidecars_rollback_on_second_commit_failure(self):
        from core.utils.io_safe import atomic_write_many
        with tempfile.TemporaryDirectory() as td:
            a, b = Path(td) / "a.lrc", Path(td) / "a.spl"
            a.write_text("OLD_A", encoding="utf-8")
            b.write_text("OLD_B", encoding="utf-8")
            native = os.replace
            count = [0]

            def fault(src, dst):
                count[0] += 1
                if count[0] == 2:
                    raise OSError("SIMULATED_SECOND_COMMIT_FAILURE")
                return native(src, dst)

            with patch("core.utils.io_safe.os.replace", fault):
                with self.assertRaises(OSError):
                    atomic_write_many({str(a): "NEW_A", str(b): "NEW_B"})
            self.assertEqual((a.read_text("utf-8"), b.read_text("utf-8")),
                             ("OLD_A", "OLD_B"), "Partial sidecar commit after exception")


from tests._testkit import run, skip, SkipTest  # noqa: E402


def _make_runner(method_name: str):
    def _runner():
        case = ReleaseBlockingRegressions()
        try:
            getattr(case, method_name)()
        except unittest.SkipTest as e:
            skip(str(e))
    _runner.__name__ = method_name
    return _runner


for _m in dir(ReleaseBlockingRegressions):
    if _m.startswith("test_"):
        globals()[_m] = _make_runner(_m)


if __name__ == "__main__":
    sys.exit(run(globals()))
