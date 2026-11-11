# -*- coding: utf-8 -*-
"""Independent release-blocker regression cases, added after the v1.8.4 review.
All data and private markers are wholly synthetic; no network access.
"""
from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from mutagen.flac import FLAC
from mutagen.id3 import ID3, USLT, SYLT, TXXX
from mutagen.wave import WAVE
from core.adapters.container_tag_adapter import write_verified_standardized_container, read_container_tags
from scripts.standardize_song import standardize_single_song
from tests.test_standard_container_tags import (
    _create_minimal_flac, _create_minimal_mp3, _create_minimal_wav, _TEST_ELRC, _TEST_TTML
)
from tests._testkit import run
SECRET = 'SYNTHETIC_PRIVATE_SECONDARY_LYRIC_UNIQUE_8496'

class IndependentReleaseBlockers(unittest.TestCase):
    def test_01_flac_report_does_not_export_raw_secondary_lyrics(self):
        with tempfile.TemporaryDirectory() as td:
            src, out, rep = Path(td)/'song.flac', Path(td)/'out', Path(td)/'audit.json'
            _create_minimal_flac(str(src))
            f = FLAC(str(src)); f['lyrics']=[_TEST_ELRC,SECRET];f['ttmllyric']=[_TEST_TTML];f.save()
            r = standardize_single_song(str(src),mode='standardize',output_dir=str(out),write_tags=True,report_path=str(rep))
            self.assertEqual(r['status'],'PASS',r)
            report = rep.read_text(encoding='utf-8')
            self.assertNotIn(SECRET,report, 'Raw secondary lyric leaked to report')
            self.assertNotIn(SECRET,json.dumps(r,ensure_ascii=False), 'Raw secondary lyric leaked to returned result')

    def test_02_mp3_report_is_json_serializable_with_secondary_uslt(self):
        with tempfile.TemporaryDirectory() as td:
            src, out, rep = Path(td)/'song.mp3', Path(td)/'out', Path(td)/'audit.json'
            _create_minimal_mp3(str(src))
            f=ID3(str(src));f.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
            f.add(USLT(encoding=3,lang='chi',desc='Translation',text=SECRET))
            f.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[_TEST_TTML]));f.save(str(src))
            r=standardize_single_song(str(src),mode='standardize',output_dir=str(out),write_tags=True,report_path=str(rep))
            self.assertEqual(r['status'],'PASS',r)
            self.assertTrue(rep.is_file())
            self.assertNotIn(SECRET,rep.read_text(encoding='utf-8'))

    def test_03_id3_txxx_multivalue_cannot_disappear_after_pass(self):
        with tempfile.TemporaryDirectory() as td:
            src,dst=Path(td)/'song.mp3',Path(td)/'output.mp3';_create_minimal_mp3(str(src))
            alt=_TEST_TTML.replace('Hello','SECOND_VALUE_MUST_SURVIVE');self.assertNotEqual(alt,_TEST_TTML)
            f=ID3(str(src));f.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
            f.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[_TEST_TTML,alt]));f.save(str(src),v2_version=4)
            self.assertEqual(len(ID3(str(src)).getall('TXXX:TTMLLYRIC')[0].text),2)
            try:
                r=write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            except (ValueError, RuntimeError):
                self.assertFalse(dst.exists(), 'Rejected write left output')
            else:
                self.assertEqual(r['status'],'PASS')
                self.assertIn(alt,ID3(str(dst)).getall('TXXX:TTMLLYRIC')[0].text,
                              'Additional TXXX value lost in successful output')

    def test_04_sylt_type_changed_during_write_must_abort(self):
        with tempfile.TemporaryDirectory() as td:
            src,dst=Path(td)/'song.mp3',Path(td)/'output.mp3';_create_minimal_mp3(str(src))
            f=ID3(str(src));f.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
            f.add(SYLT(encoding=3,lang='eng',desc='Synced',format=2,type=1,text=[('one',100),('two',500)]));f.save(str(src))
            original=ID3.save
            def tamper(self,*a,**kw):
                tracks=self.getall('SYLT')
                if tracks:tracks[0].type=2
                return original(self,*a,**kw)
            with patch.object(ID3,'save',tamper):
                with self.assertRaises((ValueError, RuntimeError),msg='Modified SYLT attributes accepted'):
                    write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            self.assertFalse(dst.exists(),'Tampered output was published')

    def test_05_described_translation_only_must_never_be_removed(self):
        with tempfile.TemporaryDirectory() as td:
            src,dst=Path(td)/'song.mp3',Path(td)/'output.mp3';_create_minimal_mp3(str(src))
            f=ID3(str(src));f.add(USLT(encoding=3,lang='chi',desc='Translation',text=SECRET));f.save(str(src))
            try:
                r=write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            except (ValueError, RuntimeError):
                self.assertFalse(dst.exists())
            else:
                self.assertEqual(r['status'],'PASS')
                self.assertTrue(any(x.desc=='Translation' and x.text==SECRET for x in ID3(str(dst)).getall('USLT')),
                                'Translation-only USLT was misclassified as main and erased')

    def test_06_flac_distinct_secondary_values_should_remain_opaque(self):
        with tempfile.TemporaryDirectory() as td:
            src,out=Path(td)/'song.flac',Path(td)/'out';_create_minimal_flac(str(src))
            f=FLAC(str(src));f['lyrics']=[_TEST_ELRC,'Chinese translation','French translation'];f['ttmllyric']=[_TEST_TTML];f.save()
            r=standardize_single_song(str(src),mode='standardize',output_dir=str(out),write_tags=True)
            self.assertFalse(any(x['code']=='SECONDARY_LYRICS_CONFLICT' for x in r['issues']),r)
            self.assertEqual(r['status'],'PASS',r)
            self.assertEqual(FLAC(str(out/src.name))['lyrics'][1:],['Chinese translation','French translation'])

    def test_07_unparsed_lyrics_should_not_echo_verbatim_into_public_report(self):
        with tempfile.TemporaryDirectory() as td:
            src,rep=Path(td)/'song.flac',Path(td)/'audit.json';_create_minimal_flac(str(src))
            src.with_suffix('.lrc').write_text('malformed song row '+SECRET,encoding='utf-8')
            r=standardize_single_song(str(src),mode='audit',report_path=str(rep))
            self.assertNotEqual(r['status'],'PASS')
            self.assertNotIn(SECRET,rep.read_text(encoding='utf-8'))


