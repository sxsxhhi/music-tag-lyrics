# -*- coding: utf-8 -*-
"""v1.8.5 / H-A2 验收：FLAC 非受管元数据块的不可变性契约。

契约（写进 SKILL.md 的那一条必须与本套件一致）：

* 允许：块顺序变化、PADDING 数量与大小变化（写入库合并空闲空间是正常的）；
* 禁止：APPLICATION / SEEKTABLE / CUESHEET / 未知块类型的载荷或数量变化；
* STREAMINFO / VORBIS_COMMENT / PICTURE 不在此层判决（各有专用闸门）。
"""

import os
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

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest,
    flac_metadata_fingerprint,
    write_verified_standardized_container,
)
from tests.test_standard_container_tags import (
    _create_minimal_flac,
    _TEST_ELRC,
    _TEST_TTML,
)


def _split_blocks(data: bytes) -> tuple[list[bytes], bytes]:
    """把 fLaC 拆成完整的块字节列表（含头部）与尾部音频。"""
    assert data[:4] == b"fLaC"
    pos = 4
    blocks: list[bytes] = []
    while True:
        header = data[pos]
        size = int.from_bytes(data[pos + 1:pos + 4], "big")
        end = pos + 4 + size
        blocks.append(data[pos:end])
        pos = end
        if header & 0x80:
            break
    return blocks, data[pos:]


def _join_blocks(blocks: list[bytes], audio: bytes) -> bytes:
    out = bytearray(b"fLaC")
    for index, block in enumerate(blocks):
        header = bytearray(block[:4])
        header[0] = (header[0] & 0x7F) | (0x80 if index == len(blocks) - 1 else 0)
        out += header + block[4:]
    return bytes(out) + audio


def _block(type_code: int, payload: bytes) -> bytes:
    return bytes([type_code]) + len(payload).to_bytes(3, "big") + payload


def _with_extension_blocks(path: Path) -> tuple[bytes, bytes]:
    """插入 APPLICATION 与 SEEKTABLE，返回 (application 载荷, seektable 载荷)。

    SEEKTABLE 载荷长度必须是 18 的整数倍，否则 mutagen 判定非法并整块丢弃
    （实测：22 字节载荷会被丢弃，导致指纹误判为"被篡改"）。
    """
    blocks, audio = _split_blocks(path.read_bytes())
    app_payload = b"ORIG" + b"APP_PAYLOAD_DATA"
    seek_payload = b"\x00" * 18
    blocks.insert(1, _block(2, app_payload))
    blocks.insert(2, _block(3, seek_payload))
    path.write_bytes(_join_blocks(blocks, audio))
    return app_payload, seek_payload


def test_fingerprint_ignores_padding_and_order():
    """块顺序互换与 PADDING 增减不得改变指纹。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.flac"
        _create_minimal_flac(str(path))
        _with_extension_blocks(path)
        baseline = flac_metadata_fingerprint(str(path))
        assert baseline, "指纹不应为空"

        blocks, audio = _split_blocks(path.read_bytes())
        # 反转顺序（STREAMINFO 必须仍在首位，这里只反转其余块）
        reordered = [blocks[0]] + list(reversed(blocks[1:]))
        path.write_bytes(_join_blocks(reordered, audio))
        assert flac_metadata_fingerprint(str(path)) == baseline, "块重排被误判为篡改"

        blocks, audio = _split_blocks(path.read_bytes())
        blocks.append(_block(1, b"\x00" * 64))
        path.write_bytes(_join_blocks(blocks, audio))
        assert flac_metadata_fingerprint(str(path)) == baseline, "PADDING 变化被误判为篡改"


def test_fingerprint_detects_application_payload_change():
    """同长度载荷替换必须被发现（不能只看长度）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.flac"
        _create_minimal_flac(str(path))
        _with_extension_blocks(path)
        baseline = flac_metadata_fingerprint(str(path))
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b"ORIGAPP_PAYLOAD_DATA", b"XORIGAPP_PAYLOAD_DAT"))
        assert flac_metadata_fingerprint(str(path)) != baseline


def test_normal_flac_write_preserves_extension_blocks():
    """常规写入保留 APPLICATION/SEEKTABLE，且不误报。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "src.flac"
        dst = Path(tmpdir) / "dst.flac"
        _create_minimal_flac(str(src))
        app, seek = _with_extension_blocks(src)
        before = flac_metadata_fingerprint(str(src))
        result = write_verified_standardized_container(
            str(src), str(dst), _TEST_ELRC, _TEST_TTML
        )
        assert result["status"] == "PASS", result
        assert app in dst.read_bytes() and seek in dst.read_bytes()
        assert flac_metadata_fingerprint(str(dst)) == before
        assert compute_audio_stream_digest(str(dst))[0] == "VERIFIED"


def test_tampered_application_payload_blocks_delivery():
    """写入期间 APPLICATION 载荷被改动 → 必须阻断且不残留输出。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "src.flac"
        dst = Path(tmpdir) / "dst.flac"
        _create_minimal_flac(str(src))
        _with_extension_blocks(src)

        native_save = FLAC.save

        def tamper(flac_obj, *args, **kwargs):
            result = native_save(flac_obj, *args, **kwargs)
            target = Path(flac_obj.filename)
            target.write_bytes(
                target.read_bytes().replace(
                    b"ORIGAPP_PAYLOAD_DATA", b"XORIGAPP_PAYLOAD_DAT"
                )
            )
            return result

        with patch.object(FLAC, "save", tamper):
            try:
                result = write_verified_standardized_container(
                    str(src), str(dst), _TEST_ELRC, _TEST_TTML
                )
            except RuntimeError as exc:
                assert "flac_block" in str(exc), str(exc)
            else:
                raise AssertionError("篡改未被阻断: " + str(result["status"]))
        assert not dst.exists()


def test_unreadable_flac_returns_none():
    """结构不可解析时返回 None，而不是伪造一个"无变化"的指纹。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad.flac"
        path.write_bytes(b"fLaC\x80\xff\xff\xff")
        assert flac_metadata_fingerprint(str(path)) is None
        path.write_bytes(b"not-flac-at-all")
        assert flac_metadata_fingerprint(str(path)) is None


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
