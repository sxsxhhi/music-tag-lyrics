# -*- coding: utf-8 -*-
"""v1.8.3 post-audit safety contract: independent adversarial synthetic cases.

Tests exercise the public CLI, structural parser, container stream verification,
and existing-source preservation. No network, model download, or private media.
"""
from __future__ import annotations

import os
import tempfile
import wave
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest,
    write_verified_standardized_container,
)
from core.auditors.elrc_ttml_comparator import (
    compare_elrc_ttml, parse_ttml_structural,
)
from scripts.standardize_song import standardize_single_song
from tests._synthetic_flac import SYNTHETIC_FLAC_BYTES
from tests._testkit import run


ELRC = '[00:15.000]<00:15.000>测试文本<00:17.000>\n'


def _ttml(*, div_bounds=True, extra='', text='测试文本'):
    div = 'begin="00:00:15.000" end="00:00:17.000"' if div_bounds else ''
    return (f'<tt xmlns="http://www.w3.org/ns/ttml" '
            f'xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
            f'<body><div {div}><p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
            f'<span begin="00:00:15.000" end="00:00:17.000">{text}</span>'
            f'{extra}</p></div></body></tt>')


def _make_wav(path: Path, *, audio=b'\x00\x00' * 256):
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(audio)


def _make_mp3(path: Path, frames=4):
    frame = b'\xff\xfb\x90\x00' + b'\xaa' * 413
    path.write_bytes(frame * frames)
    return frame


def _block_codes(ttml):
    _, _, issues = parse_ttml_structural(ttml)
    return {x.code for x in issues if x.blocking}


def test_f01_inferred_div_audit_not_blocked_and_notices_preserved():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 's.wav'; _make_wav(src)
        src.with_suffix('.ttml').write_text(_ttml(div_bounds=False), encoding='utf-8')
        ret = standardize_single_song(str(src), mode='audit')
        assert ret['status'] == 'ONE_FORMAT_ONLY', ret
        assert any(x['code'] == 'DIV_BOUNDARY_INFERRED' and x['blocking'] is False for x in ret['issues'])
        assert not any(x['code'] == 'INPUT_PARSE_FAILED' for x in ret['issues'])


def test_f02_inferred_div_standardize_works_with_canonical_verification():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 's.wav'; _make_wav(src)
        src.with_suffix('.ttml').write_text(_ttml(div_bounds=False), encoding='utf-8')
        dest = Path(td) / 'new'
        ret = standardize_single_song(str(src), mode='standardize', output_dir=str(dest), no_tags=True)
        assert ret['status'] == 'PASS', ret
        assert any(x['code'] == 'DIV_BOUNDARY_INFERRED' for x in ret['issues'])
        assert (dest / 's.lrc').exists() and (dest / 's.ttml').exists()
        assert compare_elrc_ttml((dest / 's.lrc').read_text(), (dest / 's.ttml').read_text()).passed


def test_f03_dual_source_inferred_div_accepted():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 's.wav'; _make_wav(src)
        src.with_suffix('.ttml').write_text(_ttml(div_bounds=False), encoding='utf-8')
        src.with_suffix('.lrc').write_text(ELRC, encoding='utf-8')
        ret = standardize_single_song(str(src), mode='audit')
        assert ret['status'] == 'PASS', ret
        assert ret['baseline_comparison']['passed'] is True
        assert any(x['code'] == 'DIV_BOUNDARY_INFERRED' for x in ret['issues'])


def test_f04_direct_ruby_never_silently_dropped():
    raw = _ttml(extra='<ruby>附加声部</ruby>')
    assert 'RUBY_UNSUPPORTED' in _block_codes(raw)
    assert not compare_elrc_ttml(ELRC, raw).passed


def test_f05_direct_unknown_text_never_silently_dropped():
    raw = _ttml(extra='<unexpected>附加词</unexpected>')
    assert 'UNSUPPORTED_TEXT_STRUCTURE' in _block_codes(raw)
    assert not compare_elrc_ttml(ELRC, raw).passed


def test_f06_direct_empty_unknown_and_br_are_safely_rejected():
    for child in ('<br/>', '<unknown/>', '<rb>文字</rb>'):
        assert _block_codes(_ttml(extra=child)), child


