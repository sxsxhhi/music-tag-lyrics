# -*- coding: utf-8 -*-
"""H06 验收：搜索预筛不浪费前三名额，且不与 H05 冷却冲突。"""

import json
import os
import sys
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

TTML = (
    '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
    '<p begin="1.0" end="3.0" xml:id="L1">'
    '<span begin="1.0" end="2.0">Hello</span>'
    '<span begin="2.0" end="3.0">world</span></p>'
    "</div></body></tt>"
)

LOCAL_TITLE = "Example Song"
LOCAL_ARTIST = "Example Artist"
LOCAL_ALBUM = "Example Album"
LOCAL_DUR = 200000


def _hit(isrc, title, artist=LOCAL_ARTIST, duration=200.0, album=LOCAL_ALBUM):
    return {
        "isrc": isrc,
        "title": title,
        "artist": artist,
        "duration": duration,
        "album": album,
    }


def _reset():
    LrcRedFetcher.clear_cache()


class _Router:
    """按 URL 返回预设响应，并记录访问过的 ISRC。"""

    def __init__(self, hits, meta_album=LOCAL_ALBUM, meta_title=LOCAL_TITLE):
        self.hits = hits
        self.meta_album = meta_album
        self.meta_title = meta_title
        self.song_isrcs = []
        self.urls = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        if url.endswith("/search.json?q=Example%20Song%20Example%20Artist"):
            return _result(200, json.dumps({"hits": self.hits}))
        if url.endswith(".song.json"):
            isrc = url.rsplit("/", 1)[-1].replace(".song.json", "")
            self.song_isrcs.append(isrc)
            payload = {
                "title": self.meta_title,
                "artist": LOCAL_ARTIST,
                "album": self.meta_album,
                "duration": 200.0,
                "byModel": False,
                "source": TTML,
            }
            return _result(200, json.dumps(payload))
        return _result(404, "")


def _result(status, text="", headers=None):
    return HttpResult(
        status=status,
        text=text,
        bytes_len=len(text.encode("utf-8")) if text else 0,
        elapsed_ms=5,
        error="",
        headers=headers or {},
    )


def _search(router):
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=router):
        return LrcRedFetcher.fetch_by_search(
            LOCAL_TITLE, LOCAL_ARTIST, duration_ms=LOCAL_DUR, album=LOCAL_ALBUM
        )


# --------------------------------------------------------------------------- #
# 1. 前三个名额被版本冲突占满时，正确候选仍须得到下载机会
# --------------------------------------------------------------------------- #
def test_conflicting_head_does_not_starve_correct_candidate():
    """前 3 个候选分别与本地版本硬冲突，第 4 个才正确 → 必须尝试到第 4 个。"""
    _reset()
    try:
        hits = [
            _hit("USUM71413641", f"{LOCAL_TITLE} (Live)"),
            _hit("USUM71413642", f"{LOCAL_TITLE} (Remix)"),
            _hit("USUM71413643", f"{LOCAL_TITLE} (Cover)"),
            _hit("USUM71413644", LOCAL_TITLE),
            _hit("USUM71413645", f"{LOCAL_TITLE} (Instrumental)"),
        ]
        router = _Router(hits)
        cand = _search(router)
        assert cand is not None, "正确候选未被尝试"
        assert cand["isrc"] == "USUM71413644", cand["isrc"]
        # 预筛已剔除冲突项，不应先下载它们的 TTML
        assert router.song_isrcs == ["USUM71413644"], router.song_isrcs
    finally:
        _reset()


def test_album_level_conflict_also_prefiltered():
    """冲突写在专辑而非标题时，同样不得占用名额。"""
    _reset()
    try:
        hits = [
            _hit("USUM71413651", LOCAL_TITLE, album=f"{LOCAL_ALBUM} (Live)"),
            _hit("USUM71413652", LOCAL_TITLE, album=f"{LOCAL_ALBUM} (Remix)"),
            _hit("USUM71413653", LOCAL_TITLE, album=f"{LOCAL_ALBUM} (Cover)"),
            _hit("USUM71413654", LOCAL_TITLE),
        ]
        router = _Router(hits)
        cand = _search(router)
        assert cand is not None
        assert cand["isrc"] == "USUM71413654"
        assert router.song_isrcs == ["USUM71413654"], router.song_isrcs
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# 2. 未知版本证据：保留候选但不授权写回
# --------------------------------------------------------------------------- #
def test_unknown_album_evidence_kept_but_not_write_eligible():
    """本地有专辑信息、候选完全不给专辑 → 专辑侧版本无从确认：可保留，但不得写回 ISRC。"""
    _reset()
    try:
        hits = [_hit("USUM71413661", LOCAL_TITLE, album="")]
        router = _Router(hits, meta_album="")
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=router):
            cand = LrcRedFetcher.fetch_by_search(
                LOCAL_TITLE, LOCAL_ARTIST, duration_ms=LOCAL_DUR, album=LOCAL_ALBUM
            )
        assert cand is not None, "未知证据不应直接丢弃候选"
        assert cand["version_evidence"] == "unknown", cand.get("version_evidence")
        assert cand["isrc_write_eligible"] is False, "未知版本证据不得授权写回"
    finally:
        _reset()


def test_conflicting_evidence_rejected_at_final_validation():
    """预筛漏网时，最终验收仍用同一判据拒绝硬冲突候选。"""
    _reset()
    try:
        hits = [_hit("USUM71413662", f"{LOCAL_TITLE} (Live)", album="")]
        router = _Router(hits, meta_album=f"{LOCAL_ALBUM} (Live)", meta_title=f"{LOCAL_TITLE} (Live)")
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=router):
            cand = LrcRedFetcher.fetch_by_search(
                LOCAL_TITLE, LOCAL_ARTIST, duration_ms=LOCAL_DUR, album=LOCAL_ALBUM
            )
        assert cand is None, "最终验收未拒绝版本硬冲突候选"
    finally:
        _reset()


def test_missing_duration_not_scored_as_exact_match():
    """缺时长的候选不得因 dur_diff=0 而获得满分排名优势。"""
    _reset()
    try:
        hits = [
            _hit("USUM71413671", LOCAL_TITLE, duration=0),        # 时长未知
            _hit("USUM71413672", LOCAL_TITLE, duration=200.3),    # 时长贴合
        ]
        router = _Router(hits)
        cand = _search(router)
        assert cand is not None
        assert cand["isrc"] == "USUM71413672", "缺时长候选被当成精确匹配而占优"
    finally:
        _reset()


# --------------------------------------------------------------------------- #
# 3. 与 H05 交叉：预筛改动不得重新绕过 429 冷却
# --------------------------------------------------------------------------- #
def test_prefilter_still_respects_429_cooldown():
    """搜索入口在冷却期内不得发出任何请求（含预筛与后续下载）。"""
    _reset()
    try:
        calls = []

        def always_429(url, **kwargs):
            calls.append(url)
            return _result(429, "Too Many Requests")

        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=always_429):
            LrcRedFetcher._fetch_raw_record("USUM71413699")
        assert LrcRedFetcher._cooldown_remaining() > 0
        after_429 = len(calls)

        hits = [_hit("USUM71413681", LOCAL_TITLE)]
        router = _Router(hits)
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=router):
            assert LrcRedFetcher.fetch_by_search(LOCAL_TITLE, LOCAL_ARTIST) is None
        assert router.urls == [], f"冷却期内搜索仍发起了请求: {router.urls}"
        assert len(calls) == after_429
    finally:
        _reset()


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
