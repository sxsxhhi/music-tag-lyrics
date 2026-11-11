# -*- coding: utf-8 -*-
"""v1.8.5 post-audit R2: fail-closed network / ID3v1 / FLAC gates.

Real files are synthetic, mutations happen only inside TemporaryDirectory,
HTTP is mocked (no external network). These tests intentionally cover the
unobserved failure modes that were missed by the first v1.8.5 test suite.
"""
import io
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
try:
    import _testkit
except ImportError:
    from tests import _testkit

from core.utils import http, id3v1
from core.utils.io_safe import safe_save_audio_tags
from core.adapters import container_tag_adapter as adapter
from mutagen.flac import FLAC
from scripts.tag_editor import update_audio_tags
from tests.test_v185_id3v1_fidelity import _mp3_with_v2_and_v1
from tests.test_standard_container_tags import _create_minimal_flac, _TEST_ELRC, _TEST_TTML


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.headers = {}
        self.calls = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, count=-1):
        self.calls.append(count)
        return self.payload if count < 0 else self.payload[:count]


def _flac_callback(path):
    audio = FLAC(path)
    audio["lyrics"] = [_TEST_ELRC]
    audio.save()
    return True


def test_binary_endpoint_is_bounded_by_default():
    # A direct caller of the shared binary HTTP API must not silently use an
    # unlimited body; cover-art callers separately specify their own limit.
    resp = _Response(b"b" * (http.DEFAULT_MAX_BINARY_BYTES + 1))
    with patch.object(urllib.request, "urlopen", return_value=resp):
        result = http.http_get_bytes("https://invalid.example/cover")
    assert result is None
    assert resp.calls == [http.DEFAULT_MAX_BINARY_BYTES + 1]


def test_ex_defaults_to_bounded_response():
    resp = _Response(b"x" * (http.DEFAULT_MAX_BYTES + 9))
    with patch.object(urllib.request, "urlopen", return_value=resp):
        result = http.http_get_text_ex("https://invalid.example/ttml")
    assert resp.calls == [http.DEFAULT_MAX_BYTES + 1], resp.calls
    assert result.status == 200 and result.error == "oversized" and result.text == ""


def test_ex_bounded_http_error():
    payload = io.BytesIO(b"x" * 500)
    err = urllib.error.HTTPError("https://invalid.example/x", 429, "busy", {}, payload)
    with patch.object(urllib.request, "urlopen", side_effect=err):
        result = http.http_get_text_ex("https://invalid.example/x", max_bytes=128)
    assert result.status == 429 and result.error == "oversized", result
    assert result.text == "" and result.bytes_len == 129, result
    assert payload.tell() == 129, payload.tell()


def test_ex_small_http_error_backward_compatible():
    err = urllib.error.HTTPError("https://invalid.example/x", 429, "busy", {}, io.BytesIO(b"retry"))
    with patch.object(urllib.request, "urlopen", side_effect=err):
        result = http.http_get_text_ex("https://invalid.example/x")
    assert result.status == 429 and result.text == "retry" and result.error == "", result


def test_ex_small_success_unchanged_and_opt_out_explicit():
    resp = _Response("你好".encode())
    with patch.object(urllib.request, "urlopen", return_value=resp):
        result = http.http_get_text_ex("https://invalid.example/x")
    assert result.status == 200 and result.text == "你好" and result.error == ""
    assert resp.calls == [http.DEFAULT_MAX_BYTES + 1]
    resp2 = _Response(b"ok")
    with patch.object(urllib.request, "urlopen", return_value=resp2):
        result = http.http_get_text_ex("https://invalid.example/x", max_bytes=0)
    assert resp2.calls == [-1] and result.text == "ok"


def test_id3v1_read_error_is_distinct_from_absence():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.mp3"
        tail = _mp3_with_v2_and_v1(path)
        assert id3v1.read_id3v1_tail(path) == tail
        with patch.object(id3v1.os, "fstat", side_effect=OSError("simulated reader failure")):
            try:
                id3v1.read_id3v1_tail(path)
            except id3v1.Id3v1IntegrityError as exc:
                assert "READ_FAILED" in str(exc)
            else:
                raise AssertionError("ID3v1 read error returned absent")


def test_id3v1_short_read_is_not_absence():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.mp3"
        _mp3_with_v2_and_v1(path)
        real_open = open
        class _Wrapper:
            def __init__(self, fileobj): self.f = fileobj
            def __enter__(self): return self
            def __exit__(self, *args): self.f.close()
            def fileno(self): return self.f.fileno()
            def seek(self, *args): return self.f.seek(*args)
            def read(self, count=-1): return self.f.read(max(0, count-7))
        def short_open(name, mode='r', *a, **kw):
            f = real_open(name, mode, *a, **kw)
            return _Wrapper(f) if os.fspath(name)==os.fspath(path) and mode=='rb' else f
        with patch('builtins.open', side_effect=short_open):
            try:
                id3v1.read_id3v1_tail(path)
            except id3v1.Id3v1IntegrityError as exc:
                assert 'SHORT_READ' in str(exc)
            else:
                raise AssertionError("ID3v1 truncated read returned absent")


