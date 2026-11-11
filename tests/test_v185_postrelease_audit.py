# -*- coding: utf-8 -*-
"""v1.8.5 B0：把 v1.8.4 独立审计的 5 条反例固化为仓库内可重复执行的资产。

来源：music-tag-lyrics-v1.8.4-GitHub-independent-audit/v184_postrelease_regressions.py
（2026-11-11 复核，5 tests / 5 FAIL 在 v1.8.4 上全部复现）

本套件是「红基线」：在未修复的版本上必须失败；修复后必须全部转绿。
它断言的是**安全属性**，不是实现细节：
  A1 标准化写回不得改写既有 ID3v1 尾块
  A2 日常改签不得改写既有 ID3v1 尾块
  A3 FLAC 非注释元数据块被篡改时必须阻断
  A4 报告写入与回滚都失败时不得把残留产物伪装成完全回滚
  A5 真实编码 MP3（FFmpeg）同上，缺 ffmpeg 时 SKIP
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, TPE1

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest,
    write_verified_standardized_container,
)
from scripts.tag_editor import update_audio_tags
import scripts.standardize_song as standardize_mod
from tests.test_standard_container_tags import (
    _create_minimal_flac,
    _create_minimal_mp3,
    _TEST_ELRC,
    _TEST_TTML,
)

_ID3V1_PAD = 30


def _v1_field(text: bytes, size: int = _ID3V1_PAD) -> bytes:
    return text[:_ID3V1_PAD].ljust(size, b"\0")


def _id3v1_bytes() -> bytes:
    return (
        b"TAG"
        + _v1_field(b"LEGACY_V1_TITLE")
        + _v1_field(b"LEGACY_V1_ARTIST")
        + _v1_field(b"Legacy Album")
        + b"1999"
        + _v1_field(b"Legacy comments")
        + bytes([13])
    )


def _prepare_mp3(path: Path, *, real: bool = False) -> bytes:
    """构造「同时有 ID3v2 与 ID3v1、且两者值不同」的 MP3，返回原尾块。"""
    if real:
        if not shutil.which("ffmpeg"):
            _testkit.skip("ffmpeg absent; synthetic MP3 cases still run")
        subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=1.25", "-c:a", "libmp3lame",
                "-b:a", "128k", str(path),
            ],
            check=True,
        )
    else:
        _create_minimal_mp3(str(path))
    tags = ID3(str(path))
    tags.add(TIT2(encoding=3, text=["V2_TITLE"]))
    tags.add(TPE1(encoding=3, text=["V2_ARTIST"]))
    tags.save(str(path), v1=0)
    with path.open("ab") as f:
        f.write(_id3v1_bytes())
    assert compute_audio_stream_digest(str(path))[0] == "VERIFIED"
    return path.read_bytes()[-128:]


def _inject_flac_application(path: Path) -> bytes:
    """在 FLAC 元数据区插入一个合法 APPLICATION 块（0x82 = last|type2）。"""
    raw = bytearray(path.read_bytes())
    assert raw[:4] == b"fLaC"
    pos = 4
    while True:
        header = raw[pos]
        size = int.from_bytes(raw[pos + 1:pos + 4], "big")
        end = pos + 4 + size
        if header & 0x80:
            break
        pos = end
    raw[pos] &= 0x7F  # 取消原最后一块的 last 标记
    payload = b"USER" + b"ORIGINAL_APP_PAYLOAD_12345"
    raw[end:end] = bytes([0x82]) + len(payload).to_bytes(3, "big") + payload
    path.write_bytes(bytes(raw))
    return payload


def test_a1_standardize_preserves_id3v1_tail():
    """A1：标准化写回 MP3 后，ID3v1 尾块必须逐字节不变。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.mp3"
        dst = Path(tmpdir) / "dest.mp3"
        before = _prepare_mp3(src)
        result = write_verified_standardized_container(
            str(src), str(dst), _TEST_ELRC, _TEST_TTML
        )
        assert result["status"] == "PASS", result
        assert dst.read_bytes()[-128:] == before, (
            "标准化静默改写了受保护的 ID3v1 尾块"
        )


