# -*- coding: utf-8 -*-
"""v1.8.4 R13 release gate: independent lossless-lyrics and media invariants.

All fixtures are synthetic and local, no external binaries, network or user
media. A passing test establishes the listed invariant for its own case,
not full codec correctness or power-loss transactional durability.
"""
from __future__ import annotations

import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, USLT, TXXX
from mutagen.mp4 import MP4
from mutagen.wave import WAVE

from core.adapters.audio_payload import mp4_audio_digest
from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest, read_container_tags,
    write_verified_standardized_container,
)
from core.utils.io_safe import safe_save_audio_tags
from scripts.standardize_song import standardize_single_song
from scripts.tag_editor import update_audio_tags
from tests.test_standard_container_tags import (
    _create_minimal_mp3, _create_minimal_flac, _create_minimal_wav,
    _TEST_ELRC, _TEST_TTML,
)
from tests._testkit import run, skip

MEDIA = ROOT / "tests" / "fixtures" / "media"


def _tag_mp3(path: Path, *frames):
    id3 = ID3(str(path))
    for frame in frames:
        id3.add(frame)
    id3.save(str(path))


def _cover(*, desc='ORIGINAL DESCRIPTION', width=200):
    p = Picture()
    p.type = 3
    p.mime = 'image/jpeg'
    p.desc = desc
    p.width = width
    p.height = 200
    p.depth = 24
    p.data = b'\xff\xd8\xff\xe0SYNTHETIC_NONPERSONAL_TEST_IMAGE'
    return p


def _inject_wav_chunk(path: Path, label: bytes, data: bytes):
    assert len(label) == 4
    raw = path.read_bytes()
    pos = raw.index(b'data')
    chunk = label + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')
    raw = raw[:pos] + chunk + raw[pos:]
    raw = raw[:4] + struct.pack('<I', len(raw)-8) + raw[8:]
    path.write_bytes(raw)


def _mvhd_timescale_doubled(path: Path):
    raw = bytearray(path.read_bytes())
    marker = raw.find(b'mvhd')
    assert marker > 0
    at = marker + 4 + (20 if raw[marker+4] == 1 else 12)
    value = int.from_bytes(raw[at:at+4], 'big')
    assert value > 0
    raw[at:at+4] = (value*2).to_bytes(4, 'big')
    path.write_bytes(raw)


