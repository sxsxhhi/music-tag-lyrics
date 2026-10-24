# -*- coding: utf-8 -*-
"""专辑封面补全单元测试（R9）：魔数门禁、原图 URL、三级梯级与进程内去重。全离线。"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import core.fetchers.cover_art as ca
from core.fetchers.cover_art import (
    CoverFetcher, sniff_image_mime, apple_original_url,
    MAGIC_JPEG, MAGIC_PNG,
)
from scripts.tag_editor import has_embedded_cover

# 构造合法测试图：魔数 + 填充到 >1KB
JPEG_OK = MAGIC_JPEG + b"\xff" * 2048
PNG_OK = MAGIC_PNG + b"\x00" * 2048
HTML_SHELL = b"<!DOCTYPE html><html>404</html>" + b"x" * 2048
TINY_JPEG = MAGIC_JPEG + b"\xff" * 100  # <1KB 必拒


def _patch_download(fn):
    orig = ca._download_image
    ca._download_image = fn
    return orig


def test_c1_sniff_image_magic():
    """魔数判型：JPEG/PNG 认，HTML 壳与空数据拒。"""
    assert sniff_image_mime(JPEG_OK) == "image/jpeg"
    assert sniff_image_mime(PNG_OK) == "image/png"
    assert sniff_image_mime(HTML_SHELL) is None
    assert sniff_image_mime(b"") is None
    assert sniff_image_mime(b"\x00" * 100) is None


def test_c2_apple_original_url():
    """100x100bb -> 1000x1000bb 官方参数化替换；无 token 原样返回。"""
    assert apple_original_url(
        "https://is1-ssl.mzstatic.com/image/thumb/Music/abc/100x100bb.jpg"
    ) == "https://is1-ssl.mzstatic.com/image/thumb/Music/abc/1000x1000bb.jpg"
    assert apple_original_url("https://example.com/cover.jpg") == "https://example.com/cover.jpg"
    assert apple_original_url("") == ""


def test_c3_tier1_apple_free_ride():
    """T1 顺风车：给 artwork URL 即出图，不走网易/酷狗。"""
    calls = []

    def fake_dl(url, timeout=10.0):
        calls.append(url)
        return JPEG_OK

    CoverFetcher.reset_instance()
    orig = _patch_download(fake_dl)
    try:
        cf = CoverFetcher.get_instance()
        got = cf.fetch_cover("测试曲", "测试歌手", 200000,
                             apple_artwork_url="https://is1-ssl.mzstatic.com/a/100x100bb.jpg")
        assert got is not None
        data, mime = got
        assert mime == "image/jpeg"
        assert calls and "1000x1000bb.jpg" in calls[0], calls
        assert len(calls) == 1
    finally:
        ca._download_image = orig
        CoverFetcher.reset_instance()


def test_c4_tier1_bad_falls_to_tier2():
    """T1 返回 HTML 壳/坏图 -> 丢弃并落 T2 网易。"""
    def fake_dl(url, timeout=10.0):
        if "mzstatic" in url:
            return HTML_SHELL  # 原图坏
        if url == "https://mzstatic-thumb/100x100bb.jpg":
            return TINY_JPEG   # 缩略图退回仍 <1KB
        if "music.126.net" in url:
            return PNG_OK
        return None

    CoverFetcher.reset_instance()
    orig = _patch_download(fake_dl)
    try:
        cf = CoverFetcher.get_instance()
        cf._netease_album_art = lambda *a, **k: "https://p1.music.126.net/xx.jpg"
        cf._kugou_album_art = lambda *a, **k: ""
        got = cf.fetch_cover("测试曲", "测试歌手", 200000,
                             apple_artwork_url="https://mzstatic-thumb/100x100bb.jpg")
        assert got is not None
        assert got[1] == "image/png"
    finally:
        ca._download_image = orig
        CoverFetcher.reset_instance()


def test_c5_in_process_dedup():
    """进程内去重：同一 (title, artist) 失败过一次后不再发起任何下载。"""
    calls = []

    def fake_dl(url, timeout=10.0):
        calls.append(url)
        return None

    CoverFetcher.reset_instance()
    orig = _patch_download(fake_dl)
    try:
        cf = CoverFetcher.get_instance()
        cf._netease_album_art = lambda *a, **k: "https://p1.music.126.net/yy.jpg"
        cf._kugou_album_art = lambda *a, **k: "https://imge.kugou.com/zz.jpg"
        assert cf.fetch_cover("去重曲", "甲", 100000) is None
        n_after_first = len(calls)
        assert n_after_first >= 1
        assert cf.fetch_cover("去重曲", "甲", 100000) is None
        assert len(calls) == n_after_first, "同曲第二轮不得再发起下载"
    finally:
        ca._download_image = orig
        CoverFetcher.reset_instance()


def test_c6_all_tiers_empty_returns_none():
    """三级全空：返回 None，不抛异常。"""
    CoverFetcher.reset_instance()
    orig = _patch_download(lambda url, timeout=10.0: None)
    try:
        cf = CoverFetcher.get_instance()
        cf._netease_album_art = lambda *a, **k: ""
        cf._kugou_album_art = lambda *a, **k: ""
        assert cf.fetch_cover("无图曲", "乙", 100000) is None
        assert cf.fetch_cover("", "谁", 100000) is None  # 空标题直接 None
    finally:
        ca._download_image = orig
        CoverFetcher.reset_instance()


def test_c7_has_embedded_cover_missing_file():
    """检测函数对不存在路径返回 False，不抛异常。"""
    assert has_embedded_cover("") is False
    assert has_embedded_cover("Z:/definitely/not/exist.flac") is False


def test_c8_update_audio_tags_cover_params_safe():
    """update_audio_tags 新增封面参数后：对不存在的文件仍安全返回 False。"""
    from scripts.tag_editor import update_audio_tags
    assert update_audio_tags(
        "Z:/definitely/not/exist.mp3",
        title="t", cover_data=JPEG_OK, cover_mime="image/jpeg",
    ) is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {t.__name__}: {exc}")
            import traceback
            traceback.print_exc()
            failed.append(t.__name__)

    print(f"\n{passed}/{len(tests)} passed")
    if failed:
        print(f"FAILED: {failed}")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