def test_a2_routine_write_preserves_id3v1_tail():
    """A2：日常仅改歌词时，ID3v1 尾块必须逐字节不变。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.mp3"
        before = _prepare_mp3(src)
        assert update_audio_tags(str(src), lyrics=_TEST_ELRC) is True
        assert src.read_bytes()[-128:] == before, (
            "日常改签改写了受保护的 ID3v1 标题/艺术家/注释"
        )


def test_a3_flac_application_corruption_must_be_blocked():
    """A3：FLAC APPLICATION 载荷在工作副本保存期间被改动时必须阻断。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.flac"
        dst = Path(tmpdir) / "dest.flac"
        _create_minimal_flac(str(src))
        payload = _inject_flac_application(src)
        assert payload in src.read_bytes()
        assert compute_audio_stream_digest(str(src))[0] == "VERIFIED"

        native_save = FLAC.save

        def tamper(flac_obj, *args, **kwargs):
            result = native_save(flac_obj, *args, **kwargs)
            target = Path(flac_obj.filename)
            data = target.read_bytes()
            assert payload in data
            target.write_bytes(
                data.replace(b"ORIGINAL_APP_PAYLOAD_12345", b"CORRUPT__APP_PAYLOAD_12345")
            )
            return result

        with patch.object(FLAC, "save", tamper):
            try:
                result = write_verified_standardized_container(
                    str(src), str(dst), _TEST_ELRC, _TEST_TTML
                )
            except (RuntimeError, ValueError):
                pass
            else:
                raise AssertionError(
                    "写入器放过了被篡改的 APPLICATION 块，返回 " + str(result["status"])
                )
        assert not dst.exists(), "被拒绝的输出不得残留"


def test_a4_report_and_rollback_failure_must_be_declared():
    """A4：报告写入失败 + 回滚失败时，必须声明回滚不完整并列出残留路径。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "source.flac"
        out = Path(tmpdir) / "out"
        report = Path(tmpdir) / "report.json"
        _create_minimal_flac(str(src))
        flac = FLAC(str(src))
        flac["lyrics"] = [_TEST_ELRC]
        flac["ttmllyric"] = [_TEST_TTML]
        flac.save()

        native_unlink = os.unlink
        blocked = {str(out / "source.lrc")}

        def refuse_unlink(target, *args, **kwargs):
            if str(target) in blocked:
                raise PermissionError("synthetic rollback refusal")
            return native_unlink(target, *args, **kwargs)

        with patch.object(
            standardize_mod,
            "_write_json_report",
            side_effect=PermissionError("synthetic report write failure"),
        ):
            with patch.object(standardize_mod.os, "unlink", side_effect=refuse_unlink):
                try:
                    result = standardize_mod.standardize_single_song(
                        str(src), mode="standardize", output_dir=str(out),
                        report_path=str(report),
                    )
                except (OSError, RuntimeError):
                    return
        residual = out / "source.lrc"
        if not residual.exists():
            return
        codes = [issue["code"] for issue in result["issues"]]
        assert "SIDECAR_ROLLBACK_INCOMPLETE" in codes or "ROLLBACK_INCOMPLETE" in codes, (
            "回滚不完整未被声明，输出仍存在：" + repr(codes)
        )
        assert result.get("residual_outputs"), "残留产物未进入结构化字段"


def test_a5_real_encoded_mp3_preserves_id3v1():
    """A5：真实 FFmpeg 编码 MP3 上的 ID3v1 保持（无 ffmpeg 时 SKIP）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "real.mp3"
        dst = Path(tmpdir) / "dest.mp3"
        before = _prepare_mp3(src, real=True)
        result = write_verified_standardized_container(
            str(src), str(dst), _TEST_ELRC, _TEST_TTML
        )
        assert result["status"] == "PASS", result
        assert dst.read_bytes()[-128:] == before, "实际编码 MP3 的 ID3v1 尾块被改写"


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