def test_f07_nested_unknown_inside_span_is_rejected():
    raw = _ttml(text='测')
    raw = raw.replace('>测</span>', '><em>测</em></span>')
    assert 'UNSUPPORTED_TEXT_STRUCTURE' in _block_codes(raw)


def test_f08_nested_span_and_styling_ruby_both_block():
    raw = _ttml(text='测').replace('>测</span>', '><span>测</span></span>')
    assert 'RUBY_UNSUPPORTED' in _block_codes(raw)
    raw = _ttml(text='测').replace('<span begin=', '<span xmlns:tts="http://www.w3.org/ns/ttml#styling" tts:ruby="container" begin=')
    assert 'RUBY_UNSUPPORTED' in _block_codes(raw)


def test_f09_translation_span_still_parses_and_is_not_lyric():
    raw = _ttml(extra='<span xmlns:ttm="http://www.w3.org/ns/ttml#metadata" ttm:role="x-translation">译文样例</span>')
    lines, _, issues = parse_ttml_structural(raw)
    assert not any(x.blocking for x in issues), issues
    assert len(lines) == 1 and lines[0].text == '测试文本' and lines[0].translation == '译文样例'


def test_f10_known_background_and_roman_aux_tracks_rejected():
    for role in ('x-bg', 'x-roman'):
        raw = _ttml(extra=f'<span xmlns:ttm="http://www.w3.org/ns/ttml#metadata" ttm:role="{role}" begin="00:00:15.000" end="00:00:17.000">aux</span>')
        assert _block_codes(raw)


def test_f11_unknown_span_role_must_not_be_silently_flattened():
    raw = _ttml().replace('<span begin=', '<span role="x-unmodeled" begin=')
    assert 'UNSUPPORTED_SPAN_ROLE' in _block_codes(raw)


def test_f12_unsupported_xml_nodes_prevent_sidecar_creation():
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / 's.wav'; _make_wav(src)
        src.with_suffix('.ttml').write_text(_ttml(extra='<ruby>附加文本</ruby>'), encoding='utf-8')
        dest = Path(td) / 'out'
        ret = standardize_single_song(str(src), mode='standardize', output_dir=str(dest), no_tags=True)
        assert ret['status'] == 'BLOCKED', ret
        assert any(x['code'] == 'INPUT_PARSE_FAILED' for x in ret['issues'])
        assert not dest.exists()


def test_f13_empty_mp3_and_arbitrary_bytes_not_verified():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.mp3'
        for payload in (b'', b'junk' * 256, b'ID3'):
            path.write_bytes(payload)
            assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f14_truncated_and_invalid_mp3_id3_bounds_not_verified():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.mp3'
        for payload in (b'ID3\x04\x00\x00\x7f\x7f\x7f\x7f' + b'\x00'*20,
                        b'ID3\x04\x00\x00\xff\x00\x00\x00' + b'\x00'*100):
            path.write_bytes(payload)
            assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f15_one_frame_bad_middle_and_trailing_mp3_rejected():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.mp3'
        frame = _make_mp3(path, frames=1)
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'
        path.write_bytes(frame + b'INVALID_NEXT_FRAME' + frame)
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'
        path.write_bytes(frame * 3 + b'CORRUPTED_TAIL')
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f16_id3v1_outside_stream_digest():
    """音频流摘要必须排除 ID3v1 尾块（只证明"不参与摘要"，**不证明**"受保护"）。

    命名与注释在 v1.8.5 修正：旧名 ``..._remains_verified`` 极易被读成
    「ID3v1 是安全的」，实际上它恰好把这个盲区写进了回归基线——ID3v1 既不在
    摘要里，也不在 ID3v2 帧快照里，写入时会被 mutagen 静默改写。
    真正的保护契约见 ``tests/test_v185_id3v1_fidelity.py``。
    """
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.mp3'; _make_mp3(path)
        status, digest = compute_audio_stream_digest(str(path))
        assert status == 'VERIFIED'
        with path.open('ab') as f: f.write(b'TAG' + b'\x00'*125)
        assert compute_audio_stream_digest(str(path)) == ('VERIFIED', digest)


