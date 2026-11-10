# -*- coding: utf-8 -*-
"""v1.8.2 scripts/standardize_song.py CLI 与端到端标准化流程测试。"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mutagen.flac import FLAC
from scripts.standardize_song import standardize_single_song
from tests._testkit import run
from tests._synthetic_flac import SYNTHETIC_FLAC_BYTES

# The CLI fixture is deliberately monolingual: a same-timestamp Chinese
# line in bare ELRC has no trustworthy translation provenance and must trigger
# REVIEW_REQUIRED, not be silently consumed as an official translation.
_TEST_ELRC = (
    "[00:01.000]<00:01.000>Hello<00:02.000> world<00:03.000>\n"
)


def _create_minimal_flac(filepath: str) -> None:
    with open(filepath, "wb") as f:
        f.write(SYNTHETIC_FLAC_BYTES)
    f_obj = FLAC(filepath)
    if f_obj.tags is None:
        f_obj.add_tags()
    f_obj["title"] = ["测试曲目"]
    f_obj["artist"] = ["测试歌手"]
    f_obj["lyrics"] = [_TEST_ELRC]
    f_obj.save()


def test_cli_audit_mode_read_only():
    """默认 audit 模式只读，输出 JSON 报告，原音频绝对零修改。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio = os.path.join(tmpdir, "song.flac")
        report_file = os.path.join(tmpdir, "report.json")
        _create_minimal_flac(audio)
        orig_mtime = os.path.getmtime(audio)

        res = standardize_single_song(
            audio_path=audio,
            mode="audit",
            report_path=report_file,
        )
        assert res["status"] == "ONE_FORMAT_ONLY"
        assert res["format_audit"] == "ONE_FORMAT_ONLY"
        assert os.path.exists(report_file)
        with open(report_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["mode"] == "audit"
            assert data["prelude_plan"] is not None

        # 原音频完全未改动
        assert os.path.getmtime(audio) == orig_mtime


def test_cli_standardize_no_tags_sidecars():
    """standardize 模式搭配 --no-tags：生成三种 sidecar，.lrc 与 .spl 逐字节一致，不改动音频。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio = os.path.join(tmpdir, "song.flac")
        out_dir = os.path.join(tmpdir, "preview")
        _create_minimal_flac(audio)

        res = standardize_single_song(
            audio_path=audio,
            mode="standardize",
            output_dir=out_dir,
            no_tags=True,
        )
        assert res["status"] == "PASS"

        out_lrc = os.path.join(out_dir, "song.lrc")
        out_spl = os.path.join(out_dir, "song.spl")
        out_ttml = os.path.join(out_dir, "song.ttml")

        assert os.path.exists(out_lrc)
        assert os.path.exists(out_spl)
        assert os.path.exists(out_ttml)

        # 铁律：.lrc 与 .spl 逐字节哈希一致
        with open(out_lrc, "rb") as f1, open(out_spl, "rb") as f2:
            assert f1.read() == f2.read()

        # 确保输出目录中未生成音频文件
        assert not os.path.exists(os.path.join(out_dir, "song.flac"))


def test_cli_standardize_write_tags_working_copy():
    """standardize 模式搭配 --write-tags：在输出目录生成包含双歌词的工作副本并通过回读。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio = os.path.join(tmpdir, "song.flac")
        out_dir = os.path.join(tmpdir, "verified")
        _create_minimal_flac(audio)

        res = standardize_single_song(
            audio_path=audio,
            mode="standardize",
            output_dir=out_dir,
            write_tags=True,
        )
        assert res["status"] == "PASS"
        out_audio = os.path.join(out_dir, "song.flac")
        assert os.path.exists(out_audio)

        # 检查生成的工作副本内嵌双歌词
        flac = FLAC(out_audio)
        assert "lyrics" in flac
        assert "ttmllyric" in flac


def test_cli_mutually_exclusive_flags():
    """参数冲突检测：--no-tags 与 --write-tags 并存立即抛出异常。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        audio = os.path.join(tmpdir, "song.flac")
        _create_minimal_flac(audio)

        failed = False
        try:
            standardize_single_song(
                audio_path=audio,
                mode="standardize",
                no_tags=True,
                write_tags=True,
            )
        except ValueError:
            failed = True

        assert failed is True


if __name__ == "__main__":
    sys.exit(run(globals()))