class R13Safety(unittest.TestCase):
    def test_01_foreign_only_mp3_is_secondary_not_canonical_primary(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'s.mp3', Path(tmp)/'o.mp3'
            _create_minimal_mp3(str(src))
            _tag_mp3(src, USLT(encoding=3, lang='chi', desc='', text='珍贵中文主歌词'))
            info = read_container_tags(str(src))
            self.assertIsNone(info['_primary_uslt_key'])
            self.assertEqual(info['lyrics'], '')
            self.assertEqual(len(info['extra_lyrics']), 1)
            result = write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            self.assertEqual(result['status'], 'PASS')
            frames = ID3(str(dst))
            self.assertEqual(frames['USLT::chi'].text, '珍贵中文主歌词')
            self.assertEqual(frames['USLT::eng'].text, _TEST_ELRC)

    def test_02_foreign_only_wav_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'s.wav', Path(tmp)/'o.wav'
            _create_minimal_wav(str(src))
            audio = WAVE(str(src)); audio.add_tags()
            audio.tags.add(USLT(encoding=3, lang='jpn', desc='', text='日本語の歌詞'))
            audio.save()
            write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            self.assertEqual(WAVE(str(dst)).tags['USLT::jpn'].text, '日本語の歌詞')

    def test_03_foreign_language_full_standardization_must_not_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, out = Path(tmp)/'s.mp3', Path(tmp)/'out'
            _create_minimal_mp3(str(src))
            # This case exercises the full script-level entry point.
            chinese_elrc = '[00:01.000]<00:01.000>你好<00:02.000> 世界<00:03.000>\n'
            _tag_mp3(src, USLT(encoding=3, lang='chi', desc='', text=chinese_elrc),
                     TXXX(encoding=3, desc='TTMLLYRIC', text=[_TEST_TTML]))
            result = standardize_single_song(str(src), mode='standardize',
                                             output_dir=str(out), write_tags=True)
            dst = out / src.name
            if result['status'] == 'PASS':
                self.assertTrue(dst.is_file())
                self.assertEqual(ID3(str(dst))['USLT::chi'].text, chinese_elrc)
            else:
                self.assertFalse(dst.exists())
            self.assertEqual(ID3(str(src))['USLT::chi'].text, chinese_elrc)

    def test_04_multiple_unrelated_language_tracks_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp)/'s.mp3', Path(tmp)/'o.mp3'
            _create_minimal_mp3(str(src))
            _tag_mp3(src, USLT(encoding=3,lang='eng',desc='',text='old English'),
                     USLT(encoding=3,lang='chi',desc='',text='中文'),
                     USLT(encoding=3,lang='jpn',desc='',text='日本語'))
            write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            after=ID3(str(dst))
            self.assertEqual(after['USLT::eng'].text,_TEST_ELRC)
            self.assertEqual(after['USLT::chi'].text,'中文')
            self.assertEqual(after['USLT::jpn'].text,'日本語')

    def test_05_mvhd_timescale_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a.m4a';shutil.copy2(MEDIA/'fixture.m4a',path)
            before=path.read_bytes(); digest=mp4_audio_digest(str(path))
            self.assertEqual(digest[0], 'VERIFIED')
            def mutate(q):
                a=MP4(q);a['\xa9nam']=['changed title'];a.save()
                _mvhd_timescale_doubled(Path(q))
                return True
            self.assertFalse(safe_save_audio_tags(str(path),mutate))
            self.assertEqual(path.read_bytes(),before)

    def test_06_mvhd_duration_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a.m4a';shutil.copy2(MEDIA/'fixture.m4a',path)
            before=path.read_bytes()
            def mutate(q):
                a=MP4(q);a['\xa9nam']=['changed title'];a.save()
                raw=bytearray(Path(q).read_bytes());mark=raw.index(b'mvhd')
                at=mark+4+(24 if raw[mark+4] == 1 else 16)
                raw[at]^=1;Path(q).write_bytes(raw)
                return True
            self.assertFalse(safe_save_audio_tags(str(path),mutate))
            self.assertEqual(path.read_bytes(),before)

    def test_07_normal_mp4_tags_do_not_change_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'a.m4a';shutil.copy2(MEDIA/'fixture.m4a',path)
            digest=mp4_audio_digest(str(path))
            self.assertEqual(digest[0], 'VERIFIED')
            self.assertTrue(update_audio_tags(str(path), lyrics='NEW VALUE',title='NEW TITLE'))
            self.assertEqual(mp4_audio_digest(str(path)),digest)

    def test_08_flac_cover_description_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.flac',Path(tmp)/'o.flac'
            _create_minimal_flac(str(src));a=FLAC(str(src));a.add_picture(_cover());a.save()
            native=FLAC.save
            def tamper(obj,*aa,**kw):
                out=native(obj,*aa,**kw)
                another=FLAC(obj.filename)
                another.pictures[0].desc='MUTATED DESC'
                native(another)
                return out
            with patch.object(FLAC,'save',tamper):
                with self.assertRaises((RuntimeError,ValueError)):
                    write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertFalse(dst.exists())
            self.assertEqual(FLAC(str(src)).pictures[0].desc,'ORIGINAL DESCRIPTION')

    def test_09_flac_cover_dimensions_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.flac',Path(tmp)/'o.flac'
            _create_minimal_flac(str(src));a=FLAC(str(src));a.add_picture(_cover());a.save()
            native=FLAC.save
            def tamper(obj,*aa,**kw):
                out=native(obj,*aa,**kw)
                changed=FLAC(obj.filename);changed.pictures[0].width=999
                native(changed)
                return out
            with patch.object(FLAC,'save',tamper):
                with self.assertRaises((RuntimeError,ValueError)):
                    write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertFalse(dst.exists())

    def test_10_flac_normal_cover_survives_tag_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.flac',Path(tmp)/'o.flac'
            _create_minimal_flac(str(src));a=FLAC(str(src));a.add_picture(_cover());a.save()
            before=read_container_tags(str(src))['cover_digest']
            write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertEqual(read_container_tags(str(dst))['cover_digest'],before)

    def test_11_wav_fact_sample_count_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.wav',Path(tmp)/'o.wav'
            _create_minimal_wav(str(src))
            _inject_wav_chunk(src,b'fact',struct.pack('<I',10))
            native=WAVE.save
            def tamper(obj,*aa,**kw):
                out=native(obj,*aa,**kw)
                p=Path(obj.filename);raw=bytearray(p.read_bytes())
                loc=raw.index(b'fact')+8
                raw[loc:loc+4]=struct.pack('<I',777)
                p.write_bytes(raw)
                return out
            with patch.object(WAVE,'save',tamper):
                with self.assertRaises((RuntimeError,ValueError)):
                    write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertFalse(dst.exists())

    def test_12_wav_list_metadata_corruption_must_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.wav',Path(tmp)/'o.wav'
            _create_minimal_wav(str(src))
            _inject_wav_chunk(src,b'LIST',b'INFO' + b'INAM' + struct.pack('<I',4) + b'TEST')
            native=WAVE.save
            def tamper(obj,*aa,**kw):
                out=native(obj,*aa,**kw)
                p=Path(obj.filename);raw=bytearray(p.read_bytes());loc=raw.index(b'TEST')
                raw[loc:loc+4]=b'LOST';p.write_bytes(raw)
                return out
            with patch.object(WAVE,'save',tamper):
                with self.assertRaises((RuntimeError,ValueError)):
                    write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertFalse(dst.exists())

    def test_13_wav_normal_id3_write_with_fact_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            src,dst=Path(tmp)/'s.wav',Path(tmp)/'o.wav'
            _create_minimal_wav(str(src))
            _inject_wav_chunk(src,b'fact',struct.pack('<I',10))
            _inject_wav_chunk(src,b'LIST',b'INFO' + b'INAM' + struct.pack('<I',4) + b'TEST')
            original=compute_audio_stream_digest(str(src))
            self.assertEqual(original[0],'VERIFIED')
            write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertEqual(compute_audio_stream_digest(str(dst)),original)
            self.assertEqual(WAVE(str(dst)).tags['USLT::eng'].text,_TEST_ELRC)


def _wrap(name):
    def case():
        obj=R13Safety()
        try:
            getattr(obj,name)()
        except unittest.SkipTest as e:
            skip(str(e))
    case.__name__=name
    return case

for _name in dir(R13Safety):
    if _name.startswith('test_'):
        globals()[_name]=_wrap(_name)

if __name__ == '__main__':
    sys.exit(run(globals()))