def test_f17_truncated_flac_header_cannot_be_verified():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.flac'
        for payload in (b'fLaC\x80\xff\xff\xff', b'fLaC', b'fLaC\x80\x00\x00\x22'):
            path.write_bytes(payload)
            assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f18_metadata_only_and_fake_flac_audio_rejected():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.flac'
        good = SYNTHETIC_FLAC_BYTES
        assert compute_audio_stream_digest(_write(path, good))[0] == 'VERIFIED'
        audio_offset = good.index(b'\xff\xf8')
        for payload in (good[:audio_offset], good[:audio_offset]+b'fake_audio'):
            path.write_bytes(payload)
            assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def _write(path: Path, payload: bytes) -> str:
    path.write_bytes(payload)
    return str(path)


def test_f19_valid_flac_stream_and_truncated_metadata_rejected():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.flac'
        assert compute_audio_stream_digest(_write(path, SYNTHETIC_FLAC_BYTES))[0] == 'VERIFIED'
        path.write_bytes(b'fLaC\x80\xff\xff\xff')
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f20_empty_or_invalid_wav_audio_rejected():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.wav'
        _make_wav(path, audio=b'')
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'
        _make_wav(path)
        assert compute_audio_stream_digest(str(path))[0] == 'VERIFIED'
        payload = bytearray(path.read_bytes())
        payload[4:8] = (2**32-1).to_bytes(4, 'little')
        path.write_bytes(payload)
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f21_audio_writer_denies_undeclared_mp3_payload():
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / 'not_music.mp3'
        source.write_bytes(b'junk'*300)
        destination = Path(td) / 'out.mp3'
        elrc = '[00:01.000]<00:01.000>Hi<00:02.000>\n'
        ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div begin="00:00:01.000" end="00:00:02.000">'
                '<p begin="00:00:01.000" end="00:00:02.000" key="L1"><span begin="00:00:01.000" end="00:00:02.000">Hi</span>'
                '</p></div></body></tt>')
        try:
            result = write_verified_standardized_container(str(source), str(destination), elrc, ttml)
            raise AssertionError(f'Expected rejection, got {result}')
        except Exception as exc:
            assert not isinstance(exc, AssertionError)
            assert not destination.exists() and source.read_bytes() == b'junk'*300


def test_f22_invalid_non_ttml_root_fails_closed():
    raw = _ttml().replace('<tt xmlns=', '<fake xmlns=').replace('</tt>', '</fake>')
    assert 'TTML_ROOT_INVALID' in _block_codes(raw)


def test_f23_unsupported_body_child_must_not_be_ignored():
    raw = _ttml().replace('</div></body>', '</div><p begin="00:00:18.000" end="00:00:19.000">Extra</p></body>')
    assert 'UNSUPPORTED_TEXT_STRUCTURE' in _block_codes(raw)
    assert not compare_elrc_ttml(ELRC, raw).passed


def test_f24_unsupported_div_child_must_not_be_ignored():
    raw = _ttml().replace('</p></div>', '</p><span begin="00:00:18.000" end="00:00:19.000">Extra</span></div>')
    assert 'UNSUPPORTED_TEXT_STRUCTURE' in _block_codes(raw)
    assert not compare_elrc_ttml(ELRC, raw).passed


def test_f25_nested_translation_markup_must_not_disappear():
    raw = _ttml().replace('<body>', '<head><metadata><iTunesMetadata>'
        '<translations><translation><text for="L1">译文<ruby>额外</ruby></text>'
        '</translation></translations></iTunesMetadata></metadata></head><body>')
    assert 'UNSUPPORTED_TRANSLATION_STRUCTURE' in _block_codes(raw)
    assert not compare_elrc_ttml(ELRC, raw).passed


