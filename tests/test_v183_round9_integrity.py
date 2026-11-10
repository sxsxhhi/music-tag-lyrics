# -*- coding: utf-8 -*-
"""Post-audit R9 strict-input and multi-source reconciliation regression.

All inputs are synthetic and temporary. No network, real music or private path.
Every case checks a concrete behavior that previously evaded the green CI.
"""
from __future__ import annotations
import sys
import tempfile
import wave
from pathlib import Path

REPO_ROOT=Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0,str(REPO_ROOT))

from core.auditors.elrc_ttml_comparator import compare_elrc_ttml,parse_elrc_structural,parse_ttml_structural
from core.parsers.lrc_parser import parse_lrc
from scripts.standardize_song import standardize_single_song
from tests._testkit import run

ELRC='[00:15.000]<00:15.000>唱词<00:17.000>\n'
TTML=('<tt xmlns="http://www.w3.org/ns/ttml" '
      'xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
      '<body><div begin="00:00:15.000" end="00:00:17.000">'
      '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
      '<span begin="00:00:15.000" end="00:00:17.000">唱词</span>'
      '</p></div></body></tt>')


def _wav(path:Path):
    with wave.open(str(path),'wb') as obj:
        obj.setnchannels(1);obj.setsampwidth(2);obj.setframerate(16000)
        obj.writeframes(b'\0\0'*512)


def _audit(*, elrc=None,ttml=None,spl=None,embedded_elrc=None,embedded_ttml=None,mode='audit'):
    from mutagen.wave import WAVE
    from mutagen.id3 import USLT,TXXX
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); song=root/'song.wav';_wav(song)
        if embedded_elrc or embedded_ttml:
            tags=WAVE(str(song));tags.add_tags()
            if embedded_elrc:
                tags.tags.add(USLT(encoding=3,lang='eng',desc='',text=embedded_elrc))
            if embedded_ttml:
                tags.tags.add(TXXX(encoding=3,desc='TTMLLYRIC',text=[embedded_ttml]))
            tags.save()
        for ext, val in (('.lrc',elrc),('.spl',spl),('.ttml',ttml)):
            if val is not None:song.with_suffix(ext).write_text(val,encoding='utf-8')
        dest=root/'out'
        result=standardize_single_song(str(song),mode=mode,output_dir=str(dest),no_tags=True)
        outputs={f.suffix:f.read_text(encoding='utf-8') for f in dest.glob('song.*')} if dest.exists() else {}
        return result, outputs


def _must_block(elrc=None, ttml=TTML, **kwargs):
    report,outputs=_audit(elrc=elrc,ttml=ttml,mode='standardize',**kwargs)
    assert report['status'] != 'PASS',report
    assert not outputs,outputs


def test_01_unmodeled_inline_elrc_text_fails():
    invalid=ELRC.rstrip('\n')+'<bad>额外唱词\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_02_suffix_after_metadata_fails():
    invalid=ELRC+'[ar:歌手]没有时间轴的唱词\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    assert parse_lrc(invalid)['unparsed_lines']
    _must_block(elrc=invalid)


