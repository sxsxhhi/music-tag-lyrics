# -*- coding: utf-8 -*-
"""v1.8.1 P0/P1 问题复现与回归测试集。

本模块在 Phase 0 编写，用于复现 v1.8.0 审计中发现的关键缺陷。
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
except ImportError:
    from tests import _testkit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.fetchers.lrc_red import LrcRedFetcher
from core.fetchers.apple_ttml import AppleTTMLFetcher
from core.utils.http import HttpResult

SAMPLE_TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:lrc="http://lrc.red/lyric-ttml-internal" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" lrc:timing="Word" xml:lang="en">
<head>
  <metadata>
    <ttm:agent type="person" xml:id="v1"/>
  </metadata>
</head>
<body dur="4:30.000">
  <div begin="10.000" end="20.000">
    <p begin="10.000" end="15.000" lrc:key="L1" ttm:agent="v1">
      <span begin="10.000" end="12.000">Hello</span> <span begin="12.000" end="15.000">world</span>
    </p>
  </div>
</body>
</tt>"""


def test_repro_c01_cache_cross_contamination():
    """复现 C01：A 音轨成功后，B 音轨时长不同不应复用 A 的验证结果。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
        "source": SAMPLE_TTML,
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        # 1. 歌曲 A：时长 270s -> 成功
        cand_a = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_a is not None, "Track A should succeed"

        # 2. 歌曲 B：同 ISRC，但时长 230s（差 40s，不合格）
        cand_b = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=230000, title="Song A", artist="Artist A")
        # 修复前：由于缓存以 isrc 为键缓存了 cand_a，此处 cand_b is not None（污染！）
        # 修复后：必须重新校验，返回 None
        assert cand_b is None, "Track B with mismatched duration must be rejected"


def test_repro_c02_negative_cache_lockout():
    """复现 C02：第一首因本地时长错误被拒，第二首正确时长不应被负缓存锁死。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
        "source": SAMPLE_TTML,
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        # 1. 歌曲 A：时长 100s（错误） -> 被拒
        cand_a = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=100000, title="Song A", artist="Artist A")
        assert cand_a is None

        # 2. 歌曲 B：同 ISRC，时长 270s（正确）
        # 修复前：由于 A 失败时写入 _cache[key] = None，此处 cand_b 也为 None（误判锁死！）
        # 修复后：应当成功获取
        cand_b = LrcRedFetcher.fetch_by_isrc("USUM71413645", duration_ms=270000, title="Song A", artist="Artist A")
        assert cand_b is not None, "Track B with correct duration should succeed despite track A failure"


def test_repro_m01_isrc_live_version_rejection():
    """复现 M01：ISRC 直查输入标题为 Song，远端为 Song (Live)，必须拒绝。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "title": "Hotel California (Live)",
        "artist": "Eagles",
        "duration": 390.0,
        "source": SAMPLE_TTML,
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        # 本地标题为录音室版 "Hotel California"
        cand = LrcRedFetcher.fetch_by_isrc(
            "USEE10000001",
            duration_ms=390000,
            title="Hotel California",
            artist="Eagles",
        )
        # 修复前：norm_title(keep_version=False) 抹去了 (Live)，导致被误接受
        # 修复后：版本互斥门禁拦截，返回 None
        assert cand is None, "Song vs Song (Live) must be rejected by version gate"


def test_repro_m04_remote_missing_meta_no_strict():
    """复现 M04：远端缺失 title 和 artist 时，不得伪造 strict。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "duration": 270.0,
        "source": SAMPLE_TTML,
        # 无 title，无 artist
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        cand = LrcRedFetcher.fetch_by_isrc(
            "USUM71413645",
            duration_ms=270000,
            title="Some Title",
            artist="Some Artist",
        )
        # 修复前：data.get("title") or title 回填本地字段，并在 match 中标记 strict=True
        # 修复后：远端证据缺失不得标记 strict
        if cand is not None:
            assert cand.get("match", {}).get("confidence") != "strict", "Missing remote metadata must not yield strict confidence"


