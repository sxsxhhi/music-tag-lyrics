# -*- coding: utf-8 -*-
"""v1.8.5 / H-A1 验收：MP3 ID3v1 尾块必须在所有写入路径上逐字节保持。

背景：mutagen 的 ``ID3.save()`` 没有「只改 ID3v2、别碰 ID3v1」的选项
（``v1=0`` 删除、``v1=1`` 更新、``v1=2`` 创建），默认 ``v1=1`` 会把既有
ID3v1 的 title/artist/album/year 从 ID3v2 重写一遍，并**无条件清空 comment**。
音频流摘要刻意排除 ID3v1，所以改动完全不可见。本套件锁定修复后的契约。
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

from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, COMM

from core.adapters.container_tag_adapter import (
    compute_audio_stream_digest,
    read_container_tags,
    write_verified_standardized_container,
)
from core.utils.id3v1 import read_id3v1_tail
from scripts.tag_editor import update_audio_tags
from tests.test_standard_container_tags import _TEST_ELRC, _TEST_TTML

_MPEG_FRAME = b"\xff\xfb\x90\x00" + (b"\x00" * 413)


def _bare_mp3(path: Path) -> None:
    """只有 MPEG 帧、既无 ID3v2 也无 ID3v1。"""
    path.write_bytes(_MPEG_FRAME * 4)


def _v1_block(
    title: bytes = b"V1_TITLE",
    artist: bytes = b"V1_ARTIST",
    album: bytes = b"V1_ALBUM",
    year: bytes = b"1999",
    comment: bytes = b"V1_COMMENT",
    track: int = 7,
    genre: int = 17,
) -> bytes:
    def field(value: bytes) -> bytes:
        return value[:30].ljust(30, b"\0")

    return (
        b"TAG"
        + field(title)
        + field(artist)
        + field(album)
        + year
        + comment[:28].ljust(28, b"\0")
        + bytes([0, track])
        + bytes([genre])
    )


def _mp3_with_v2_and_v1(path: Path, *, with_album: bool = True) -> bytes:
    """构造 ID3v2 与 ID3v1 值不同的 MP3，返回原 ID3v1 尾块。"""
    _bare_mp3(path)
    id3 = ID3()
    id3.add(TIT2(encoding=0, text=["V2_TITLE"]))
    id3.add(TPE1(encoding=0, text=["V2_ARTIST"]))
    if with_album:
        id3.add(TALB(encoding=0, text=["V2_ALBUM"]))
        id3.add(TDRC(encoding=0, text=["2020"]))
        id3.add(COMM(encoding=0, lang="eng", desc="", text=["V2_COMMENT"]))
    id3.save(str(path), v1=0)
    tail = _v1_block()
    with path.open("ab") as handle:
        handle.write(tail)
    assert compute_audio_stream_digest(str(path))[0] == "VERIFIED"
    return tail


def _v1_fields(block: bytes) -> dict:
    return {
        "title": block[3:33].rstrip(b"\0"),
        "artist": block[33:63].rstrip(b"\0"),
        "album": block[63:93].rstrip(b"\0"),
        "year": block[93:97],
        "comment": block[97:125].rstrip(b"\0"),
        "track": block[126],
        "genre": block[127],
    }


def test_routine_write_keeps_all_id3v1_fields():
    """日常改签后，ID3v1 的 7 个字段全部保持不变（含 comment）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "a.mp3"
        before = _mp3_with_v2_and_v1(path)
        assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is True
        after = path.read_bytes()[-128:]
        assert after == before, f"ID3v1 被改写: {_v1_fields(before)} -> {_v1_fields(after)}"


