# -*- coding: utf-8 -*-
"""H18 验收：按 lrc.red 官方 API 条款提供可联系 UA 与署名信息。

条款证据：https://lrc.red/api（2026-11-09 核验）原文：
  "You must send a User-Agent naming your site or app, with a link and email
   address to contact."
  "You must clearly credit and link back to lrc.red wherever its data is shown."
"""

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
from core.fetchers.lrc_red import (  # noqa: E402
    LRC_RED_ATTRIBUTION,
    LrcRedFetcher,
    build_lrc_red_ua,
)
from core.utils.http import HttpResult  # noqa: E402

lr_mod.MIN_REQ_INTERVAL_S = 0.0


def _with_contact(value):
    old = os.environ.get("MTL_LRC_RED_CONTACT")
    if value is None:
        os.environ.pop("MTL_LRC_RED_CONTACT", None)
    else:
        os.environ["MTL_LRC_RED_CONTACT"] = value
    return old


def _restore(old):
    if old is None:
        os.environ.pop("MTL_LRC_RED_CONTACT", None)
    else:
        os.environ["MTL_LRC_RED_CONTACT"] = old


def test_ua_has_app_name_link_and_contact_field():
    ua = build_lrc_red_ua()
    assert ua.startswith("music-tag-lyrics/"), ua
    assert "https://github.com/sxsxhhi/music-tag-lyrics" in ua
    assert "contact:" in ua, "UA 缺少条款要求的联系方式字段"
    assert "\n" not in ua and len(ua) < 300


def test_ua_does_not_hardcode_personal_mailbox():
    """默认 UA 不得包含任何硬编码邮箱（联系方式由环境变量注入）。"""
    old = _with_contact(None)
    try:
        ua = build_lrc_red_ua()
        assert "@" not in ua.split("github.com", 1)[1], f"UA 疑似硬编码邮箱: {ua}"
    finally:
        _restore(old)


def test_ua_contact_env_injected():
    old = _with_contact("ops@example.invalid")
    try:
        ua = build_lrc_red_ua()
        assert "ops@example.invalid" in ua, ua
    finally:
        _restore(old)


def test_attribution_constant_is_official_site():
    assert LRC_RED_ATTRIBUTION == "https://lrc.red"


def test_candidate_carries_attribution():
    ttml = (
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="1.0" end="3.0"><span begin="1.0" end="2.0">Hi</span>'
        '<span begin="2.0" end="3.0">there</span></p></div></body></tt>'
    )
    payload = json.dumps(
        {"title": "T", "artist": "A", "album": "Al", "duration": 200.0,
         "byModel": False, "source": ttml}
    )

    def fake(url, **kwargs):
        return HttpResult(status=200, text=payload, bytes_len=len(payload), elapsed_ms=5, error="")

    LrcRedFetcher.clear_cache()
    try:
        with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=fake):
            cand = LrcRedFetcher.fetch_by_isrc(
                "USUM71413645", duration_ms=200000, title="T", artist="A", album="Al"
            )
        assert cand is not None
        assert cand["provenance"]["attribution"] == "https://lrc.red"
        assert cand["provenance"]["provider"] == "lrc.red"
    finally:
        LrcRedFetcher.clear_cache()


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
