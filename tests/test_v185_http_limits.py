# -*- coding: utf-8 -*-
"""v1.8.5 / H-C1 验收：抓取层必须对响应体设上限。

背景：``http_get_text`` / ``http_get_json`` / ``http_post_json`` /
``http_post_json_body`` 此前直接 ``resp.read()``，异常或恶意的大型响应会
无界占用内存；同文件的 ``http_get_bytes`` / ``http_get_text_ex`` 早就有
``max_bytes``，属于两套 API 并存留下的缺口。

本套件断言两件事：
1. 超限时返回 ``None``（保持既有"取不到"契约，不抛异常）；
2. **读取本身是受限的**——必须是 ``read(max_bytes + 1)``，而不是先整体读入再判断。
"""

import json
import os
import sys
import urllib.request
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

from core.utils import http as http_mod


class _FakeResponse:
    """最小 urllib 响应替身，记录每次 read() 请求的长度。"""

    def __init__(self, payload: bytes):
        self._payload = payload
        self._pos = 0
        self.read_sizes: list = []

    def read(self, amount: int = -1) -> bytes:
        self.read_sizes.append(amount)
        if amount is None or amount < 0:
            chunk = self._payload[self._pos:]
            self._pos = len(self._payload)
            return chunk
        chunk = self._payload[self._pos:self._pos + amount]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _patch_urlopen(payload: bytes):
    response = _FakeResponse(payload)
    return response, patch.object(urllib.request, "urlopen", return_value=response)


def test_oversized_text_returns_none_and_reads_bounded():
    """超限文本返回 None，且只读了 max_bytes+1 个字节。"""
    payload = b"x" * 5000
    response, patcher = _patch_urlopen(payload)
    with patcher:
        assert http_mod.http_get_text("http://example.invalid/a", max_bytes=1024) is None
    assert response.read_sizes == [1025], response.read_sizes
    assert response._pos == 1025, "读取位置超过上限，说明发生了无界读"


def test_normal_text_still_decoded():
    """未超限时行为不变。"""
    payload = "歌词内容".encode("utf-8")
    _, patcher = _patch_urlopen(payload)
    with patcher:
        text = http_mod.http_get_text("http://example.invalid/b", max_bytes=1024)
    assert text == "歌词内容"


def test_default_limit_is_applied_when_not_specified():
    """不传 max_bytes 时也要有默认上限，而不是退回无界。"""
    assert http_mod.DEFAULT_MAX_BYTES > 0
    payload = b"y" * (http_mod.DEFAULT_MAX_BYTES + 10)
    response, patcher = _patch_urlopen(payload)
    with patcher:
        assert http_mod.http_get_text("http://example.invalid/c") is None
    assert response.read_sizes == [http_mod.DEFAULT_MAX_BYTES + 1]


def test_json_variants_respect_limit():
    """GET JSON / POST JSON / POST JSON body 三个入口都受限。"""
    big = json.dumps({"data": "z" * 5000}).encode("utf-8")
    for call in (
        lambda: http_mod.http_get_json("http://example.invalid/d", max_bytes=512),
        lambda: http_mod.http_post_json("http://example.invalid/e", {"a": 1}, max_bytes=512),
        lambda: http_mod.http_post_json_body(
            "http://example.invalid/f", {"a": 1}, max_bytes=512
        ),
    ):
        _, patcher = _patch_urlopen(big)
        with patcher:
            assert call() is None


def test_small_json_roundtrip():
    """小响应正常解析，确认上限没有误伤。"""
    payload = json.dumps({"hits": []}).encode("utf-8")
    _, patcher = _patch_urlopen(payload)
    with patcher:
        assert http_mod.http_get_json("http://example.invalid/g", max_bytes=4096) == {"hits": []}
    _, patcher = _patch_urlopen(payload)
    with patcher:
        assert http_mod.http_post_json("http://example.invalid/h", {"q": 1}, max_bytes=4096) == {
            "hits": []
        }


def test_explicit_zero_keeps_legacy_unbounded_read():
    """max_bytes<=0 是显式退出开关，仍按旧语义整体读取（供特殊调用方使用）。"""
    payload = b"k" * 4096
    response, patcher = _patch_urlopen(payload)
    with patcher:
        text = http_mod.http_get_text("http://example.invalid/i", max_bytes=0)
    assert text == "k" * 4096
    assert response.read_sizes == [-1]


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
