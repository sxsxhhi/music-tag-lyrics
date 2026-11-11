# -*- coding: utf-8 -*-
"""ISRC 写入安全、真实容器读写回归与文件完整性验证 (v1.8.1 / R02 / R17 / R18)。

覆盖测试矩阵：
- I01: 本地时长缺失 + --write-isrc -> 必须尝试更新标签但 isrc=None (R02)
- I02: 远端音轨时长缺失 + --write-isrc -> 必须尝试更新标签但 isrc=None (R02)
- I03: <body dur> 存在但音轨时长缺失 -> 必须尝试更新标签但 isrc=None (R02)
- I04: 本地已有非空 ISRC -> 绝不覆盖已有标签且 isrc=None (R02)
- I05: --no-tags 绝对不调 update；--lyrics-only 只更歌词不写 isrc (R02)
- I06: 门禁完全满足时的正向对照 -> 真实写入对应 ISRC (R02 正向验证)
- F01: MP3 / FLAC / WAV 容器真实标签写入、保存与回读 (R17)
- F02: 写入异常保护：safe_save_audio_tags 确保原子替换，失败返回值严格为 False 且原文件完好 (R02/R17)
- F03: --no-tags 只读处理：音频文件 SHA-256 哈希 100% 保持一致 (R17)
- T01: 离线网络屏障 Tripwire 拦截哨兵用例 (R18)
"""

import contextlib
import hashlib
import os
import shutil
import sys
import tempfile
import urllib.request
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

from mutagen.id3 import ID3
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


@contextlib.contextmanager
def _isolated_ps_env():
    """彻底隔离 process_single_song 的旁路网络调用（年份补全与封面下载）。"""
    mock_cf = mock.MagicMock()
    mock_cf.fetch_cover.return_value = (None, None)
    with mock.patch.object(ps.NetEaseFetcher, "lookup_year", return_value="2021"), \
         mock.patch.object(ps.CoverFetcher, "get_instance", return_value=mock_cf):
        yield



