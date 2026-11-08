# -*- coding: utf-8 -*-
"""lrc.red 歌词客户端、解析适配器与 ISRC 工具单元测试 (v1.8.0)。"""

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

from core.utils.isrc import normalize_isrc, is_valid_isrc
from core.parsers.lrc_red_ttml import parse_lrc_red_ttml, normalize_lrc_red_namespaces
from core.fetchers.lrc_red import LrcRedFetcher
from core.utils.http import HttpResult

# 模拟 lrc.red 样本 TTML
SAMPLE_LRC_RED_TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:lrc="http://lrc.red/lyric-ttml-internal" xmlns:ttm="http://www.w3.org/ns/ttml#metadata" lrc:timing="Word" xml:lang="en">
<head>
  <metadata>
    <ttm:agent type="person" xml:id="v1"/>
    <sourceMetadata xmlns="http://lrc.red/lyric-ttml-internal" leadingSilence="0.140">
      <translations>
        <translation xml:lang="zh-Hans">这是中文译文</translation>
      </translations>
      <songwriters>
        <songwriter>Mark Ronson</songwriter>
        <songwriter>Bruno Mars</songwriter>
      </songwriters>
    </sourceMetadata>
  </metadata>
</head>
<body dur="4:29.658">
  <div begin="16.906" end="21.036">
    <p begin="16.906" end="18.766" lrc:key="L1" ttm:agent="v1">
      <span begin="16.906" end="17.266">This</span> <span begin="17.266" end="17.687">hit,</span> <span begin="17.687" end="18.010">that</span> <span begin="18.010" end="18.418">ice</span> <span begin="18.418" end="18.766">cold</span>
      <span ttm:role="x-bg" begin="18.500" end="18.766">cold</span>
    </p>
    <p begin="18.766" end="21.036" lrc:key="L2" ttm:agent="v1">
      <span begin="18.766" end="19.324">Michelle</span> <span begin="19.324" end="19.772">Pfeiffer</span>
      <span ttm:role="x-translation">米歇尔·菲佛</span>
    </p>
  </div>