def test_repro_q01_by_model_not_strict():
    """复现 Q01：远端 byModel=True 不得作为 T1 高可信原生逐字交付。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "title": "Test Track",
        "artist": "Test Artist",
        "duration": 270.0,
        "byModel": True,
        "source": SAMPLE_TTML,
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=30, error=""
        )

        cand = LrcRedFetcher.fetch_by_isrc(
            "USUM71413645",
            duration_ms=270000,
            title="Test Track",
            artist="Test Artist",
        )
        # 修复前：byModel=True 仍然给出 confidence="strict"
        # 修复后：byModel=True 被拒绝或不标记为 strict
        if cand is not None:
            assert cand.get("match", {}).get("confidence") != "strict", "byModel=True must not be strict T1"


def test_repro_a03_amll_zh_non_strict_fallback():
    """复现 A03：AMLL 华语未达 strict 时，应转入 lrc.red 兜底而非直接 return None。"""
    from core.parsers.ttml_parser import parse_ttml
    # 模拟 AMLL 抓取到华语歌词，但 strict 判定失败（如标题不完全一致或时长不达标）
    with mock.patch("core.fetchers.apple_ttml.fetch_ttml_raw") as mock_raw, \
         mock.patch("core.fetchers.apple_ttml.AmllIndex.get_instance") as mock_idx, \
         mock.patch("core.fetchers.lrc_red.LrcRedFetcher.fetch") as mock_lrc_red:
        
        # 让本地索引找到 candidate
        mock_entry = mock.MagicMock()
        mock_entry.ids = ["123456"]
        mock_entry.ambiguous = False
        mock_idx_instance = mock.MagicMock()
        mock_idx_instance.lookup.return_value = [mock_entry]
        mock_idx.return_value = mock_idx_instance

        # 模拟返回的 TTML 中缺少时长轴
        mock_raw.return_value = ("OK", SAMPLE_TTML, {"mirror": "test"})
        mock_lrc_red.return_value = {
            "source": "lrc-red",
            "lyric_type": "word",
            "lines": [{"start": 1000, "end": 2000, "text": "fallback"}],
        }

        # 华语歌曲：测试标题
        cand = AppleTTMLFetcher.fetch(title="你好世界", artist="测试歌手", duration_ms=270000)
        # 修复前：apple_ttml 华语未达 strict 或无轴时直接 return None，不调用 lrc.red
        # 修复后：应当调用 mock_lrc_red
        assert mock_lrc_red.called, "lrc.red fallback must be attempted when AMLL fails strict"


def test_repro_a06_song_json_404_fallback_to_ttml():
    """复现 A06：.song.json 为 404 时，备用 .ttml 和 .json 接口应有机会成功。"""
    LrcRedFetcher._cache.clear()
    meta_json = {
        "title": "Song A",
        "artist": "Artist A",
        "duration": 270.0,
    }

    def fake_http_get(url, **kwargs):
        if url.endswith(".song.json"):
            return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=30, error="")
        elif url.endswith(".json"):
            return HttpResult(status=200, text=json.dumps(meta_json), bytes_len=50, elapsed_ms=30, error="")
        elif url.endswith(".ttml"):
            return HttpResult(status=200, text=SAMPLE_TTML, bytes_len=len(SAMPLE_TTML), elapsed_ms=30, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex", side_effect=fake_http_get):
        cand = LrcRedFetcher.fetch_by_isrc(
            "USUM71413645",
            duration_ms=270000,
            title="Song A",
            artist="Artist A",
        )
        # 修复前：.song.json 404 立即 cache[isrc]=None 并 return None
        # 修复后：备用端点成功拉取
        assert cand is not None, "Fallback to .json and .ttml should succeed when .song.json returns 404"


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