def test_id3v1_reader_false_absent_detected_at_commit():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.mp3"
        _mp3_with_v2_and_v1(path)
        original = path.read_bytes()
        # Simulate a bug in the writer's existence check: the separately-read
        # integrity fingerprint must still reject the output.
        with patch.object(id3v1, 'read_id3v1_tail', return_value=None):
            assert not update_audio_tags(str(path), lyrics=_TEST_ELRC)
        assert path.read_bytes() == original


def test_id3v1_preflight_read_error_returns_false():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.mp3"
        _mp3_with_v2_and_v1(path)
        original = path.read_bytes()
        called = []
        with patch.object(id3v1, '_read_id3v1_tail_checked', side_effect=id3v1.Id3v1IntegrityError('READ_FAILED')):
            ok = safe_save_audio_tags(str(path), lambda tmp: called.append(tmp) or True)
        assert not ok and not called
        assert path.read_bytes() == original


def test_flac_routine_none_fingerprint_blocks():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.flac"
        _create_minimal_flac(str(path))
        original = path.read_bytes()
        with patch.object(adapter, 'flac_metadata_fingerprint', return_value=None):
            ok = safe_save_audio_tags(str(path), _flac_callback)
        assert not ok and path.read_bytes() == original


def test_flac_routine_raised_fingerprint_blocks():
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "a.flac"
        _create_minimal_flac(str(path))
        original = path.read_bytes()
        with patch.object(adapter, 'flac_metadata_fingerprint', side_effect=OSError('unreadable')):
            ok = safe_save_audio_tags(str(path), _flac_callback)
        assert not ok and path.read_bytes() == original


def test_flac_verified_writer_none_fingerprint_blocks():
    with tempfile.TemporaryDirectory() as temp:
        src, dst = Path(temp)/"src.flac", Path(temp)/"dst.flac"
        _create_minimal_flac(str(src))
        original = src.read_bytes()
        with patch.object(adapter, 'flac_metadata_fingerprint', return_value=None):
            try:
                adapter.write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
            except RuntimeError as exc:
                assert 'flac_metadata_unreadable' in str(exc)
            else:
                raise AssertionError("unavailable FLAC fingerprint accepted")
        assert src.read_bytes() == original and not dst.exists()


def test_flac_empty_fingerprint_is_valid():
    with tempfile.TemporaryDirectory() as temp:
        src, dst = Path(temp)/"src.flac", Path(temp)/"dst.flac"
        _create_minimal_flac(str(src))
        # No APPLICATION/SEEKTABLE/CUESHEET: {} is a valid fingerprint.
        assert adapter.flac_metadata_fingerprint(str(src)) == {}
        result = adapter.write_verified_standardized_container(str(src), str(dst), _TEST_ELRC, _TEST_TTML)
        assert result['status'] == 'PASS' and dst.exists()
        assert adapter.flac_metadata_fingerprint(str(dst)) == {}


def test_container_read_tolerates_non_mp3_and_directories():
    """只读巡检接口不得因非 MP3 / 目录抛 ID3v1 错误（本机集成时发现的 R2 回归）。

    `read_container_tags()` 是"看一眼有什么标签"的只读 API；ID3v1 只存在于 MP3，
    对 FLAC/M4A/OGG 或目录做尾块探测既无意义，又会把真实原因替换成
    ID3V1_READ_FAILED。写入路径仍需 fail-closed，这一点由 MP3 分支保留。
    """
    with tempfile.TemporaryDirectory() as temp:
        info_dir = adapter.read_container_tags(temp)
        assert info_dir["id3v1_present"] is False
        assert info_dir["id3v1_sha256"] == ""

        flac_path = Path(temp) / "a.flac"
        _create_minimal_flac(str(flac_path))
        info_flac = adapter.read_container_tags(str(flac_path))
        assert info_flac["id3v1_present"] is False
        assert info_flac["id3v1_sha256"] == ""

        mp3_path = Path(temp) / "a.mp3"
        _mp3_with_v2_and_v1(mp3_path)
        info_mp3 = adapter.read_container_tags(str(mp3_path))
        assert info_mp3["id3v1_present"] is True
        assert len(info_mp3["id3v1_sha256"]) == 64

        directory_named_mp3 = Path(temp) / "trap.mp3"
        directory_named_mp3.mkdir()
        try:
            adapter.read_container_tags(str(directory_named_mp3))
        except id3v1.Id3v1IntegrityError:
            raise AssertionError("目录被误判为 ID3v1 读取失败")
        except Exception:
            pass  # 其它异常（例如 ID3 解析失败）属于可接受范畴


if __name__ == '__main__':
    sys.exit(_testkit.run(globals()))