def test_f26_wav_multiple_data_chunks_all_contribute_to_digest():
    # Unusual RIFF layouts must not report VERIFIED based only on the first
    # data chunk while silently ignoring a second audio-containing chunk.
    import struct
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 'multi.wav'
        fmt = struct.pack('<HHIIHH', 1, 1, 16000, 32000, 2, 16)
        parts = []
        for name, payload in ((b'fmt ', fmt), (b'data', b'\x01\x00' * 32),
                              (b'JUNK', b'1234'), (b'data', b'\x02\x00' * 32)):
            parts.append(name + len(payload).to_bytes(4, 'little') + payload)
        body = b'WAVE' + b''.join(parts)
        path.write_bytes(b'RIFF' + len(body).to_bytes(4, 'little') + body)
        status, digest = compute_audio_stream_digest(str(path))
        assert status == 'VERIFIED'
        content = bytearray(path.read_bytes())
        content[-2] ^= 1
        path.write_bytes(content)
        new_status, new_digest = compute_audio_stream_digest(str(path))
        assert new_status == 'VERIFIED' and new_digest != digest


def test_f27_two_byte_flac_sync_is_not_a_complete_audio_frame():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / 's.flac'
        good = SYNTHETIC_FLAC_BYTES
        pos = good.index(b'\xff\xf8')
        path.write_bytes(good[:pos] + b'\xff\xf8')
        assert compute_audio_stream_digest(str(path))[0] != 'VERIFIED'


def test_f28_direct_nonwhitespace_in_body_or_div_or_ptail_blocks():
    # Body text, div text, and p tail non-whitespace text must block
    # rather than silently disappearing.
    div_begin = '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"><body><div begin="00:00:15.000" end="00:00:17.000">'
    line = '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1"><span begin="00:00:15.000" end="00:00:17.000">测试文本</span></p>'
    div_end = '</div></body></tt>'

    # 1. Div text
    raw_div = div_begin + '被遗漏的正文' + line + div_end
    _, _, issues_div = parse_ttml_structural(raw_div)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues_div)
    assert not compare_elrc_ttml(ELRC, raw_div).passed

    # 2. Body text
    raw_body = '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"><body>外层正文<div begin="00:00:15.000" end="00:00:17.000">' + line + div_end
    _, _, issues_body = parse_ttml_structural(raw_body)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues_body)

    # 3. P tail
    raw_tail = div_begin + line + '行后残余文本' + div_end
    _, _, issues_tail = parse_ttml_structural(raw_tail)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues_tail)


def test_f29_span_tail_nonwhitespace_must_block_without_inventing_word_time():
    div_begin = '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"><body><div begin="00:00:15.000" end="00:00:17.000">'
    line = '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1"><span begin="00:00:15.000" end="00:00:17.000">测试文本</span>未提供逐字时间的文本</p>'
    div_end = '</div></body></tt>'
    raw = div_begin + line + div_end
    _, _, issues = parse_ttml_structural(raw)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues)


def test_f30_wav_trailing_partial_riff_chunk_rejected():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'sound.wav'
        _make_wav(p)
        assert compute_audio_stream_digest(str(p))[0] == 'VERIFIED'
        b = bytearray(p.read_bytes())
        b += b'JUNK'
        b[4:8] = (len(b) - 8).to_bytes(4, 'little')
        p.write_bytes(b)
        assert compute_audio_stream_digest(str(p))[0] != 'VERIFIED'


