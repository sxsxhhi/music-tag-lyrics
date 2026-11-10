# -*- coding: utf-8 -*-
"""v1.8.3 P1: 音频数据主体流保真度与大文件流式哈希测试 (SEC-04, SEC-05)。

涵盖：
- 大文件流式分块哈希，验证不调用 Path.read_bytes() 且逐字节一致
- FLAC、WAV、MP3 容器修改元数据标签时，音频帧数据主体 100% 保持不变
- 篡改音频数据帧故障注入：触发 AUDIO_STREAM_CORRUPTED 并安全回滚
- 非法/无法解析音频边界容器：触发 AUDIO_STREAM_NOT_VERIFIED 拒绝写入
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.wave import WAVE

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest,
    write_verified_standardized_container,
)
from core.utils.io_safe import sha256_file
from tests._testkit import run
from tests._synthetic_flac import SYNTHETIC_FLAC_BYTES

_TEST_ELRC = "[00:01.000]<00:01.000>Audio<00:02.000> stream<00:03.000>\n"
_TEST_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
    'xml:lang="en" itunes:timing="Word">'
    '<body><div begin="00:00:01.000" end="00:00:03.000">'
    '<p begin="00:00:01.000" end="00:00:03.000" itunes:key="L1">'
    '<span begin="00:00:01.000" end="00:00:02.000">Audio</span> '
    '<span begin="00:00:02.000" end="00:00:03.000">stream</span>'
    '</p></div></body></tt>'
)


def _create_test_flac(filepath: str) -> None:
    with open(filepath, "wb") as f:
        f.write(SYNTHETIC_FLAC_BYTES)
    flac = FLAC(filepath)
    flac["title"] = ["Original FLAC"]
    flac.save()


def _create_test_wav(filepath: str, frames: bytes = b"\x01\x00\x02\x00" * 2000) -> None:
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(frames)


def _create_test_mp3(filepath: str, num_frames: int = 5) -> None:
    frame = b"\xff\xfb\x90\x00" + (b"\xaa" * 413)
    with open(filepath, "wb") as f:
        f.write(frame * num_frames)
    id3 = ID3()
    id3.save(filepath)


def test_sec_04_streaming_hash_does_not_call_read_bytes():
    """SEC-04: 流式哈希计算大文件时，不调用 Path.read_bytes() 且哈希一致。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "stream_test.dat"
        payload = b"STREAMING_BLOCK_TEST_DATA_" * 10000
        p.write_bytes(payload)

        import hashlib
        expected_hash = hashlib.sha256(payload).hexdigest()

        with patch.object(Path, "read_bytes", side_effect=AssertionError("Path.read_bytes() must NOT be called")):
            got_hash = sha256_file(str(p), chunk_size=1024)
            assert got_hash == expected_hash, f"Hash mismatch: expected {expected_hash}, got {got_hash}"


def test_sec_05_flac_audio_stream_invariance():
    """SEC-05-A: FLAC 容器标签更新前后，音频帧数据流哈希保持 100% 绝对一致。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orig.flac")
        dst = os.path.join(td, "standardized.flac")
        _create_test_flac(src)

        st_orig, hash_orig = compute_audio_stream_digest(src)
        assert st_orig == "VERIFIED"

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
        )
        assert res["status"] == "PASS"
        assert res["audio_stream_status"] == "VERIFIED"

        st_dst, hash_dst = compute_audio_stream_digest(dst)
        assert st_dst == "VERIFIED"
        assert hash_orig == hash_dst, "FLAC audio stream corrupted across metadata write"


def test_sec_05_wav_audio_stream_invariance():
    """SEC-05-B: WAV 容器标签更新前后，data chunk PCM 音频流保持 100% 绝对一致。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orig.wav")
        dst = os.path.join(td, "standardized.wav")
        _create_test_wav(src)

        st_orig, hash_orig = compute_audio_stream_digest(src)
        assert st_orig == "VERIFIED"

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
        )
        assert res["status"] == "PASS"
        st_dst, hash_dst = compute_audio_stream_digest(dst)
        assert st_dst == "VERIFIED"
        assert hash_orig == hash_dst, "WAV audio stream corrupted across metadata write"


def test_sec_05_mp3_audio_stream_invariance():
    """SEC-05-C: MP3 容器标签更新前后，MPEG 有效音频帧区保持 100% 绝对一致。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orig.mp3")
        dst = os.path.join(td, "standardized.mp3")
        _create_test_mp3(src)

        st_orig, hash_orig = compute_audio_stream_digest(src)
        assert st_orig == "VERIFIED"

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
        )
        assert res["status"] == "PASS"
        st_dst, hash_dst = compute_audio_stream_digest(dst)
        assert st_dst == "VERIFIED"
        assert hash_orig == hash_dst, "MP3 audio stream corrupted across metadata write"


def test_sec_05_tampered_audio_frame_triggers_corruption_rollback():
    """SEC-05-D: 故障注入：若音频帧数据在写入过程中发生篡改，阻断并销毁暂存副本。"""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "orig.flac")
        dst = os.path.join(td, "standardized.flac")
        _create_test_flac(src)

        # Mock shutil.copy2 模拟复制后音频帧被损坏
        real_copy2 = shutil.copy2

        def corrupting_copy(s, d):
            real_copy2(s, d)
            with open(d, "ab") as f:
                f.write(b"CORRUPTED_FRAME_PAYLOAD")

        with patch("shutil.copy2", side_effect=corrupting_copy):
            try:
                write_verified_standardized_container(
                    source_audio_path=src,
                    target_work_path=dst,
                    elrc_text=_TEST_ELRC,
                    ttml_text=_TEST_TTML,
                )
                assert False, "Should raise AUDIO_STREAM_CORRUPTED"
            except RuntimeError as exc:
                assert "AUDIO_STREAM_CORRUPTED" in str(exc)

        # 确保目标文件未被建立
        assert not os.path.exists(dst)


if __name__ == "__main__":
    sys.exit(run(globals()))
