# -*- coding: utf-8 -*-
"""ISRC 写入安全、真实容器读写回归与文件完整性验证 (v1.8.1)。

覆盖测试矩阵：
- I01: 本地时长缺失 + --write-isrc -> 不写
- I02: 远端音轨时长缺失 + --write-isrc -> 不写
- I03: <body dur> 存在但音轨时长缺失 -> 不写
- I04: 本地已有非空 ISRC -> 不覆盖已有标签
- I05: --no-tags / --lyrics-only -> 绝对不写 ISRC
- F01: MP3 / FLAC / WAV 容器真实标签写入、保存与回读
- F02: 写入异常保护：safe_save_audio_tags 确保原子替换，失败不损坏原文件
- F03: --no-tags 只读处理：音频文件 SHA-256 哈希 100% 保持一致
"""

import hashlib
import os
import shutil
import sys
import tempfile
import wave
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:
    from tests import _testkit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mutagen.id3 import ID3, TIT2, TSRC
from mutagen.flac import FLAC

from scripts.tag_editor import update_audio_tags, read_audio_tags
import scripts.process_song as ps


def _sha256(filepath: str) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _create_minimal_wav(filepath: str, duration_sec: int = 2) -> None:
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00" * (44100 * 2 * duration_sec))


def _create_minimal_flac(filepath: str) -> None:
    streaminfo = b"\x10\x00\x10\x00\x00\x00\x00\x00\x00\x00\x0a\x00\x01\x00\x00\x00" + b"\x00" * 18
    header = b"fLaC\x80\x00\x00\x22" + streaminfo
    with open(filepath, "wb") as f:
        f.write(header)
    f_obj = FLAC(filepath)
    f_obj.add_tags()
    f_obj.save()


def _create_minimal_mp3(filepath: str) -> None:
    id3 = ID3()
    id3.save(filepath)