def test_f31_wav_fmt_validation_pcm_and_extensible():
    import struct
    with tempfile.TemporaryDirectory() as td:
        # Invalid: sample_rate = 0
        p = Path(td) / 'bad_sr.wav'
        _make_wav(p)
        b = bytearray(p.read_bytes())
        b[24:28] = b'\x00\x00\x00\x00'
        p.write_bytes(b)
        assert compute_audio_stream_digest(str(p))[0] != 'VERIFIED'

        # Invalid: block_align mismatch
        p_ba = Path(td) / 'bad_ba.wav'
        _make_wav(p_ba)
        b = bytearray(p_ba.read_bytes())
        b[32:34] = b'\x05\x00'
        p_ba.write_bytes(b)
        assert compute_audio_stream_digest(str(p_ba))[0] != 'VERIFIED'

        # Valid: WAVE_FORMAT_EXTENSIBLE with PCM subformat
        p_ext = Path(td) / 'extensible.wav'
        fmt_data = struct.pack(
            '<HHIIHH',
            0xFFFE,   # WAVE_FORMAT_EXTENSIBLE
            2,        # 2 channels
            44100,    # sample_rate
            176400,   # byte_rate (44100 * 4)
            4,        # block_align (2 * 16 // 8)
            16        # bits_per_sample
        )
        # cbSize=22, valid_bits=16, channel_mask=3, subformat=KSDATAFORMAT_SUBTYPE_PCM
        ext_extra = struct.pack(
            '<HHI',
            22, 16, 3
        ) + b'\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71'
        fmt_chunk = b'fmt ' + struct.pack('<I', len(fmt_data + ext_extra)) + fmt_data + ext_extra
        pcm_payload = b'\x00\x00\x00\x00' * 100
        data_chunk = b'data' + struct.pack('<I', len(pcm_payload)) + pcm_payload
        body = b'WAVE' + fmt_chunk + data_chunk
        p_ext.write_bytes(b'RIFF' + struct.pack('<I', len(body)) + body)
        assert compute_audio_stream_digest(str(p_ext))[0] == 'VERIFIED'


def test_f32_p0_cli_end_to_end_rejects_missing_text_without_artifacts():
    div_begin = '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal"><body><div begin="00:00:15.000" end="00:00:17.000">'
    line = '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1"><span begin="00:00:15.000" end="00:00:17.000">测试文本</span></p>'
    div_end = '</div></body></tt>'
    raw = div_begin + '被遗漏的正文' + line + div_end

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'song.wav'
        _make_wav(p)
        p.with_suffix('.ttml').write_text(raw, encoding='utf-8')
        dest = Path(td) / 'output'
        result = standardize_single_song(str(p), mode='standardize', output_dir=str(dest), no_tags=True)
        assert result['status'] != 'PASS'
        assert not list(dest.glob('song.*'))


def test_f33_valid_whitespace_inter_word_span_tail_preserved():
    # Valid spaces between word spans must remain intact and parse cleanly.
    raw = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<body><div begin="00:00:10.000" end="00:00:14.000">'
        '<p begin="00:00:10.000" end="00:00:14.000" itunes:key="L1">'
        '<span begin="00:00:10.000" end="00:00:12.000">Hello</span> '
        '<span begin="00:00:12.000" end="00:00:14.000">world</span>'
        '</p></div></body></tt>'
    )
    lines, _, issues = parse_ttml_structural(raw)
    assert not any(x.blocking for x in issues)
    assert lines[0].text == 'Hello world'
    assert len(lines[0].words) == 2
    assert lines[0].words[0].text == 'Hello'
    assert lines[0].words[1].text == 'world'


def test_f34_duplicate_fmt_chunk_in_wav_rejected():
    import struct
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'dup_fmt.wav'
        fmt = struct.pack('<HHIIHH', 1, 1, 16000, 32000, 2, 16)
        fmt_chunk = b'fmt ' + len(fmt).to_bytes(4, 'little') + fmt
        data_payload = b'\x00\x00' * 64
        data_chunk = b'data' + len(data_payload).to_bytes(4, 'little') + data_payload
        # Duplicate fmt chunk inside RIFF
        body = b'WAVE' + fmt_chunk + fmt_chunk + data_chunk
        p.write_bytes(b'RIFF' + len(body).to_bytes(4, 'little') + body)
        assert compute_audio_stream_digest(str(p))[0] != 'VERIFIED'


def test_f35_head_direct_text_and_multiple_heads_blocked():
    # Direct text in <head>
    raw_head_text = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<head>非法直接正文<metadata /></head>'
        '<body><div begin="00:00:15.000" end="00:00:17.000">'
        '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
        '<span begin="00:00:15.000" end="00:00:17.000">测试</span></p></div></body></tt>'
    )
    _, _, issues1 = parse_ttml_structural(raw_head_text)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues1)

    # Multiple <head> tags
    raw_multi_head = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<head><metadata /></head><head><metadata /></head>'
        '<body><div begin="00:00:15.000" end="00:00:17.000">'
        '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
        '<span begin="00:00:15.000" end="00:00:17.000">测试</span></p></div></body></tt>'
    )
    _, _, issues2 = parse_ttml_structural(raw_multi_head)
    assert any(x.blocking and x.code == 'HEAD_STRUCTURE_INVALID' for x in issues2)