def test_standardize_keeps_all_id3v1_fields():
    """标准化写回后，ID3v1 逐字节不变，且回读能自证。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "src.mp3"
        dst = Path(tmpdir) / "dst.mp3"
        before = _mp3_with_v2_and_v1(src)
        result = write_verified_standardized_container(
            str(src), str(dst), _TEST_ELRC, _TEST_TTML
        )
        assert result["status"] == "PASS"
        assert result["readback_tags"]["id3v1_preserved"] is True
        assert dst.read_bytes()[-128:] == before


def test_non_latin1_title_does_not_become_question_marks():
    """中文标题不得被 latin1 降级成 ???（真实曲库的主要受害场景）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "cjk.mp3"
        _bare_mp3(path)
        id3 = ID3()
        id3.add(TIT2(encoding=3, text=["晴天"]))
        id3.add(TPE1(encoding=3, text=["周杰伦"]))
        id3.save(str(path), v1=0)
        tail = _v1_block(title=b"", artist=b"", album=b"")
        with path.open("ab") as handle:
            handle.write(tail)
        assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is True
        after = path.read_bytes()[-128:]
        assert after == tail
        assert b"???" not in after[3:93], "ID3v1 出现 latin1 降级产生的问号"


def test_v2_missing_fields_leave_v1_values_intact():
    """ID3v2 缺 TALB/TDRC/COMM 时，ID3v1 对应字段不得被清空或覆盖。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "partial.mp3"
        before = _mp3_with_v2_and_v1(path, with_album=False)
        assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is True
        assert path.read_bytes()[-128:] == before


def test_no_id3v1_is_not_created():
    """原文件没有 ID3v1 时，写入不得凭空创建一个。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "plain.mp3"
        _bare_mp3(path)
        ID3().save(str(path))
        assert read_id3v1_tail(str(path)) is None
        assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is True
        assert read_id3v1_tail(str(path)) is None, "写入库凭空创建了 ID3v1"


def test_id3v1_only_file_keeps_tail_and_gains_v2():
    """只有 ID3v1、没有 ID3v2 的文件：尾块保持，同时正常补出 ID3v2。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "v1only.mp3"
        _bare_mp3(path)
        tail = _v1_block()
        with path.open("ab") as handle:
            handle.write(tail)
        assert read_id3v1_tail(str(path)) == tail
        assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is True
        assert path.read_bytes()[-128:] == tail
        assert path.read_bytes()[:3] == b"ID3", "未补出 ID3v2 头"


def test_created_id3v1_is_rejected_and_file_untouched():
    """写入库若凭空创建 ID3v1，必须整体拒绝落盘，原文件字节不变。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "guard.mp3"
        _bare_mp3(path)
        ID3().save(str(path))
        original = path.read_bytes()

        native_save = ID3.save

        def save_and_append(tag, *args, **kwargs):
            result = native_save(tag, *args, **kwargs)
            target = Path(tag.filename)
            with target.open("ab") as handle:
                handle.write(_v1_block())
            return result

        with patch.object(ID3, "save", save_and_append):
            assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is False
        assert path.read_bytes() == original, "被拒绝的写入仍然改动了原文件"


def test_duplicated_id3v1_is_rejected():
    """写入库若再追加一个 ID3v1，必须拒绝落盘。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "dup.mp3"
        before = _mp3_with_v2_and_v1(path)
        original = path.read_bytes()

        native_save = ID3.save

        def save_and_append(tag, *args, **kwargs):
            result = native_save(tag, *args, **kwargs)
            target = Path(tag.filename)
            with target.open("ab") as handle:
                handle.write(before)
            return result

        with patch.object(ID3, "save", save_and_append):
            assert update_audio_tags(str(path), lyrics=_TEST_ELRC) is False
        assert path.read_bytes() == original


def test_read_container_tags_reports_id3v1_facts():
    """回读接口必须把 ID3v1 的存在性与摘要暴露出来，否则无法自证。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "report.mp3"
        _mp3_with_v2_and_v1(path)
        info = read_container_tags(str(path))
        assert info["id3v1_present"] is True
        assert len(info["id3v1_sha256"]) == 64

        bare = Path(tmpdir) / "bare.mp3"
        _bare_mp3(bare)
        ID3().save(str(bare))
        info_bare = read_container_tags(str(bare))
        assert info_bare["id3v1_present"] is False
        assert info_bare["id3v1_sha256"] == ""


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