def test_i01_local_duration_missing_no_isrc_write():
    """I01: 本地时长为 0 时，即使开启 --write-isrc 也不允许写入。必须严格断言调用与 isrc=None。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "GBARL1401524",
        "duration_ms": 270000,
        "isrc_write_eligible": False,  # 因本地时长为 0，门禁未通过
        "match": {"confidence": "strict", "by_model": "false"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": None, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            # R02: 严禁 if mock_update.called，必须证明确实执行了标签写入且 isrc 参数严格为 None
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs.get("isrc") is None


def test_i02_remote_duration_missing_no_isrc_write():
    """I02: 远端音轨时长缺失时，即使开启 --write-isrc 也不允许写入。必须严格断言 isrc=None。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "GBARL1401524",
        "duration_ms": 0,
        "isrc_write_eligible": False,
        "match": {"confidence": "strict", "by_model": "false"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs.get("isrc") is None


def test_i03_body_dur_only_no_isrc_write():
    """I03: 仅有 TTML body dur 而缺失远端真实音轨时长时，不得写回 ISRC。必须严格断言 isrc=None。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "GBARL1401524",
        "duration_ms": 270000,  # 仅歌词时长
        "isrc_write_eligible": False,
        "match": {"confidence": "insufficient", "by_model": "unknown"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs.get("isrc") is None


def test_i04_existing_isrc_never_overwritten():
    """I04: 本地已有非空 ISRC 时，无论是否开启 --write-isrc 均绝不覆盖。必须严格断言 isrc=None。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "NEWISRC12345",
        "duration_ms": 270000,
        "isrc_write_eligible": True,
        "match": {"confidence": "strict", "by_model": "false"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": "OLDISRC00000"}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs.get("isrc") is None


def test_i05_no_tags_and_lyrics_only_never_write_isrc():
    """I05: --no-tags 与 --lyrics-only 模式下绝对不写回 ISRC。严格区分两种模式断言。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "GBARL1401524",
        "duration_ms": 270000,
        "isrc_write_eligible": True,
        "match": {"confidence": "strict", "by_model": "false"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        # 1. lyrics-only 模式：只写歌词，绝不写 ISRC，且必须明确调用了仅更新歌词的流程
        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out1")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, lyrics_only=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_called_once()
            assert "isrc" not in mock_update.call_args.kwargs or mock_update.call_args.kwargs.get("isrc") is None
            assert mock_update.call_args.kwargs.get("lyrics") is not None

        # 2. no-tags 模式：严禁调用 update_audio_tags
        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out2")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=False, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_not_called()


def test_i06_eligible_isrc_writes_successfully():
    """I06 (正向对照): 当全部安全门禁均满足且开启 --write-isrc 时，必须真实写入 ISRC。"""
    lyric_data = {
        "source": "lrc-red",
        "lyric_type": "word",
        "isrc": "GBARL1401524",
        "duration_ms": 270000,
        "isrc_write_eligible": True,
        "match": {"confidence": "strict", "by_model": "false"},
        "lines": [{"start": 1000, "end": 2000, "text": "Testing", "words": [{"start": 1000, "end": 2000, "text": "Testing"}]}],
    }
    with tempfile.TemporaryDirectory() as td:
        mp3 = os.path.join(td, "test.mp3")
        _create_minimal_mp3(mp3)

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Song", "artist": "Artist", "year": "2021", "duration": 270000, "isrc": ""}), \
             mock.patch.object(ps.LyricAggregator, "fetch_best_lyrics", return_value=lyric_data), \
             mock.patch.object(ps, "update_audio_tags", return_value=True) as mock_update:
            out_dir = os.path.join(td, "out")
            os.makedirs(out_dir, exist_ok=True)
            ok = ps.process_single_song(mp3, update_tags=True, write_isrc=True, output_dir=out_dir, non_interactive=True)
            assert ok is True
            mock_update.assert_called_once()
            assert mock_update.call_args.kwargs.get("isrc") == "GBARL1401524", "合规 ISRC 必须真实传递至写回参数"


def test_f01_real_container_read_write_roundtrip():
    """F01: FLAC / MP3 / WAV 真实文件容器 ISRC 读写往返测试。"""
    with tempfile.TemporaryDirectory() as td:
        # 1. MP3
        mp3 = os.path.join(td, "track.mp3")
        # F01 must use a real MPEG audio payload; an ID3-only file is not MP3.
        frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
        with open(mp3, "wb") as audio_f:
            audio_f.write(frame * 4)
        ID3().save(mp3)
        assert update_audio_tags(mp3, title="Song 1", isrc="GBARL1401524") is True
        info = read_audio_tags(mp3)
        assert info["title"] == "Song 1"
        assert info["isrc"] == "GBARL1401524"

        # 2. FLAC
        flac = os.path.join(td, "track.flac")
        # Use an actual FLAC frame region, not a tag-only synthetic header.
        from _synthetic_flac import SYNTHETIC_FLAC_BYTES
        with open(flac, "wb") as audio_f:
            audio_f.write(SYNTHETIC_FLAC_BYTES)
        FLAC(flac).save()
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
    """F02: 写入中途发生异常时，safe_save_audio_tags 确保原音频文件完好不损，严格断言函数返回值。"""
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

        # R02: 真实获取函数返回值并断言为 False
        res = safe_save_audio_tags(target, faulty_writer)
        assert res is False, "写入异常时 safe_save_audio_tags 必须返回 False"
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

        with _isolated_ps_env(), \
             mock.patch.object(ps, "read_audio_tags", return_value={"title": "Wav Song", "duration": 2000}), \
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


def test_t01_tripwire_intercepts_unmocked_network():
    """T01 (R18 拦截哨兵): 离线测试中未经 Mock 的外部 HTTP 请求必须被 Tripwire 拦截抛出 NetworkTripwireError。"""
    intercepted = False
    try:
        urllib.request.urlopen("https://music.163.com", timeout=1)
    except _testkit.NetworkTripwireError:
        intercepted = True
    except Exception:
        pass
    assert intercepted is True, "Tripwire 必须成功拦截未经授权的外部网络请求"


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
