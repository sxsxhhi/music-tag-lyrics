# -*- coding: utf-8 -*-
"""Independent v1.8.2 post-review guards: no silent vocal loss / false PASS.

Only synthetic minimal fixtures are used; source audio never modified.
"""
from __future__ import annotations
import hashlib
import tempfile
from pathlib import Path
from unittest import mock
import sys
ROOT=Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tests._testkit import run
from tests.test_standard_cli import _create_minimal_flac
from tests.test_standard_container_tags import _create_minimal_mp3, _TEST_ELRC, _TEST_TTML
from core.adapters.container_tag_adapter import write_verified_standardized_container
from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.schedulers.prelude_scheduler import plan_prelude_ab
from scripts.standardize_song import standardize_single_song
from mutagen.flac import FLAC


def _word(text, st, en):
    return {"text": text, "start": st, "end": en}


def _new_flac(root: Path, lyrics=None) -> Path:
    src = root / 'song.flac'
    _create_minimal_flac(str(src))
    if lyrics is not None:
        f = FLAC(str(src))
        f['lyrics'] = [lyrics]
        f.save()
    return src


def test_early_simultaneous_bilingual_remains_two_vocals():
    data = {'lines': [
        {'start': 2000, 'end': 3000, 'text': 'Hello', 'words': [_word('Hello',2000,3000)]},
        {'start': 2000, 'end': 3000, 'text': '你好', 'words': []},
    ]}
    c = classify_and_clean_all_lines(data,song_title='测试曲目')
    assert [l.text for l in c.lines] == ['Hello','你好']
    assert c.lines[0].translation is None
    assert any(x.code == 'AMBIGUOUS_BILINGUAL_VOCALS' and x.blocking for x in c.issues)


def test_early_bilingual_blocks_standardize_without_outputs():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        src=_new_flac(root,'[00:02.000]<00:02.000>Hello<00:03.000>\n[00:02.000]你好\n')
        out=root/'out'
        result=standardize_single_song(str(src),'standardize',output_dir=str(out),no_tags=True)
        assert result['status']=='REVIEW_REQUIRED',result
        assert any(x['code']=='AMBIGUOUS_BILINGUAL_VOCALS' and x['blocking'] for x in result['issues'])
        assert not out.exists()


def test_sung_title_without_words_must_not_be_deleted():
    c=classify_and_clean_all_lines({'lines':[
        {'start':15000,'end':17000,'text':'测试曲目','words':[]},
        {'start':18000,'end':20000,'text':'下一句','words':[_word('下一句',18000,20000)]},
    ]},song_title='测试曲目')
    assert [(x.text,x.start_ms) for x in c.lines][:1]==[('测试曲目',15000)]
    plan=plan_prelude_ab(c)
    assert plan.mode=='NONE' and plan.t_sing_ms==0
    assert any(x.code=='UNTIMED_TITLE_AMBIGUOUS' and x.blocking for x in c.issues)
    assert any(x.code=='FIRST_SING_UNVERIFIED' and x.blocking for x in c.issues)


def test_sung_title_end_to_end_not_false_pass():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d)
        src=_new_flac(root,'[00:15.000]测试曲目\n[00:18.000]<00:18.000>下一句<00:20.000>\n')
        out=root/'out'
        result=standardize_single_song(str(src),'standardize',output_dir=str(out),no_tags=True)
        assert result['status']=='REVIEW_REQUIRED',result
        assert any(x['code']=='UNTIMED_TITLE_AMBIGUOUS' for x in result['issues'])
        assert not out.exists()


def test_explicit_prelude_title_still_classifies_as_title():
    c=classify_and_clean_all_lines({'lines':[
        {'start':1500,'end':12000,'text':'测试曲目','semantic_role':'PRELUDE','words':[]},
        {'start':25000,'end':28000,'text':'实际唱词','words':[_word('实际唱词',25000,28000)]},
    ]},song_title='测试曲目')
    assert c.lines[0].role.value=='TITLE'  # retained as metadata; exporters skip it
    assert plan_prelude_ab(c).t_sing_ms==25000
    assert not any(x.code=='UNTIMED_TITLE_AMBIGUOUS' for x in c.issues)


def test_audit_blocking_issue_not_masked_by_one_format_only():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=_new_flac(root,'[00:12.000]<00:12.000>鸣谢：这是被演唱的歌词<00:14.000>\n')
        result=standardize_single_song(str(src),'audit')
        assert result['format_audit']=='ONE_FORMAT_ONLY'
        assert result['status']=='REVIEW_REQUIRED',result
        assert any(x['blocking'] for x in result['issues'])