</body>
</tt>"""


def test_isrc_normalization():
    """测试 ISRC 提取、分隔符清洗与大写格式化。"""
    assert normalize_isrc("GBARL1401524") == "GBARL1401524"
    assert normalize_isrc("gb-arl-14-01524") == "GBARL1401524"
    assert normalize_isrc("US-UM7-14-13645") == "USUM71413645"
    assert normalize_isrc(" cn-a01-02-304-00 ") == "CNA010230400"

    # 非法长度或字符
    assert normalize_isrc("") is None
    assert normalize_isrc("GBARL140152") is None       # 11 位
    assert normalize_isrc("GBARL14015240") is None     # 13 位
    assert normalize_isrc("12ARL1401524") is None      # 国家码前两位必须是字母
    assert normalize_isrc(None) is None


def test_isrc_validation():
    """测试 ISRC 有效性判定布尔函数。"""
    assert is_valid_isrc("GBARL1401524") is True
    assert is_valid_isrc("invalid_isrc") is False
    assert is_valid_isrc(None) is False


def test_lrc_red_namespace_normalization():
    """测试将 xmlns:lrc 转为 xmlns:itunes 命名空间。"""
    norm = normalize_lrc_red_namespaces(SAMPLE_LRC_RED_TTML)
    assert 'xmlns:lrc=' not in norm
    assert 'xmlns:itunes="http://music.apple.com/lyric-ttml-internal"' in norm
    assert 'itunes:timing="Word"' in norm
    assert 'itunes:key="L1"' in norm


def test_lrc_red_ttml_parser_metadata_injection():
    """测试元数据注入机制：保证缺失 <amll:meta> 时仍能获得 tags["ti"] 和 tags["ar"]。"""
    meta = {
        "title": "Uptown Funk",
        "artist": "Mark Ronson / Bruno Mars",
        "album": "Uptown Special",
        "duration": 269.658,
    }
    parsed = parse_lrc_red_ttml(SAMPLE_LRC_RED_TTML, metadata=meta)

    # 验证 tags 成功注入，严防下游门禁误杀
    tags = parsed.get("tags") or {}
    assert tags.get("ti") == "Uptown Funk"
    assert tags.get("ar") == "Mark Ronson / Bruno Mars"
    assert tags.get("al") == "Uptown Special"

    # 验证时长解析
    assert parsed.get("duration_ms") == 269658

    # 验证词曲作者
    songwriters = parsed.get("songwriters") or []
    assert "Mark Ronson" in songwriters
    assert "Bruno Mars" in songwriters


def test_lrc_red_ttml_parser_words_and_roles():
    """测试逐词时间轴提取、背景声过滤与译文抽取。"""
    parsed = parse_lrc_red_ttml(SAMPLE_LRC_RED_TTML)
    lines = parsed.get("lines") or []
    assert len(lines) == 2

    # 第一行验证
    line1 = lines[0]
    assert line1["start"] == 16906
    assert line1["end"] == 18766
    words1 = line1.get("words") or []
    assert len(words1) == 5  # This, hit,, that, ice, cold (背景声 cold 应被剔除)
    assert words1[0]["text"] == "This "
    assert words1[0]["start"] == 16906
    assert words1[0]["end"] == 17266
    assert words1[4]["text"] == "cold"
    assert line1["text"] == "This hit, that ice cold"

    # 第二行验证（包含译文）
    line2 = lines[1]
    assert line2["start"] == 18766
    assert line2["end"] == 21036
    assert line2.get("translation") == "米歇尔·菲佛"
    assert parsed.get("has_verbatim") is True
    assert parsed.get("has_translation") is True


def test_fetch_by_isrc_success():
    """Mock 测试：ISRC 直查成功返回标准候选。"""
    LrcRedFetcher._cache.clear()
    LrcRedFetcher._last_req_time = 0.0

    mock_json = {
        "title": "Uptown Funk (feat. Bruno Mars)",
        "artist": "Mark Ronson",
        "album": "Uptown Special",
        "duration": 270.613,
        "sync": "word",
        "source": SAMPLE_LRC_RED_TTML,
    }

    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200,
            text=json.dumps(mock_json),
            bytes_len=len(json.dumps(mock_json)),
            elapsed_ms=80,
            error="",
        )

        cand = LrcRedFetcher.fetch_by_isrc(
            isrc="GBARL1401524",
            duration_ms=270000,
            title="Uptown Funk",
            artist="Mark Ronson",
        )

        assert cand is not None
        assert cand["source"] == "lrc-red"
        assert cand["lyric_type"] == "word"
        assert cand["isrc"] == "GBARL1401524"
        assert cand["match"]["confidence"] == "strict"
        assert len(cand["lines"]) == 2
        assert cand["tags"]["ti"] == "Uptown Funk (feat. Bruno Mars)"


def test_fetch_by_isrc_404():
    """Mock 测试：404 返回 None 并不重试。"""
    LrcRedFetcher._cache.clear()
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=404, text="", bytes_len=0, elapsed_ms=50, error=""
        )
        cand = LrcRedFetcher.fetch_by_isrc("USUM71413645")
        assert cand is None


def test_fetch_duration_mismatch_rejected():
    """Mock 测试：时长差超过 5000ms 时触发门禁拒绝。"""
    LrcRedFetcher._cache.clear()
    mock_json = {
        "title": "Uptown Funk",
        "artist": "Mark Ronson",
        "duration": 270.613,  # ~270s
        "source": SAMPLE_LRC_RED_TTML,
    }
    with mock.patch("core.fetchers.lrc_red.http_get_text_ex") as mock_http:
        mock_http.return_value = HttpResult(
            status=200, text=json.dumps(mock_json), bytes_len=100, elapsed_ms=40, error=""
        )
        # 本地音轨时长为 230s（差 40s，远大于 5s 门禁）
        cand = LrcRedFetcher.fetch_by_isrc("GBARL1401524", duration_ms=230000)
        assert cand is None


def test_rank_and_filter_hits_medley_rejected():
    """测试候选集仲裁：串烧/翻唱因时长或艺人不符被成功过滤。"""
    hits = [
        {
            "isrc": "QZ22B1933030",
            "title": "24K Magic / Perm / Uptown Funk (Medley)",
            "artist": "Berklee Bruno Mars Ensemble",
            "duration": 230.274,
        },
        {
            "isrc": "GBARL1401524",
            "title": "Uptown Funk (feat. Bruno Mars)",
            "artist": "Mark Ronson",
            "duration": 270.613,
        },
    ]

    # 本地歌曲为 Mark Ronson 的 Uptown Funk，时长 270000ms
    best = LrcRedFetcher._rank_and_filter_hits(
        hits, title="Uptown Funk", artist="Mark Ronson", duration_ms=270000
    )

    # 验证串烧版被成功剔除，原版排第一
    assert len(best) == 1
    assert best[0]["isrc"] == "GBARL1401524"
    assert best[0]["artist"] == "Mark Ronson"


def test_fetch_env_switch_off():
    """测试环境变量禁用开关 MTL_LRC_RED=off。"""
    with mock.patch.dict(os.environ, {"MTL_LRC_RED": "off"}):
        cand = LrcRedFetcher.fetch("Uptown Funk", "Mark Ronson", isrc="GBARL1401524")
        assert cand is None


def test_apple_ttml_fallback_to_lrc_red():
    """测试集成：当 AppleTTMLFetcher 在 AMLL 404 时，无缝触发 lrc.red 兜底。"""
    from core.fetchers.apple_ttml import AppleTTMLFetcher
    mock_lrc_red_cand = {
        "source": "lrc-red",
        "lyric_type": "word",
        "lines": [{"start": 1000, "end": 2000, "text": "test"}],
        "match": {"confidence": "strict"},
    }
    with mock.patch.object(AppleTTMLFetcher, "fetch_by_apple_id_ex", return_value=(None, "NOT_FOUND")), \
         mock.patch("core.fetchers.apple_ttml.fetch_ttml_raw", return_value=("NOT_FOUND", "", {})), \
         mock.patch.object(LrcRedFetcher, "fetch", return_value=mock_lrc_red_cand) as mock_lr:
        cand = AppleTTMLFetcher.fetch("Uptown Funk", "Mark Ronson", isrc="GBARL1401524")
        assert cand is not None
        assert cand["source"] == "lrc-red"
        mock_lr.assert_called_once()


def test_tag_editor_isrc_read_write_vorbis_and_id3():
    """测试 tag_editor 中 ISRC 字段在 TagSet 规格表与读写操作中的正确传递。"""
    from scripts.tag_editor import TagSet, _apply_fields
    from mutagen.id3 import ID3, TSRC

    # 1. 验证 TagSet 规格表正确识别 isrc
    ts = TagSet(title="Test Song", isrc="GBARL1401524").normalized()
    assert ts.isrc == "GBARL1401524"

    # 2. 验证 ID3 写入 TSRC
    fake_id3 = ID3()
    _apply_fields(ts, fake_id3, use_id3=True)
    assert "TSRC" in fake_id3
    assert fake_id3["TSRC"].text == ["GBARL1401524"]

    # 3. 验证 Vorbis 写入 isrc
    fake_vorbis = {}
    _apply_fields(ts, fake_vorbis, use_id3=False)
    assert fake_vorbis["isrc"] == ["GBARL1401524"]


def test_cli_flags_registered():
    """测试 process_song 命令行参数 --write-isrc 与 --no-lrc-red 正常注册。"""
    import subprocess
    cmd = [sys.executable, os.path.join(REPO_ROOT, "scripts", "process_song.py"), "--help"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert res.returncode == 0
    assert "--write-isrc" in res.stdout
    assert "--no-lrc-red" in res.stdout


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