def test_03_unclosed_metadata_fails():
    invalid=ELRC+'[ar:歌手\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_04_embedded_sidecar_conflict_review():
    report,outputs=_audit(elrc=ELRC.replace('唱词','冲突'), embedded_elrc=ELRC,
                          embedded_ttml=TTML,mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert report['source_audit']=='CONFLICT_OR_INVALID'
    assert any(x['code']=='SOURCE_LYRICS_CONFLICT' for x in report['issues'])
    assert not outputs


def test_05_lrc_spl_conflict_review():
    report,outputs=_audit(elrc=ELRC,spl=ELRC.replace('唱词','冲突'),ttml=TTML,mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert any(x['code']=='SOURCE_LYRICS_CONFLICT' for x in report['issues'])
    assert not outputs


def test_06_offset_1000_mismatch_blocks():
    elrc='[offset:1000]\n'+ELRC
    assert parse_lrc(elrc)['lines'][0]['start']==16000
    assert not compare_elrc_ttml(elrc,TTML).passed
    _must_block(elrc=elrc)


def test_07_unknown_p_role_rejected():
    invalid=TTML.replace('<p begin=',
        '<p xmlns:ttm="http://www.w3.org/ns/ttml#metadata" ttm:role="x-unmodeled" begin=')
    assert not compare_elrc_ttml(ELRC,invalid).passed
    _must_block(elrc=ELRC,ttml=invalid)


def test_08_matching_lrc_spl_equivalent_precision_passes():
    report,_=_audit(elrc=ELRC,spl=ELRC.replace('00:15.000','00:15.00').replace('00:17.000','00:17.00'),
                    ttml=TTML)
    assert report['status']=='PASS',report
    assert report['source_audit']=='CONSISTENT'


def test_09_embedded_and_sidecar_same_text_passes():
    report,_=_audit(elrc=ELRC,embedded_elrc=ELRC,embedded_ttml=TTML)
    assert report['status']=='PASS',report
    assert all(x['present'] for k,x in report['source_inventory'].items() if k in ('embedded_elrc','embedded_ttml','sidecar_lrc'))


def test_10_matching_offset_equivalent_time_passes():
    elrc='[offset:-1000]\n'+ELRC.replace('00:15.000','00:16.000').replace('00:17.000','00:18.000')
    assert compare_elrc_ttml(elrc,TTML).passed
    report,_=_audit(elrc=elrc,ttml=TTML)
    assert report['status']=='PASS',report


def test_11_negative_shift_clamping_is_never_consistent():
    invalid='[offset:-1000]\n[00:00.500]<00:00.500>唱词<00:02.000>\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_12_duplicate_offset_rejected():
    invalid='[offset:0]\n[offset:1000]\n'+ELRC
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_13_bad_offset_rejected():
    invalid='[offset:abc]\n'+ELRC
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_14_unknown_metadata_rejected():
    invalid='[alien:cannot_assume_harmless]\n'+ELRC
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_15_source_invalid_even_if_valid_embedded_selected():
    report,outputs=_audit(embedded_elrc=ELRC,embedded_ttml=TTML,
                          elrc=ELRC+'[ar:歌手]隐藏词\n',mode='standardize')
    assert report['status']=='BLOCKED',report
    assert any(x['code']=='SOURCE_LYRICS_INVALID' for x in report['issues'])
    assert not outputs


def test_16_secondary_ttml_conflict_rejected():
    report,outputs=_audit(embedded_elrc=ELRC,embedded_ttml=TTML,
                          ttml=TTML.replace('唱词','另一句'),mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert any(x['code']=='SOURCE_LYRICS_CONFLICT' for x in report['issues'])
    assert not outputs


def test_17_literal_less_than_not_mistaken_for_markup():
    # An isolated less-than operator is ordinary lyric text, not an XML tag.
    src='[00:15.000]<00:15.000>I < 3<00:17.000>\n'
    ttml=TTML.replace('唱词','I &lt; 3')
    assert compare_elrc_ttml(src,ttml).passed


def test_18_malformed_inline_time_rejected():
    invalid='[00:15.000]<00:15.000>唱词<00:17.000><00:XX.000>隐藏词\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_19_unknown_itunes_p_semantic_role_fails():
    ttml=TTML.replace('itunes:key="L1"','itunes:key="L1" itunes:role="x-unknown"')
    lines,_,issues=parse_ttml_structural(ttml)
    assert any(x.code=='UNSUPPORTED_ITUNES_P_ROLE' and x.blocking for x in issues)
    _must_block(elrc=ELRC,ttml=ttml)


def test_20_untrusted_prelude_key_fails():
    ttml=TTML.replace('itunes:key="L1"','itunes:key="P_TITLE" itunes:role="SINGING"')
    _,_,issues=parse_ttml_structural(ttml)
    assert any(x.code=='PRELUDE_MARKER_UNTRUSTED' for x in issues)
    _must_block(elrc=ELRC,ttml=ttml)


def test_21_safe_lrc_red_attribution_is_allowed():
    elrc='[re:lrc.red (https://lrc.red)]\n'+ELRC
    assert compare_elrc_ttml(elrc,TTML).passed
    report,outputs=_audit(elrc=elrc,ttml=TTML,mode='standardize')
    assert report['status']=='PASS',report
    assert '[re:lrc.red (https://lrc.red)]' in outputs['.lrc']


def test_22_same_format_ttml_different_div_boundaries_conflict():
    t2=TTML.replace('begin="00:00:15.000" end="00:00:17.000"><p',
                    'begin="00:00:14.000" end="00:00:17.000"><p')
    report,outputs=_audit(embedded_elrc=ELRC,embedded_ttml=TTML,
                          ttml=t2,mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert not outputs


def test_23_final_untimed_lyric_word_never_gets_fake_500ms():
    invalid='[00:15.000]<00:15.000>唱词<00:17.000>隐藏未打轴唱词\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_24_unmodeled_inline_bracket_timing_not_silently_reinterpreted():
    invalid='[00:15.000]<00:15.000>唱词[00:15.500]<00:17.000>\n'
    assert not compare_elrc_ttml(invalid,TTML).passed
    _must_block(elrc=invalid)


def test_25_differential_canonical_roundtrip_300_synthetic_cases():
    import random
    from core.models.canonical_lyrics import CanonicalLyrics,CanonicalLine,CanonicalWord,LineRole
    from core.renderers.standard_elrc_renderer import render_elrc_v182
    from core.renderers.standard_ttml_renderer import render_ttml_v182
    r=random.Random(91482)
    for iteration in range(300):
        canonical=CanonicalLyrics();canonical.song={'title':'Synthetic'}
        cursor=r.randrange(1000,30000)
        for li in range(r.randint(1,4)):
            tokens=[];full=''
            for wi in range(r.randint(1,6)):
                cursor+=r.randint(0,100)
                start,end=cursor,cursor+r.randint(100,1100)
                word=r.choice(['甲','乙','Hello','world','<3','不','t'])
                join='space' if wi and r.random()<0.4 else 'none'
                tokens.append(CanonicalWord(text=word,start_ms=start,end_ms=end,join=join))
                full+=(' ' if join=='space' else '')+word
                cursor=end
            canonical.lines.append(CanonicalLine('L'+str(li+1),li,LineRole.SINGING,
                tokens[0].start_ms,tokens[-1].end_ms,full,words=tokens))
            cursor+=r.randint(100,2000)
        elrc=render_elrc_v182(canonical);ttml=render_ttml_v182(canonical)
        compare=compare_elrc_ttml(elrc,ttml)
        assert compare.passed,(iteration,compare.issues)


def test_26_differential_mutated_word_text_rejected():
    import random
    r=random.Random(2190)
    for _ in range(150):
        word=''.join(r.choice('甲乙丙丁戊') for x in range(r.randint(1,8)))
        elrc=f'[00:15.000]<00:15.000>{word}<00:17.000>\n'
        ttml=TTML.replace('唱词',word)
        assert compare_elrc_ttml(elrc,ttml).passed
        modified=elrc.replace(word,word+'<bad>注入')
        assert not compare_elrc_ttml(modified,ttml).passed


def test_27_legacy_parser_must_not_invent_speaker_prefix_word_axis():
    # Timed singing words plus a separate untimed singer cue. A permissive
    # legacy parse invents a timed word for the cue; strict intake blocks it.
    elrc='[00:15.000]男：<00:15.000>唱词<00:17.000>\n'
    report,out=_audit(elrc=elrc,mode='standardize')
    assert report['status']=='BLOCKED',report
    assert any(x['code']=='SOURCE_LYRICS_INVALID' for x in report['issues'])
    assert not out


def test_28_declared_spoken_content_role_cannot_be_silently_recast():
    ttml=TTML.replace('itunes:key="L1"','itunes:key="L1" itunes:role="SPOKEN_CONTENT"')
    report,out=_audit(ttml=ttml,mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert any(x['code']=='INPUT_SEMANTIC_ROLE_CHANGED' for x in report['issues'])
    assert not out


def test_29_unverified_multiword_nonsinging_role_cannot_lose_timing():
    ttml=TTML.replace('唱词','采样：样例').replace('itunes:key="L1"','itunes:key="L1" itunes:role="SAMPLE_EXPLANATION"')
    ttml=ttml.replace('<span begin="00:00:15.000" end="00:00:17.000">采样：样例</span>',
        '<span begin="00:00:15.000" end="00:00:16.000">采样：</span>'
        '<span begin="00:00:16.000" end="00:00:17.000">样例</span>')
    report,out=_audit(ttml=ttml,mode='standardize')
    assert report['status']=='REVIEW_REQUIRED',report
    assert any(x['code']=='UNSUPPORTED_TIMED_NONSINGING_WORDS' for x in report['issues'])
    assert not out


def test_30_oversized_sidecar_is_rejected_before_large_read():
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp); song=root/'song.wav';_wav(song)
        huge=song.with_suffix('.lrc')
        with huge.open('wb') as f:
            f.truncate(16*1024*1024 + 1)
        try:
            standardize_single_song(str(song), mode='audit')
        except ValueError as exc:
            assert 'LYRICS_SOURCE_TOO_LARGE' in str(exc)
        else:
            raise AssertionError('oversized sidecar was loaded without a size gate')


def test_31_embedded_elrc_with_utf8_bom_is_semantically_identical():
    raw='\ufeff'+ELRC
    assert compare_elrc_ttml(raw,TTML).passed
    report,_=_audit(embedded_elrc=raw,embedded_ttml=TTML)
    assert report['status']=='PASS',report


if __name__=='__main__':
    sys.exit(run(globals()))
