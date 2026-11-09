# -*- coding: utf-8 -*-
"""lrc.red 429 语义 H05 验收：单次计数、全入口冷却、Retry-After、恢复。

覆盖三条曾被绕过的入口：ISRC 直查、/match.json、/search.json，以及备用 .json/.ttml。
所有请求均由 mock 拦截，**不发起任何真实网络访问**。
"""

import os
import sys
import time
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import core.fetchers.lrc_red as lr_mod  # noqa: E402
from core.fetchers.lrc_red import LrcRedFetcher  # noqa: E402
from core.utils.http import HttpResult  # noqa: E402

lr_mod.MIN_REQ_INTERVAL_S = 0.0

SAMPLE_TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml"><body dur="0:30.000"><div>'
    '<p begin="1.000" end="4.000" xml:id="L1">'
    '<span begin="1.000" end="2.000">Hello</span>'
    '<span begin="2.000" end="3.000">world</span>'
    "</p></div></body></tt>"
)


class _Recorder:
    """记录每次 HTTP 调用并返回预设响应。"""

    def __init__(self, responder):
        self.urls = []
        self._responder = responder

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        return self._responder(url, len(self.urls))


def _reset():
    LrcRedFetcher.clear_cache()
    LrcRedFetcher.COOLDOWN_SEC = 60.0


def _result(status, text="", headers=None):
    return HttpResult(
        status=status,
        text=text,
        bytes_len=len(text.encode("utf-8")) if text else 0,
        elapsed_ms=5,
        error="",
        headers=headers or {},
    )


# --------------------------------------------------------------------------- #
# 1. 一次 429 只计一次（原缺陷：底层 + 上层各计一次，冷却翻倍）
# --------------------------------------------------------------------------- #
def test_single_429_counted_once():
    """单次真实 429 只能让 _consecutive_429 增加 1。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429, "Too Many Requests"))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            assert LrcRedFetcher._fetch_raw_record("USUM71413645") is None
        assert len(rec.urls) == 1, rec.urls
        assert LrcRedFetcher._consecutive_429 == 1, LrcRedFetcher._consecutive_429
    finally:
        _reset()


def test_isrc_429_stops_fallback_endpoints():
    """ISRC 直查 429 后不得继续请求备用 .json / .ttml。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429, "Too Many Requests"))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            assert LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=200000) is None
        assert len(rec.urls) == 1, f"429 之后仍追加了请求: {rec.urls}"
        assert rec.urls[0].endswith(".song.json")
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# 2. 冷却覆盖全部入口：match / search 也不得绕过
# --------------------------------------------------------------------------- #
def test_cooldown_blocks_match_and_search():
    """进入冷却后，/match.json 与 /search.json 均不得发出请求。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429, "Too Many Requests"))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
            sent_after_429 = len(rec.urls)

            assert LrcRedFetcher.fetch_by_match("Song", "Artist") is None
            assert LrcRedFetcher.fetch_by_search("Song", "Artist") is None

        assert len(rec.urls) == sent_after_429, f"冷却期内绕过了冷却检查: {rec.urls}"
    finally:
        _reset()


def test_cooldown_direct_http_helpers_blocked():
    """冷却期内底层 JSON/文本请求直接返回 0（未发出请求）。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
            before = len(rec.urls)
            status_json, payload = LrcRedFetcher._http_get_json("https://lrc.red/x.json")
            status_text, text = LrcRedFetcher._http_get_text("https://lrc.red/x.ttml")
        assert (status_json, payload) == (0, None)
        assert (status_text, text) == (0, None)
        assert len(rec.urls) == before
        assert LrcRedFetcher._cooldown_remaining() > 0
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# 3. Retry-After 解析与退避上限
# --------------------------------------------------------------------------- #
def test_retry_after_header_respected():
    """Retry-After 大于默认退避时以响应头为准。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429, "429", headers={"retry-after": "120"}))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
        remaining = LrcRedFetcher._cooldown_remaining()
        assert 110 <= remaining <= 125, remaining
    finally:
        _reset()


def test_retry_after_capped_at_300s():
    """异常大的 Retry-After 必须被 300s 上限截断。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429, "429", headers={"retry-after": "99999"}))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
        assert LrcRedFetcher._cooldown_remaining() <= 301
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# 4. 恢复与计数复位
# --------------------------------------------------------------------------- #
def test_cooldown_expiry_allows_requests_again():
    """冷却结束后必须恢复请求能力。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(429))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
            blocked = len(rec.urls)
            LrcRedFetcher._cooldown_until = time.perf_counter() - 1.0
            LrcRedFetcher._fetch_raw_record("USUM71413646")
            assert len(rec.urls) > blocked, "冷却解除后仍不发起请求"
    finally:
        _reset()


def test_success_resets_consecutive_counter():
    """成功请求后 429 连续计数复位。"""
    _reset()
    try:
        payload = (
            '{"title": "T", "artist": "A", "duration": 30.0, "source": '
            + repr(SAMPLE_TTML).replace("'", '"')
            + "}"
        )
        rec = _Recorder(lambda url, n: _result(429))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            LrcRedFetcher._fetch_raw_record("USUM71413645")
        assert LrcRedFetcher._consecutive_429 == 1

        LrcRedFetcher._cooldown_until = 0.0
        ok = _Recorder(lambda url, n: _result(200, payload))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=ok):
            rec_ok = LrcRedFetcher._fetch_raw_record("USUM71413646")
        assert rec_ok is not None, ok.urls
        assert LrcRedFetcher._consecutive_429 == 0
    finally:
        _reset()


def test_404_is_not_treated_as_429():
    """真实 404 不得激活 429 冷却。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(404, ""))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            assert LrcRedFetcher._fetch_raw_record("USUM71413645") is None
        assert LrcRedFetcher._consecutive_429 == 0
        assert LrcRedFetcher._cooldown_remaining() == 0
    finally:
        _reset()


def test_timeout_does_not_create_permanent_negative_cache():
    """超时/异常不得写入永久负缓存，后续仍可再次请求。"""
    _reset()
    try:
        rec = _Recorder(lambda url, n: _result(0, "", headers={}))
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=rec):
            assert LrcRedFetcher.fetch_by_isrc("USUM71413645") is None
            first = len(rec.urls)
            assert "USUM71413645" not in LrcRedFetcher._raw_cache
            LrcRedFetcher.fetch_by_isrc("USUM71413645")
            assert len(rec.urls) > first, "失败后未重新发起请求（疑似负缓存锁死）"
    finally:
        _reset()


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