def test_01_flac_report_does_not_export_raw_secondary_lyrics():
    IndependentReleaseBlockers("test_01_flac_report_does_not_export_raw_secondary_lyrics").test_01_flac_report_does_not_export_raw_secondary_lyrics()

def test_02_mp3_report_is_json_serializable_with_secondary_uslt():
    IndependentReleaseBlockers("test_02_mp3_report_is_json_serializable_with_secondary_uslt").test_02_mp3_report_is_json_serializable_with_secondary_uslt()

def test_03_id3_txxx_multivalue_cannot_disappear_after_pass():
    IndependentReleaseBlockers("test_03_id3_txxx_multivalue_cannot_disappear_after_pass").test_03_id3_txxx_multivalue_cannot_disappear_after_pass()

def test_04_sylt_type_changed_during_write_must_abort():
    IndependentReleaseBlockers("test_04_sylt_type_changed_during_write_must_abort").test_04_sylt_type_changed_during_write_must_abort()

def test_05_described_translation_only_must_never_be_removed():
    IndependentReleaseBlockers("test_05_described_translation_only_must_never_be_removed").test_05_described_translation_only_must_never_be_removed()

def test_06_flac_distinct_secondary_values_should_remain_opaque():
    IndependentReleaseBlockers("test_06_flac_distinct_secondary_values_should_remain_opaque").test_06_flac_distinct_secondary_values_should_remain_opaque()

def test_07_unparsed_lyrics_should_not_echo_verbatim_into_public_report():
    IndependentReleaseBlockers("test_07_unparsed_lyrics_should_not_echo_verbatim_into_public_report").test_07_unparsed_lyrics_should_not_echo_verbatim_into_public_report()

def test_08_wav_report_serializable_and_secondary_uslt_preserved():
    with tempfile.TemporaryDirectory() as td:
        src,out,rep=Path(td)/'song.wav',Path(td)/'out',Path(td)/'report.json'
        _create_minimal_wav(str(src))
        w=WAVE(str(src))
        if w.tags is None:w.add_tags()
        w.tags.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
        w.tags.add(USLT(encoding=3,lang='chi',desc='Translation',text=SECRET))
        w.tags.add(SYLT(encoding=3,lang='eng',desc='sync',format=2,type=1,text=[('X',100)]))
        w.tags.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[_TEST_TTML]))
        w.save()
        r=standardize_single_song(str(src),mode='standardize',output_dir=str(out),write_tags=True,report_path=str(rep))
        assert r['status']=='PASS',r
        assert SECRET not in rep.read_text('utf-8')
        assert any(x.desc=='Translation' and x.text==SECRET
                   for x in WAVE(str(out/src.name)).tags.getall('USLT'))


def test_09_id3_description_and_key_never_leak_in_inventory():
    with tempfile.TemporaryDirectory() as td:
        src,rep=Path(td)/'song.mp3',Path(td)/'audit.json';_create_minimal_mp3(str(src))
        f=ID3(str(src));f.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
        f.add(USLT(encoding=3,lang='chi',desc='PrivateDescription'+SECRET,text='translated'))
        f.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[_TEST_TTML]));f.save(str(src))
        result=standardize_single_song(str(src),mode='audit',report_path=str(rep))
        assert result['status']=='PASS',result
        assert SECRET not in json.dumps(result,ensure_ascii=False)
        assert SECRET not in rep.read_text('utf-8')


def test_10_verified_container_result_is_public_dto():
    with tempfile.TemporaryDirectory() as td:
        src,dst=Path(td)/'src.flac',Path(td)/'dest.flac';_create_minimal_flac(str(src))
        f=FLAC(str(src));f['lyrics']=[_TEST_ELRC,SECRET];f['ttmllyric']=[_TEST_TTML];f.save()
        result=write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
        assert result['status']=='PASS'
        assert SECRET not in json.dumps(result,ensure_ascii=False)
        assert '_extra_lyrics_raw' not in result['readback_tags']


def test_11_txxx_secondary_value_tamper_fail_closed():
    with tempfile.TemporaryDirectory() as td:
        src,dst=Path(td)/'src.mp3',Path(td)/'dest.mp3';_create_minimal_mp3(str(src))
        alt=_TEST_TTML.replace('Hello','SYNTHETIC_ALTERNATE_TTML')
        assert alt!=_TEST_TTML
        f=ID3(str(src));f.add(USLT(encoding=3,lang='eng',desc='',text=_TEST_ELRC))
        f.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[_TEST_TTML,alt]));f.save(str(src))
        old=ID3.save
        def tamper(self,*a,**kw):
            fr=self.get('TXXX:TTMLLYRIC')
            if fr and len(fr.text)>1:fr.text[1]='CORRUPTED'
            return old(self,*a,**kw)
        with patch.object(ID3,'save',tamper):
            try:write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            except RuntimeError as exc:assert 'PROTECTED_METADATA_CHANGED' in str(exc),exc
            else:raise AssertionError('TXXX secondary value tampering not blocked')
        assert not dst.exists()


if __name__ == '__main__':
    sys.exit(run(globals()))
