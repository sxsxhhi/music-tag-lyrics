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


def test_f16_valid_mp3_plus_id3v1_remains_verified():
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


if __name__ == '__main__':
    import sys
    sys.exit(run(globals()))
