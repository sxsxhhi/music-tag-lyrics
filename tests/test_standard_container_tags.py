# -*- coding: utf-8 -*-
"""v1.8.2 R4-C 真实音频容器 (FLAC/MP3/WAV) 标签读写与回读验收测试。

严格验证任务书 §8 全部要求：
1. FLAC Vorbis Comment 映射 lyrics + ttmllyric；
2. MP3/WAV ID3 映射 USLT::eng + TXXX:TTMLLYRIC；
3. 严格保护既有封面 (APIC/pictures)、ReplayGain 与未授权元数据；
4. 磁盘物理回读并重新执行 R1 双向比对；
5. 故障注入与写回中断时自动清理副本（回滚保护）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mutagen
from mutagen.flac import FLAC, Picture as FlacPicture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
    TXXX,
)
from mutagen.wave import WAVE

from core.adapters.container_tag_adapter import (
    read_container_tags,
    write_verified_standardized_container,
)
from tests._testkit import run
from tests._synthetic_flac import SYNTHETIC_FLAC_BYTES

# 合成 1x1 像素 PNG 图片字节（用于测试封面保护）
_TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xbf"
    b"\x04\x00\x05\x05\x02\x02\xa7v\x81\xb8\x00\x00\x00\x00IEND\xaeB`\x82"
)

# 标准测试 ELRC 与 TTML
_TEST_ELRC = (
    "[00:01.000]<00:01.000>Hello<00:02.000> world<00:03.000>\n"
    "[00:01.000]你好世界\n"
)
_TEST_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml" '
    'xmlns:itunes="http://music.apple.com/lyric-ttml-internal" '
    'xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xml:lang="en" itunes:timing="Word">'
    '<head><metadata><iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal">'
    '<translations><translation xml:lang="zh-Hans-CN" type="subtitle">'
    '<text for="L1">你好世界</text>'
    '</translation></translations></iTunesMetadata></metadata></head>'
    '<body><div begin="00:01.000" end="00:03.000">'
    '<p begin="00:01.000" end="00:03.000" itunes:key="L1">'
    '<span begin="00:01.000" end="00:02.000">Hello</span> '
    '<span begin="00:02.000" end="00:03.000">world</span>'
    '</p></div></body></tt>'
)


def _create_minimal_wav(filepath: str) -> None:
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(b"\x00" * 8820)


def _create_minimal_flac(filepath: str) -> None:
    with open(filepath, "wb") as f:
        f.write(SYNTHETIC_FLAC_BYTES)
    f_obj = FLAC(filepath)
    if f_obj.tags is None:
        f_obj.add_tags()
    f_obj.save()


def _create_minimal_mp3(filepath: str) -> None:
    frame = b"\xff\xfb\x90\x00" + (b"\x00" * 413)
    with open(filepath, "wb") as f:
        f.write(frame * 4)
    id3 = ID3()
    id3.save(filepath)


def test_flac_container_standard_tag_roundtrip():
    """FLAC 容器：写入 lyrics + ttmllyric + 词曲，保护封面与 ReplayGain，回读通过。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.flac")
        dst = os.path.join(tmpdir, "out_test.flac")
        _create_minimal_flac(src)

        flac = FLAC(src)
        flac["title"] = ["原曲名"]
        flac["artist"] = ["原歌手"]
        flac["album"] = ["原专辑"]
        flac["date"] = ["2022"]
        flac["tracknumber"] = ["03"]
        flac["genre"] = ["Pop"]
        flac["replaygain_track_gain"] = ["-6.50 dB"]

        pic = FlacPicture()
        pic.type = 3
        pic.mime = "image/png"
        pic.data = _TINY_PNG_BYTES
        flac.add_picture(pic)
        flac.save()

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
            lyricist="作词人甲",
            composer="作曲人乙",
        )
        assert res["status"] == "PASS"

        readback = read_container_tags(dst)
        assert readback["lyrics"] == _TEST_ELRC
        assert readback["ttml"] == _TEST_TTML
        assert readback["lyricist"] == "作词人甲"
        assert readback["composer"] == "作曲人乙"
        assert readback["title"] == "原曲名"
        assert readback["artist"] == "原歌手"
        assert readback["album"] == "原专辑"
        assert readback["year"] == "2022"
        assert readback["tracknumber"] == "03"
        assert readback["genre"] == "Pop"
        assert readback["has_cover"] is True
        assert "replaygain_track_gain" in readback["replaygain"]