def test_i01_local_duration_missing_no_isrc_write():
    """I01: 本地时长为 0 时，即使开启 --write-isrc 也不允许写入。"""
    lyric_data = {
        "isrc": "GBARL1401524",
        "duration_ms": 270000,
        "isrc_write_eligible": False,  # 因本地时长为 0，门禁未通过
        "match": {"confidence": "strict", "by_model": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": None, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=True, write_isrc=True, non_interactive=True)
            if mock_update.called:
                assert mock_update.call_args.kwargs.get("isrc") is None


def test_i02_remote_duration_missing_no_isrc_write():
    """I02: 远端音轨时长缺失时，即使开启 --write-isrc 也不允许写入。"""
    lyric_data = {
        "isrc": "GBARL1401524",
        "duration_ms": 0,
        "isrc_write_eligible": False,
        "match": {"confidence": "strict", "by_model": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=True, write_isrc=True, non_interactive=True)
            if mock_update.called:
                assert mock_update.call_args.kwargs.get("isrc") is None


def test_i03_body_dur_only_no_isrc_write():
    """I03: 仅有 TTML body dur 而缺失远端真实音轨时长时，不得写回 ISRC。"""
    lyric_data = {
        "isrc": "GBARL1401524",
        "duration_ms": 270000,  # 仅歌词时长
        "isrc_write_eligible": False,
        "match": {"confidence": "insufficient", "by_model": "unknown"},
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=True, write_isrc=True, non_interactive=True)
            if mock_update.called:
                assert mock_update.call_args.kwargs.get("isrc") is None


def test_i04_existing_isrc_never_overwritten():
    """I04: 本地已有非空 ISRC 时，无论是否开启 --write-isrc 均绝不覆盖。"""
    lyric_data = {
        "isrc": "NEWISRC12345",
        "duration_ms": 270000,
        "isrc_write_eligible": True,
        "match": {"confidence": "strict", "by_model": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": 270000, "isrc": "OLDISRC00000"}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=True, write_isrc=True, non_interactive=True)
            if mock_update.called:
                assert mock_update.call_args.kwargs.get("isrc") is None


def test_i05_no_tags_and_lyrics_only_never_write_isrc():
    """I05: --no-tags 与 --lyrics-only 模式下绝对不写回 ISRC。"""
    lyric_data = {
        "isrc": "GBARL1401524",
        "duration_ms": 270000,
        "isrc_write_eligible": True,
        "match": {"confidence": "strict", "by_model": "false"},
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        # 1. lyrics-only 模式
        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=True, lyrics_only=True, write_isrc=True, non_interactive=True)
            if mock_update.called:
                assert "isrc" not in mock_update.call_args.kwargs or mock_update.call_args.kwargs.get("isrc") is None

        # 2. no-tags 模式
        with mock.patch.object(ps, "read_audio_tags", return_value={"duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags") as mock_update:
            ps.process_single_song(mp3, update_tags=False, write_isrc=True, non_interactive=True)
            assert not mock_update.called, "--no-tags 严禁调用标签更新"


def test_f01_real_container_read_write_roundtrip():
    """F01: FLAC / MP3 / WAV 真实文件容器 ISRC 读写往返测试。"""
    with tempfile.TemporaryDirectory() as td:
        # 1. MP3
        mp3 = os.path.join(td, "track.mp3")
        _create_minimal_mp3(mp3)
        assert update_audio_tags(mp3, title="Song 1", isrc="GBARL1401524") is True
        info = read_audio_tags(mp3)
        assert info["title"] == "Song 1"
        assert info["isrc"] == "GBARL1401524"

        # 2. FLAC
        flac = os.path.join(td, "track.flac")
        _create_minimal_flac(flac)
        assert update_audio_tags(flac, title="Song 2", isrc="USUM71413645") is True
        info = read_audio_tags(flac)
        assert info["title"] == "Song 2"
        assert info["isrc"] == "USUM71413645"

        # 3. WAV
        wav = os.path.join(td, "track.wav")
        _create_minimal_wav(wav)
        assert update_audio_tags(wav, title="Song 3", isrc="CNA010230400") is True
        info = read_audio_tags(wav)
        assert info["title"] == "Song 3"
        assert info["isrc"] == "CNA010230400"


def test_f02_atomic_write_failure_preserves_original():
    """F02: 写入中途发生异常时，safe_save_audio_tags 确保原音频文件完好不损。"""
    from core.utils.io_safe import safe_save_audio_tags

    with tempfile.TemporaryDirectory() as td:
        target = os.path.join(td, "safe_test.mp3")
        original_bytes = b"ORIGINAL_AUDIO_CONTENT_HEADER" * 10
        with open(target, "wb") as f:
            f.write(original_bytes)
        orig_hash = _sha256(target)

        def faulty_writer(tmp_path: str):
            with open(tmp_path, "wb") as f:
                f.write(b"CORRUPTED_PARTIAL_WRITE")
            raise RuntimeError("Simulated process crash / disk error")

        success = False
        try:
            safe_save_audio_tags(target, faulty_writer)
        except Exception:
            pass

        assert success is False
        assert os.path.exists(target)
        assert _sha256(target) == orig_hash, "原文件必须完全未受破坏"


def test_f03_no_tags_mode_preserves_audio_hash():
    """F03: --no-tags 运行下，音频文件内容哈希 100% 保持严格一致。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "readonly_test.wav")
        _create_minimal_wav(src)
        orig_hash = _sha256(src)

        mock_cand = {
            "source": "apple-ttml",
            "lyric_type": "word",
            "lines": [
                {"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}
            ],
            "tags": {"ti": "Wav Song"},
        }

        with mock.patch.object(ps, "read_audio_tags", return_value={"title": "Wav Song", "duration": 2000}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=mock_cand):
            out_dir = os.path.join(td, "lyrics_out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(
                src,
                update_tags=False,
                output_dir=out_dir,
                non_interactive=True,
            )
            assert ok is True

        assert _sha256(src) == orig_hash, "--no-tags 处理后音频文件哈希必须绝对一致"


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