def test_invalid_report_parent_rejected_before_output():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=_new_flac(root)
        blocker=root/'occupied';blocker.write_text('untouched')
        out=root/'out';original=src.read_bytes()
        try:
            result=standardize_single_song(str(src),'standardize',output_dir=str(out),no_tags=True,report_path=str(blocker/'r.json'))
        except NotADirectoryError:
            pass
        else:
            assert result['status']=='BLOCKED',result
        assert not out.exists() and blocker.read_text()=='untouched' and src.read_bytes()==original


def test_report_failure_after_sidecar_commit_rolls_back():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=_new_flac(root);original=hashlib.sha256(src.read_bytes()).hexdigest()
        out=root/'out'
        with mock.patch('scripts.standardize_song._write_json_report',side_effect=OSError('simulated quota')):
            result=standardize_single_song(str(src),'standardize',output_dir=str(out),no_tags=True,report_path=str(root/'report.json'))
        assert result['status']=='BLOCKED'
        assert any(x['code']=='REPORT_WRITE_FAILED' for x in result['issues'])
        assert result['actions_taken']==[]
        assert not list(out.glob('song.*'))
        assert hashlib.sha256(src.read_bytes()).hexdigest()==original


def test_report_failure_after_audio_copy_commit_rolls_back():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=_new_flac(root);before=src.read_bytes();out=root/'out'
        with mock.patch('scripts.standardize_song._write_json_report',side_effect=OSError('synthetic delayed failure')):
            result=standardize_single_song(str(src),'standardize',output_dir=str(out),write_tags=True,report_path=str(root/'report.json'))
        assert result['status']=='BLOCKED'
        assert not list(out.glob('song.*'))
        assert src.read_bytes()==before


def test_production_credits_at_outro_retain_following_singing():
    c=classify_and_clean_all_lines({'lines':[
      {'start':10000,'end':13000,'text':'第一句','words':[_word('第一句',10000,13000)]},
      {'start':15000,'end':16000,'text':'作词：样例作者','words':[]},
      {'start':16000,'end':17000,'text':'混音：样例工程师','words':[]},
      {'start':18000,'end':21000,'text':'后面的演唱','words':[_word('后面的演唱',18000,21000)]},
    ]},song_title='歌曲')
    assert [x.text for x in c.lines]==['第一句','后面的演唱']
    assert c.song.get('extracted_lyricist')=='样例作者'
    assert plan_prelude_ab(c).t_sing_ms==10000

def test_zero_start_line_only_sung_title_never_auto_deleted():
    c=classify_and_clean_all_lines({'lines':[
        {'start':0,'end':1500,'text':'测试曲目','words':[]},
        {'start':2500,'end':3500,'text':'后面的歌词','words':[_word('后面的歌词',2500,3500)]},
    ]},song_title='测试曲目')
    assert c.lines[0].role.value=='SINGING'
    assert any(x.code=='UNTIMED_TITLE_AMBIGUOUS' and x.blocking for x in c.issues)
    assert plan_prelude_ab(c).mode=='NONE'


def test_isolated_sung_thanks_without_word_axis_stays_for_review():
    c=classify_and_clean_all_lines({'lines':[
        {'start':1000,'end':4000,'text':'鸣谢：在我孤独的那些日子','words':[]},
        {'start':5000,'end':8000,'text':'真实副歌','words':[_word('真实副歌',5000,8000)]},
    ]},song_title='歌曲')
    assert c.lines[0].text=='鸣谢：在我孤独的那些日子'
    assert any(x.code=='AMBIGUOUS_LINE_RETAINED' and x.severity.value=='REVIEW_REQUIRED' for x in c.issues)
    assert plan_prelude_ab(c).mode=='NONE'


def test_unreadable_existing_mp3_id3_must_not_be_silently_reinitialized():
    with tempfile.TemporaryDirectory() as d:
        root=Path(d);src=root/'song.mp3';dst=root/'result.mp3'
        _create_minimal_mp3(str(src));before=src.read_bytes()
        with mock.patch('core.adapters.container_tag_adapter.ID3',side_effect=ValueError('corrupt ID3 tag')):
            try:
                write_verified_standardized_container(str(src),str(dst),_TEST_ELRC,_TEST_TTML)
            except ValueError as e:
                assert 'corrupt ID3 tag' in str(e)
            else:
                raise AssertionError('Corrupt existing ID3 must stop container write')
        assert src.read_bytes()==before
        assert not dst.exists()


if __name__=='__main__':
    import sys
    sys.exit(run(globals()))
