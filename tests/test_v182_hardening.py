# -*- coding: utf-8 -*-
"""Independent v1.8.2 safety regression: known review P0/P1 failure paths.
All audio and names are synthetic. No network access or private library use.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tests._testkit import run
from tests.test_standard_cli import _create_minimal_flac
from tests.test_standard_container_tags import _TEST_ELRC,_TEST_TTML,_create_minimal_mp3,_create_minimal_wav
from mutagen.flac import FLAC
from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.models.canonical_lyrics import CanonicalLyrics,CanonicalLine,CanonicalWord,LineRole
from core.schedulers.prelude_scheduler import plan_prelude_ab
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from core.auditors.elrc_ttml_comparator import compare_elrc_ttml,verify_canonical_exports,parse_ttml_structural
from core.adapters.container_tag_adapter import read_container_tags,write_verified_standardized_container
from core.parsers.ttml_parser import parse_ttml
from scripts.standardize_song import standardize_single_song


def _word(t,s,e,join=None):return {'text':t,'start':s,'end':e,'join':join}
def _line(t,s,e,words=True):return {'text':t,'start':s,'end':e,
                                   'words':[_word(t,s,e)] if words else []}
def _canonical(*lines):
    c=CanonicalLyrics(song={'title':'虚构测试曲','language':'en-US'})
    c.lines=list(lines)
    plan_prelude_ab(c)
    return c

def _elrc_ttml(c):
    e=render_elrc_v182(c);t=render_ttml_v182(c)
    return e,t,verify_canonical_exports(c,e,t)

def _sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def test_01_legacy_space_join_roundtrip():
    c=_canonical(CanonicalLine('L1',0,LineRole.SINGING,12000,14000,'Hello world',
                  [CanonicalWord('Hello',12000,13000,join='space'),CanonicalWord('world',13000,14000)]))
    e,t,r=_elrc_ttml(c)
    assert r.passed,[(x.code,x.message) for x in r.issues]
    assert '<00:13.000> world' in e
    assert '>Hello</span> <span' in t
    assert 'Hello world' in parse_ttml_structural(t)[0][-1].text

def test_02_new_space_join_roundtrip():
    c=_canonical(CanonicalLine('L1',0,LineRole.SINGING,12000,14000,'Hello world',
                  [CanonicalWord('Hello',12000,13000),CanonicalWord('world',13000,14000,join=' ')]))
    assert _elrc_ttml(c)[2].passed

def test_03_gap_end_not_inferred_as_next_start():
    c=_canonical(CanonicalLine('L1',0,LineRole.SINGING,12000,16000,'你好',
                  [CanonicalWord('你',12000,13000),CanonicalWord('好',15000,16000)]))
    e,t,r=_elrc_ttml(c)
    assert '<00:13.000><00:15.000>' in e
    assert r.passed,[(x.code,x.message) for x in r.issues]

def test_04_standalone_cue_survives_both_formats():
    raw={'lines':[_line('男：',1000,2000,False),_line('真实唱词',12000,15000)]}
    c=classify_and_clean_all_lines(raw,song_title='虚构测试曲')
    plan_prelude_ab(c)
    e,t,r=_elrc_ttml(c)
    assert '男：' in e and '男：' in t and r.passed

def test_05_original_singer_and_sample_preserved():
    raw={'lines':[_line('原唱：虚构歌手',500,900,False),_line('真实唱词',10000,12000),
                  _line('采样来源：虚构广播',14000,17000,False)]}
    c=classify_and_clean_all_lines(raw,song_title='虚构测试曲')
    p=plan_prelude_ab(c)
    e,t,r=_elrc_ttml(c)
    assert p.t_sing_ms==10000 and r.passed
    assert '原唱：虚构歌手' in t and '采样来源：虚构广播' in t

def test_06_simultaneous_duet_never_translation():
    raw={'lines':[_line('甲唱：你好',10000,12000),_line('乙唱：你好',10000,12000,False)]}
    c=classify_and_clean_all_lines(raw)
    assert len(c.lines)==2 and not any(x.translation for x in c.lines)

def test_07_sung_credit_word_not_deleted():
    raw={'lines':[_line('鸣谢：在我孤独的那些日子',10000,12000),
                  _line('当我说作曲：你会不会记得',12000,14000)]}
    c=classify_and_clean_all_lines(raw)
    assert len(c.lines)==2 and not c.song.get('extracted_composer')

def test_08_outro_credit_and_next_singing():
    raw={'lines':[_line('真实歌词',12000,14000),_line('作词：合成甲',15000,16000,False),
                  _line('编曲：合成乙',17000,18000,False),_line('后续演唱',19000,21000)]}
    c=classify_and_clean_all_lines(raw)
    assert [x.text for x in c.lines]==['真实歌词','后续演唱']
    assert c.song['extracted_lyricist']=='合成甲'
    assert plan_prelude_ab(c).t_sing_ms==12000

def test_09_source_attribution_parser_to_renderer():
    from core.parsers.lrc_parser import parse_lrc
    raw='[re:lrc.red (https://lrc.red)]\n[00:12.000]<00:12.000>你好<00:13.000>\n'
    c=classify_and_clean_all_lines(parse_lrc(raw),song_title='虚构测试曲')
    plan_prelude_ab(c)
    e,t,r=_elrc_ttml(c)
    assert '[re:lrc.red (https://lrc.red)]' in e and r.passed

def test_10_head_translation_readable_in_legacy_parser():
    out=parse_ttml(_TEST_TTML)
    assert [x['translation'] for x in out['lines']]==['你好世界']

def test_11_head_translation_namespace_is_not_ttml():
    c=_canonical(CanonicalLine('L1',0,LineRole.SINGING,12000,14000,'Hello',
                  [CanonicalWord('Hello',12000,14000)],translation='你好'))
    r=ET.fromstring(render_ttml_v182(c))
    parent=r.find('.//{http://music.apple.com/lyric-ttml-internal}iTunesMetadata')
    assert parent is not None and parent[0].tag=='{http://music.apple.com/lyric-ttml-internal}translations'
    assert parent[0][0][0].tag=='{http://music.apple.com/lyric-ttml-internal}text'

def test_12_ttml_only_head_translated_output():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/src.flac';_create_minimal_flac(src)
        f=FLAC(src);f['ttmllyric']=[_TEST_TTML];f.save()
        r=standardize_single_song(src,'standardize',output_dir=td+'/out',no_tags=True)
        assert r['status']=='PASS',r
        assert '你好世界' in Path(td+'/out/src.lrc').read_text(encoding='utf-8')

def test_13_ttml_hanging_translation_blocks():
    corrupted=_TEST_TTML.replace('for="L1"','for="L999"')
    r=parse_ttml_structural(corrupted)
    assert 'TRANSLATION_HANGING_REFERENCE' in [e.code for e in r[2]]

def test_14_both_outputs_simultaneously_corrupted_canonical_detects():
    c=_canonical(CanonicalLine('L1',0,LineRole.SINGING,12000,14000,'Hello world',
                [CanonicalWord('Hello',12000,13000,join='space'),CanonicalWord('world',13000,14000)]))
    # Mutating only the canonical expected text makes both independent exports
    # syntactically equal, but no longer equal to the original source truth.
    e,t,_=_elrc_ttml(c)
    c.lines[0].text='Helloworld'
    result=verify_canonical_exports(c,e,t)
    assert not result.passed and any(x.code=='CANONICAL_TEXT_LOSS' for x in result.issues)

def test_15_role_tamper_detected_by_canonical():
    c=_canonical(CanonicalLine('L1',0,LineRole.PERFORMER_CUE,1000,2000,'男：'))
    e,t,_=_elrc_ttml(c)
    bad=t.replace('itunes:role="PERFORMER_CUE"','itunes:role="SINGING"')
    r=verify_canonical_exports(c,e,bad)
    assert not r.passed and any(x.code=='CANONICAL_ROLE_MISMATCH' for x in r.issues)

def test_16_target_existing_rejected_unchanged():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';dst=td+'/dest.flac';_create_minimal_flac(src);_create_minimal_flac(dst)
        before=_sha(dst)
        try:write_verified_standardized_container(src,dst,_TEST_ELRC,_TEST_TTML)
        except FileExistsError:pass
        else:raise AssertionError('Should reject occupied target')
        assert _sha(dst)==before

def test_17_existing_credit_conflict_blocks_no_target():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';dst=td+'/out.flac';_create_minimal_flac(src)
        f=FLAC(src);f['composer']=['已核实原曲作者'];f.save()
        try:write_verified_standardized_container(src,dst,_TEST_ELRC,_TEST_TTML,composer='未经核实的新作者')
        except ValueError as e:assert 'CREDIT_CONFLICT' in str(e)
        else:raise AssertionError('Conflict must block')
        assert not os.path.lexists(dst) and FLAC(src)['composer'][0]=='已核实原曲作者'

def test_18_in_place_rejected_before_sidecar_writes():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';side=td+'/a.lrc';_create_minimal_flac(src);Path(side).write_text('SENTINEL')
        before=_sha(src)
        try:standardize_single_song(src,'standardize',write_tags=True,in_place=True)
        except ValueError as e:assert 'IN_PLACE_DISABLED' in str(e)
        else:raise AssertionError('Must reject')
        assert _sha(src)==before and Path(side).read_text()=='SENTINEL'

def test_19_prior_sidecar_not_overwritten():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        out=Path(td+'/out');out.mkdir();(out/'a.lrc').write_text('BEFORE')
        r=standardize_single_song(src,'standardize',output_dir=str(out),no_tags=True)
        assert r['status']=='BLOCKED',r
        assert (out/'a.lrc').read_text()=='BEFORE' and not (out/'a.spl').exists()

def test_20_sidecar_commit_failure_rolls_back_new_only():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src);out=Path(td+'/out')
        original_link=os.link
        counts=[0]
        def crash(src,dst,*args,**kwargs):
            counts[0]+=1
            if counts[0]==2:raise OSError('SYNTHETIC_DISK_INTERRUPT')
            return original_link(src,dst,*args,**kwargs)
        with mock.patch('scripts.standardize_song.os.link',side_effect=crash):
            r=standardize_single_song(src,'standardize',output_dir=str(out),no_tags=True)
        assert r['status']=='BLOCKED',r
        assert not list(out.glob('a.*'))

def test_21_previously_existing_output_audio_untouched():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        dest=Path(td+'/out');dest.mkdir();_create_minimal_flac(str(dest/'a.flac'))
        before=_sha(dest/'a.flac')
        r=standardize_single_song(src,'standardize',output_dir=str(dest),write_tags=True)
        assert r['status']=='BLOCKED' and _sha(dest/'a.flac')==before
        assert not (dest/'a.lrc').exists()

def test_22_one_format_audit_never_claims_consistency():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        r=standardize_single_song(src,'audit')
        assert r['status']=='ONE_FORMAT_ONLY' and r['baseline_comparison'] is None

def test_23_reference_performer_cue_actual_effect():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        ref=Path(td+'/cue.lrc');ref.write_text('[00:01.000]<00:01.000>男：Hello<00:02.000> world<00:03.000>\n', encoding='utf-8')
        r=standardize_single_song(src,'standardize',output_dir=td+'/out',ref_lyrics_path=str(ref))
        assert r['status']=='PASS',r
        assert '男：' in Path(td+'/out/a.lrc').read_text(encoding='utf-8')

def test_24_ambiguous_line_blocks_write():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        # Use sidecar-only unknown cue to force REVIEW_REQUIRED.
        FLAC(src)['lyrics']=[_TEST_ELRC]  # copy not saved; inject via actual save
        f=FLAC(src);f['lyrics']=['[00:00.000]和声：某人\n'+_TEST_ELRC];f.save()
        r=standardize_single_song(src,'standardize',output_dir=td+'/out')
        assert r['status']=='REVIEW_REQUIRED' and not Path(td+'/out').exists()

def test_25_very_short_prelude_merges_slots():
    c=CanonicalLyrics(song={'title':'短前奏','extracted_lyricist':'A','extracted_composer':'B'},
        lines=[CanonicalLine('L1',0,LineRole.SINGING,1,101,'起', [CanonicalWord('起',1,101)])])
    p=plan_prelude_ab(c)
    assert p.title_window==(0,1) and p.display_lyricist is None and p.display_composer is None
    e,t,r=_elrc_ttml(c)
    assert r.passed and '词：A' in e and '曲：B' in e

def test_26_dtd_entity_rejected():
    r=parse_ttml_structural('<!DOCTYPE foo [<!ENTITY x "EXPAND">]><tt>&x;</tt>')
    assert any(x.code=='XML_UNSAFE_INPUT' for x in r[2])



def test_27_line_only_singing_not_promoted_to_fake_word():
    c=CanonicalLyrics(song={'title':'T'},lines=[CanonicalLine('L1',0,LineRole.SINGING,1000,4000,'没有逐字')])
    for render in (render_elrc_v182,render_ttml_v182):
        try:render(c)
        except ValueError as exc:assert 'SINGING_WORDS_ABSENT' in str(exc)
        else:raise AssertionError('Line-only singing must never be fake word aligned')


def test_28_report_must_not_overwrite_source_audio_or_sidecar():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';side=td+'/a.lrc';_create_minimal_flac(src)
        Path(side).write_text('PRESERVE_LRC')
        h=_sha(src)
        for rp in (src,side):
            try:standardize_single_song(src,'audit',report_path=rp)
            except ValueError as e: assert 'REPORT_TARGET_PROTECTED' in str(e)
            else:raise AssertionError('Protected report target overwrite')
        assert _sha(src)==h and Path(side).read_text()=='PRESERVE_LRC'


def test_29_generated_container_can_be_audited_then_restandardized():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        r1=standardize_single_song(src,'standardize',output_dir=td+'/round1',write_tags=True)
        assert r1['status']=='PASS',r1
        emitted=td+'/round1/a.flac'
        check=standardize_single_song(emitted,'audit')
        assert check['status']=='PASS' and check['format_audit']=='CONSISTENT',check
        r2=standardize_single_song(emitted,'standardize',output_dir=td+'/round2',no_tags=True)
        assert r2['status']=='PASS',r2
        assert '[00:00.' in Path(td+'/round2/a.lrc').read_text(encoding='utf-8')


def test_30_sung_staff_keyword_causes_review_not_auto_drop():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        f=FLAC(src);f['lyrics']=['[00:01.000]<00:01.000>鸣谢：这其实是唱词<00:03.000>\n'];f.save()
        r=standardize_single_song(src,'standardize',output_dir=td+'/out')
        assert r['status']=='REVIEW_REQUIRED',r
        assert any(x['code']=='CREDIT_LIKE_SUNG_TEXT_RETAINED' for x in r['issues'])
        assert not Path(td+'/out').exists()


def test_31_reference_with_mismatched_title_is_blocked():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        ref=Path(td+'/bad_ref.lrc');ref.write_text('[ti:另一首完全不同的歌]\n[00:01.000]<00:01.000>男：Hello<00:02.000> world<00:03.000>\n', encoding='utf-8')
        r=standardize_single_song(src,'standardize',output_dir=td+'/out',ref_lyrics_path=str(ref))
        assert r['status']=='REVIEW_REQUIRED'
        assert any(x['code']=='REFERENCE_SONG_MISMATCH' for x in r['issues'])
        assert not Path(td+'/out').exists()



def test_32_verified_artist_name_cue_retained():
    raw={'lines':[_line('示例主唱：',1000,2000,False),
                  _line('示例主唱：这一段是歌声',12000,15000)]}
    c=classify_and_clean_all_lines(raw,song_title='虚构测试曲',song_artist='示例主唱')
    assert c.lines[0].role==LineRole.PERFORMER_CUE
    assert c.lines[1].speaker_prefix=='示例主唱'
    plan_prelude_ab(c)
    e,t,r=_elrc_ttml(c)
    assert r.passed and '示例主唱：' in e and '示例主唱：' in t


def test_33_existing_verified_credits_drive_prelude_when_lyrics_lack_them():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        f=FLAC(src);f['lyricist']=['已核实词'];f['composer']=['已核实曲'];f.save()
        result=standardize_single_song(src,'standardize',output_dir=td+'/out',write_tags=True)
        assert result['status']=='PASS',result
        assert result['prelude_plan']['display_lyricist']=='词：已核实词'
        assert result['prelude_plan']['display_composer']=='曲：已核实曲'
        assert FLAC(td+'/out/a.flac')['lyricist'][0]=='已核实词'


def test_34_embedded_credit_conflict_blocks_before_any_write():
    with tempfile.TemporaryDirectory() as td:
        src=td+'/a.flac';_create_minimal_flac(src)
        f=FLAC(src);f['lyricist']=['已核实词'];f['lyrics']=[
            '[00:00.100]作词：未经核实词\n[00:01.000]<00:01.000>Hello<00:02.000> world<00:03.000>\n']
        f.save()
        r=standardize_single_song(src,'standardize',output_dir=td+'/out',no_tags=True)
        assert r['status']=='REVIEW_REQUIRED',r
        assert any(x['code']=='EXISTING_CREDIT_CONFLICT' for x in r['issues'])
        assert not Path(td+'/out').exists()



def test_35_report_never_corrupts_new_lrc():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); src = root / 'song.flac'; _create_minimal_flac(str(src))
        out = root / 'out'; report = out / 'song.lrc'
        try:
            result = standardize_single_song(str(src), 'standardize', output_dir=str(out), no_tags=True, report_path=str(report))
        except (ValueError, FileExistsError):
            return
        assert result['status'] != 'PASS', 'Report collision must not report PASS'
        if report.exists():
            assert report.read_bytes() == (out / 'song.spl').read_bytes(), 'LRC corrupted'


def test_36_report_never_corrupts_new_audio_copy():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); src = root / 'song.flac'; _create_minimal_flac(str(src)); out = root / 'out'
        try:
            result = standardize_single_song(str(src), 'standardize', output_dir=str(out), write_tags=True, report_path=str(out / 'song.flac'))
        except (ValueError, FileExistsError):
            return
        assert result['status'] != 'PASS', 'JSON report overwritten audio but function returned PASS'
        if (out / 'song.flac').exists():
            assert (out / 'song.flac').read_bytes().startswith(b'fLaC')


def test_37_audit_report_must_not_overwrite_reference():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d); src = root / 'song.flac'; _create_minimal_flac(str(src))
        reference = root / 'reference.lrc'
        reference.write_text('[00:02.000] synthetic reference', encoding='utf-8')
        before = hashlib.sha256(reference.read_bytes()).digest()
        try:
            standardize_single_song(str(src), 'audit', report_path=str(reference), ref_lyrics_path=str(reference))
        except (ValueError, FileExistsError):
            return
        assert before == hashlib.sha256(reference.read_bytes()).digest(), 'Readonly reference was overwritten'


def test_38_sung_title_with_verbatim_words_is_not_removed():
    raw = {'lines': [{'start': 15000, 'end': 17000, 'text': '测试曲目',
                      'words': [{'start': 15000, 'end': 17000, 'text': '测试曲目'}]},
                     {'start': 18000, 'end': 20000, 'text': '第二句演唱',
                      'words': [{'start': 18000, 'end': 20000, 'text': '第二句演唱'}]}]}
    canonical = classify_and_clean_all_lines(raw, song_title='测试曲目', song_artist='虚构歌手')
    plan = plan_prelude_ab(canonical)
    assert plan.t_sing_ms == 15000, 'First actual singing title was mistaken for display-only prelude'
    assert any(x.text == '测试曲目' and x.is_singing for x in canonical.lines)


def test_39_ambiguous_bilingual_simultaneous_vocals_not_assumed_translation():
    raw = {'lines': [{'start': 12000, 'end': 15000, 'text': 'Hello',
                      'words': [{'start': 12000, 'end': 15000, 'text': 'Hello'}]},
                     {'start': 12000, 'end': 15000, 'text': '你好', 'words': []}]}
    c = classify_and_clean_all_lines(raw, song_title='TEST SONG')
    is_blocked = any(x.blocking or x.severity.value == 'REVIEW_REQUIRED' for x in c.issues)
    assert is_blocked or len([x for x in c.lines if x.is_singing]) == 2, 'Same-timestamp English and Chinese vocals silently treated as translation'

if __name__=='__main__':sys.exit(run(globals()))