def test_f36_mixed_speaker_cue_and_untimed_words_blocked():
    # Speaker prefix with trailing untimed singing text before span must block
    raw = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<body><div begin="00:00:15.000" end="00:00:17.000">'
        '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
        '男：附加未打轴词'
        '<span begin="00:00:15.000" end="00:00:17.000">打轴词</span></p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(raw)
    assert any(x.blocking and x.code == 'UNSUPPORTED_TEXT_NODE' for x in issues)


def test_f37_tab_and_newline_inter_word_spacing_preserved():
    # Tabs and newlines in span.tail must be recognized as valid word separation
    raw = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<body><div begin="00:00:10.000" end="00:00:14.000">'
        '<p begin="00:00:10.000" end="00:00:14.000" itunes:key="L1">\n'
        '  <span begin="00:00:10.000" end="00:00:12.000">Hello</span>\n  '
        '  <span begin="00:00:12.000" end="00:00:14.000">world</span>\n'
        '</p></div></body></tt>'
    )
    lines, _, issues = parse_ttml_structural(raw)
    assert not any(x.blocking for x in issues)
    assert 'Hello' in lines[0].words[0].text
    assert 'world' in lines[0].words[1].text


def test_f38_unknown_root_child_blocked():
    # Unknown/unmodeled direct children under <tt> must produce blocking UNSUPPORTED_ROOT_CHILD
    raw = (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:itunes="http://music.apple.com/lyric-ttml-internal">'
        '<unknown>未处理的文本节点</unknown>'
        '<body><div begin="00:00:15.000" end="00:00:17.000">'
        '<p begin="00:00:15.000" end="00:00:17.000" itunes:key="L1">'
        '<span begin="00:00:15.000" end="00:00:17.000">测试文本</span></p></div></body></tt>'
    )
    _, _, issues = parse_ttml_structural(raw)
    assert any(x.blocking and x.code == 'UNSUPPORTED_ROOT_CHILD' for x in issues)

    res = compare_elrc_ttml(ELRC, raw)
    assert not res.passed
    assert any(x.blocking and x.code == 'UNSUPPORTED_ROOT_CHILD' for x in res.issues)

    with tempfile.TemporaryDirectory() as tmp_d:
        p = Path(tmp_d)
        wav = p / 'song.wav'
        _make_wav(wav)
        wav.with_suffix('.lrc').write_text(ELRC, encoding='utf-8')
        wav.with_suffix('.ttml').write_text(raw, encoding='utf-8')
        out_d = p / 'out'
        for mode in ('audit', 'standardize'):
            sr = standardize_single_song(str(wav), mode=mode, output_dir=str(out_d), no_tags=True)
            assert sr['status'] != 'PASS'
            assert not list(out_d.glob('song.*'))


def test_f39_malformed_elrc_line_blocked():
    # Unrecognized/malformed non-empty ELRC lines must not be silently dropped
    malformed_elrc = ELRC + '[00:AA.123]被跳过的另一句歌词\n'
    ttml = _ttml(text='测试文本')

    res = compare_elrc_ttml(malformed_elrc, ttml)
    assert not res.passed
    assert any(x.blocking and x.code == 'ELRC_SYNTAX_ERROR' for x in res.issues)

    with tempfile.TemporaryDirectory() as tmp_d:
        p = Path(tmp_d)
        wav = p / 'song.wav'
        _make_wav(wav)
        wav.with_suffix('.lrc').write_text(malformed_elrc, encoding='utf-8')
        wav.with_suffix('.ttml').write_text(ttml, encoding='utf-8')
        out_d = p / 'out'
        for mode in ('audit', 'standardize'):
            sr = standardize_single_song(str(wav), mode=mode, output_dir=str(out_d), no_tags=True)
            assert sr['status'] != 'PASS'
            assert not list(out_d.glob('song.*'))


if __name__ == '__main__':
    import sys
    sys.exit(run(globals()))