def test_mp3_container_standard_tag_roundtrip():
    """MP3 容器：写入 USLT::eng + TXXX:TTMLLYRIC + TEXT/TCOM，保护 APIC 封面。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.mp3")
        dst = os.path.join(tmpdir, "out_test.mp3")
        _create_minimal_mp3(src)

        id3 = ID3(src)
        id3["TIT2"] = TIT2(encoding=3, text=["流行主打"])
        id3["TPE1"] = TPE1(encoding=3, text=["实力唱将"])
        id3["TALB"] = TALB(encoding=3, text=["首张同名"])
        id3["TDRC"] = TDRC(encoding=3, text=["2023"])
        id3["TCON"] = TCON(encoding=3, text=["R&B"])
        id3["TPE2"] = TPE2(encoding=3, text=["群星"])
        id3["APIC:Cover"] = APIC(
            encoding=3,
            mime="image/png",
            type=3,
            desc="Cover",
            data=_TINY_PNG_BYTES,
        )
        id3.save(src)

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
            lyricist="词圣",
            composer="曲仙",
        )
        assert res["status"] == "PASS"

        readback = read_container_tags(dst)
        assert readback["lyrics"] == _TEST_ELRC
        assert readback["ttml"] == _TEST_TTML
        assert readback["lyricist"] == "词圣"
        assert readback["composer"] == "曲仙"
        assert readback["title"] == "流行主打"
        assert readback["artist"] == "实力唱将"
        assert readback["album"] == "首张同名"
        assert readback["albumartist"] == "群星"
        assert readback["has_cover"] is True


def test_wav_container_standard_tag_roundtrip():
    """WAV 容器：在 RIFF-ID3 块中写入双格式与词曲，音频数据块未损坏。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.wav")
        dst = os.path.join(tmpdir, "out_test.wav")
        _create_minimal_wav(src)

        wav = WAVE(src)
        if wav.tags is None:
            wav.add_tags()
        wav.tags["TIT2"] = TIT2(encoding=3, text=["无损母带"])
        wav.tags["TPE1"] = TPE1(encoding=3, text=["交响乐团"])
        wav.save()

        res = write_verified_standardized_container(
            source_audio_path=src,
            target_work_path=dst,
            elrc_text=_TEST_ELRC,
            ttml_text=_TEST_TTML,
            lyricist="古典大师",
            composer="交响大师",
        )
        assert res["status"] == "PASS"

        readback = read_container_tags(dst)
        assert readback["lyrics"] == _TEST_ELRC
        assert readback["ttml"] == _TEST_TTML
        assert readback["lyricist"] == "古典大师"
        assert readback["composer"] == "交响大师"
        assert readback["title"] == "无损母带"
        assert readback["artist"] == "交响乐团"

        with wave.open(dst, "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getframerate() == 44100


def test_fault_injection_rolls_back_and_destroys_copy():
    """故障注入：当回读或比对失败时，自动清理工作副本（零污染回滚）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = os.path.join(tmpdir, "test.flac")
        dst = os.path.join(tmpdir, "corrupted_out.flac")
        _create_minimal_flac(src)

        broken_ttml = '<tt><invalid_syntax>'
        failed = False
        try:
            write_verified_standardized_container(
                source_audio_path=src,
                target_work_path=dst,
                elrc_text=_TEST_ELRC,
                ttml_text=broken_ttml,
            )
        except Exception:
            failed = True

        assert failed is True
        assert not os.path.exists(dst)


if __name__ == "__main__":
    sys.exit(run(globals()))
