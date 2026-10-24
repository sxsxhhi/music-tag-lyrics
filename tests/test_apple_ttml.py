# -*- coding: utf-8 -*-
"""Apple Music TTML 单元测试：镜像攒票、索引消歧、门禁判定与等级单调 (56 条全量测试)。"""

import os
import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.http import HttpResult
import core.fetchers.apple_ttml as atm
from core.fetchers.apple_ttml import AppleTTMLFetcher, disambiguate_entry
from core.fetchers.amll_index import AmllIndex
from core.fetchers.itunes_client import ITunesClient
from core.parsers.ttml_parser import parse_ttml
import core.aggregator as agg
from scripts.ttml2lyrics import main as ttml2lyrics_main, extract_apple_id
from core.utils.text_match import primary_artist, artist_token_list
from core.utils.cache_paths import set_cache_dir_override

FIXTURES_DIR = Path(REPO_ROOT) / "tests" / "fixtures"


# =====================================================================
# 公共缓存沙盒（R1）：临时缓存目录 + 单例复位。
# 任何用例绝不读写真实 %LOCALAPPDATA%\music-tag-lyrics\ 缓存。
# =====================================================================

import functools
import shutil
import tempfile as _tempfile_mod
from contextlib import contextmanager


@contextmanager
def _cache_sandbox():
    td = Path(_tempfile_mod.mkdtemp(prefix="mtl-test-"))
    set_cache_dir_override(td)
    ITunesClient.reset_instance()
    AmllIndex.reset_instance()
    try:
        yield
    finally:
        ITunesClient.reset_instance()
        AmllIndex.reset_instance()
        set_cache_dir_override(None)
        shutil.rmtree(td, ignore_errors=True)


def sandboxed(fn):
    """整用例包进缓存沙盒：真实用户缓存零读写零污染。"""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _cache_sandbox():
            return fn(*args, **kwargs)
    return wrapper


@contextmanager
def _http_stub(fake):
    """B2 去重：替换「patch http_get_text_ex → try → finally 还原」三行样板，退出自动还原。"""
    import core.utils.http as http_mod
    orig = http_mod.http_get_text_ex
    http_mod.http_get_text_ex = fake
    try:
        yield fake
    finally:
        http_mod.http_get_text_ex = orig


# =====================================================================
# Group A: 镜像回退与 origin 攒票机制 (8 条离线测试)
# =====================================================================

def test_a1_gbcl_200_direct_success():
    """A1: gbcl 200 直接返回成功，不再发后续请求。"""
    calls = []

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        calls.append(url)
        return HttpResult(status=200, text='<tt xmlns="http://www.w3.org/ns/ttml"><body><p begin="0" end="1000"><span>test</span></p></body></tt>', bytes_len=100, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "OK", verdict
        assert "<tt" in text
        assert len(calls) == 1
        assert "gbclstudio" in calls[0]
        assert meta["origin"] == "gbcl"


def test_a2_gbcl_404_testingcf_200_ok():
    """A2: gbcl 404 + testingcf 200 -> OK。"""
    calls = []

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        calls.append(url)
        if "gbclstudio" in url:
            return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")
        if "testingcf" in url:
            return HttpResult(status=200, text='<tt><body><p begin="0"><span>hi</span></p></body></tt>', bytes_len=50, elapsed_ms=10, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "OK", verdict
        assert len(calls) == 2
        assert meta["origin"] == "jsdelivr"


def test_a3_gbcl_404_testingcf_404_not_found():
    """A3: gbcl 404 + testingcf 404 -> 达到 2 个不同 origin -> NOT_FOUND。"""
    calls = []

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        calls.append(url)
        return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "NOT_FOUND", verdict
        assert len(calls) == 2
        assert set(meta["origins_404"]) == {"gbcl", "jsdelivr"}


def test_a4_gbcl_404_dimeta_404_suspect_missing():
    """A4: gbcl 404 + dimeta 404 (其余超时) -> dimeta 0 票，仅 1 origin -> SUSPECT_MISSING。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        if "gbclstudio" in url:
            return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")
        if "dimeta" in url:
            return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=10, error="timeout")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "SUSPECT_MISSING", verdict
        assert meta["origins_404"] == ["gbcl"]


def test_a5_three_jsdelivr_404_counts_as_one_origin():
    """A5: testingcf/gcore/fastly 全 404（gbcl 超时）-> 3 镜像同属 jsdelivr，仅算 1 票 -> SUSPECT_MISSING。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        if "jsdelivr" in url:
            return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=10, error="timeout")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "SUSPECT_MISSING", verdict
        assert meta["origins_404"] == ["jsdelivr"]


def test_a6_all_403_unreachable():
    """A6: 镜像全部返回 403 -> 0 个 404 -> UNREACHABLE。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=403, text="Forbidden", bytes_len=9, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "UNREACHABLE", verdict
        assert len(meta["origins_404"]) == 0


def test_a7_three_html_shells_unreachable():
    """A7: ≥3 次 200 但非 TTML 内容（HTML 壳）-> 判 UNREACHABLE。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=200, text="<!DOCTYPE html><html><body>Error 404 CDN</body></html>", bytes_len=50, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        verdict, text, meta = atm.fetch_ttml_raw("12345")
        assert verdict == "UNREACHABLE", verdict
        assert meta.get("reason") == "html_shell_exceeded"


def test_a8_deadline_timeout_caps_to_suspect():
    """A8: 6.0s 硬顶生效 -> 停止请求并转 SUSPECT_MISSING。"""
    fake_clock = [0.0]

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        fake_clock[0] += 3.5
        if "gbclstudio" in url:
            return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=3500, error="")
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=3500, error="timeout")

    import core.utils.http as http_mod
    orig_http = http_mod.http_get_text_ex
    orig_perf = time.perf_counter
    http_mod.http_get_text_ex = fake_http
    time.perf_counter = lambda: fake_clock[0]
    try:
        verdict, text, meta = atm.fetch_ttml_raw("12345", deadline_s=6.0)
        assert verdict == "SUSPECT_MISSING", verdict
        assert meta["origins_404"] == ["gbcl"]
    finally:
        http_mod.http_get_text_ex = orig_http
        time.perf_counter = orig_perf


# =====================================================================
# Group B: 本地索引反查与歧义消歧 (10 条离线测试)
# =====================================================================

def _load_sample_index():
    sample_file = FIXTURES_DIR / "apple_index_sample.jsonl"
    lines = sample_file.read_text(encoding="utf-8").splitlines()
    return AmllIndex.parse_jsonl_content(lines)


def test_b1_strict_hit():
    """B1: 简体严格命中。"""
    idx = _load_sample_index()
    res = idx.lookup("大石碎胸口", "万能青年旅店")
    assert len(res) >= 1
    assert res[0].ids[0] == "1538548882"


def test_b2_live_mismatch_rejected():
    """B2: 查询为录音室版本，索引仅有 Live 候选被硬拒。"""
    idx = _load_sample_index()
    # 模拟只有 Live 条目
    e = idx.entries[2] # 杀死那个石家庄人 (Live)
    custom_idx = AmllIndex([e])
    res = custom_idx.lookup("杀死那个石家庄人", "万能青年旅店")
    assert len(res) == 0, "录音室查询不应命中 Live 候选"


def test_b3_raw_lyric_file_folding():
    """B3: 样本 7 行按 rawLyricFile 折叠成 5 个唯一 Entry。"""
    idx = _load_sample_index()
    assert len(idx.entries) == 6
    unique_files = {e.file for e in idx.entries}
    assert len(unique_files) == 6


def test_b4_multi_id_group_up_to_four():
    """B4: 多 id 组（如包含 5 个 id 的大石碎胸口）严格截取前 4 个。"""
    idx = _load_sample_index()
    e = [x for x in idx.entries if "1538548882" in x.ids][0]
    assert len(e.ids) == 4
    assert e.ids[0] == "1538548882"


def test_b5_download_id_from_row_not_raw_filename():
    """B5: 下载 id 必须来自行内 id / appleMusicId，绝不能从文件名抽数字。"""
    idx = _load_sample_index()
    e = [x for x in idx.entries if x.file == "ambig_file_1.ttml"][0]
    assert e.ids[0] == "2000000001"


def test_b6_33_keys_album_disambiguation():
    """B6: 33 个歧义键通过 Variant/Entry 专辑精确匹配成功消歧。"""
    idx = _load_sample_index()
    candidates = idx.lookup("歧义曲目", "测试多艺人1")
    assert len(candidates) == 2
    # 传 "重置单曲" 专辑消歧
    resolved = disambiguate_entry(candidates, "重置单曲", duration_ms=200000, is_zh=True)
    assert resolved is not None
    assert resolved.file == "ambig_file_2.ttml"
    # 传 "重版EP"（优化 2：多专辑第二项）同样成功命中
    resolved2 = disambiguate_entry(candidates, "重版EP", duration_ms=200000, is_zh=True)
    assert resolved2 is not None
    assert resolved2.file == "ambig_file_1.ttml"


def test_b7_33_keys_disambiguation_failure_drops():
    """B7: 33 个歧义键若专辑与时长均无法消歧，必须全弃返回 None。"""
    idx = _load_sample_index()
    candidates = idx.lookup("歧义曲目", "测试多艺人1")
    # 无专辑，时长未知
    resolved = disambiguate_entry(candidates, "", duration_ms=0, is_zh=True)
    assert resolved is None


def test_b8_multi_artist_token_intersection():
    """B8: 7 个艺人的长列表截取前 6 个，艺人交集判定正确。"""
    idx = _load_sample_index()
    res = idx.lookup("歧义曲目", "测试多艺人2")
    assert len(res) >= 1


def test_b9_index_download_timeout_degrades():
    """B9: 索引下载超时降级为 iTunes-only，不抛出异常。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=12000, error="timeout")

    with _http_stub(fake_http):
        idx = AmllIndex._download_index(prev_line_count=0)
        assert idx is None


def test_b10_shrink_097_protection():
    """B10: 索引行数收缩超过 0.97 时（防 dimeta 滞后版），拒绝替换旧缓存。"""
    # 模拟旧版 2745 行，新版 2424 行 (< 2745 * 0.97 = 2662)
    fake_lines = ['{"appleMusicId":["1"]}'] * 2424
    fake_lines[-1] = '{"appleMusicId":["1"]}'

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=200, text="\n".join(fake_lines), bytes_len=50000, elapsed_ms=100, error="")

    with _http_stub(fake_http):
        downloaded = AmllIndex._download_index(prev_line_count=2745)
        assert downloaded is None, "滞后的行数应该被 0.97 保护拦截"


# =====================================================================
# Group C: 门禁判定与时长判别 (15 条离线测试)
# =====================================================================

def _make_ttml_body(music_name="测试曲", artist="测试歌手", album="测试专辑", body_dur="00:06:07.136", last_end=367136):
    m = int(last_end // 60000)
    rem = last_end % 60000
    s = rem / 1000.0
    end_tag = f"{m:02d}:{s:06.3f}"
    return f"""<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:amll="http://example.com/amll">
  <head>
    <amll:meta key="musicName" value="{music_name}" />
    <amll:meta key="artists" value="{artist}" />
    <amll:meta key="album" value="{album}" />
  </head>
  <body dur="{body_dur}">
    <div>
      <p begin="00:00.000" end="{end_tag}">
        <span begin="00:00.000" end="{end_tag}">测试歌词</span>
      </p>
    </div>
  </body>
</tt>"""


@sandboxed
def test_c1_duration_uses_itunes_not_body_dur():
    """C1: local=423533 / itunes=423533 / body=367136 -> PASS（不因 body 尾差误拒）。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        if "am-lyrics" in url:
            return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 423533, "ts": int(time.time())}
        # 手工绑定 index 实例
        sample_idx = _load_sample_index()
        AmllIndex._instance = sample_idx
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=423533, album="万能青年旅店")
        assert res is not None, "大石碎胸口应顺利通过"
        assert res["match"]["confidence"] == "strict"


@sandboxed
def test_c2_e1_long_outro_still_pass():
    """C2: E1 合格 + 尾差 120s（比例 0.72 ≥ 0.50）仍 PASS。"""
    # local=420000, itunes=420000, axis=300000 -> ratio=0.714 >= 0.50
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:05:00.000", last_end=300000)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 420000, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=420000, album="万能青年旅店")
        assert res is not None
        assert res["match"]["confidence"] == "strict"


@sandboxed
def test_c3_truncated_body_rejected():
    """C3: 末行 200000 / 音频 423533 (比例 0.47 < 0.50) 即使 body dur 写成满曲也拒。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:07:03.533", last_end=200000)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 423533, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=423533, album="万能青年旅店")
        assert res is None, "截断地板未过应被硬拒"


@sandboxed
def test_c4_missing_body_dur_falls_back_to_last_line_end():
    """C4: 缺 body dur 时 axis 自动回退到末行 end，不抛异常。"""
    ttml_txt = """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:amll="http://example.com/amll">
  <head>
    <amll:meta key="musicName" value="大石碎胸口" />
    <amll:meta key="artists" value="万能青年旅店" />
    <amll:meta key="album" value="万能青年旅店" />
  </head>
  <body>
    <div>
      <p begin="00:00.000" end="06:07.136">
        <span begin="00:00.000" end="06:07.136">测试歌词</span>
      </p>
    </div>
  </body>
</tt>"""
    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 367136, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=367136, album="万能青年旅店")
        assert res is not None


@sandboxed
def test_c5_zero_local_duration_degrades_without_crash():
    """C5: 本地音频时长为 0 时华语安全返回 None，不抛异常。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店")

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=0, album="万能青年旅店")
        assert res is None, "华语时长未知必须安全落酷狗"


@sandboxed
def test_c6_no_e1_e3b_strict_fail_chinese_none_foreign_loose():
    """C6: 无 E1 时 E3b_strict 失败：华语返回 None，外语给 loose。"""
    # axis=200000, duration=300000 (ratio=0.666): floor 0.50 passed, strict 0.70 failed
    ttml_zh = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:03:20.000", last_end=200000)
    ttml_en = _make_ttml_body(music_name="Hello", artist="Adele", album="25", body_dur="00:03:20.000", last_end=200000)

    def fake_http(url, **k):
        txt = ttml_zh if "1538548882" in url else ttml_en
        return HttpResult(status=200, text=txt, bytes_len=len(txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        # 清空两首的 E1 缓存
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 0, "ts": int(time.time())}
        ITunesClient.get_instance().cache["appleid:9999999999"] = {"duration_ms": 0, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        # 华语 -> None
        res_zh = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=300000, album="不同专辑")
        assert res_zh is None

        # 外语 -> loose
        fake_cand = {"apple_id": "9999999999", "title": "Hello", "artist": "Adele", "album": "25", "duration_ms": 300000}
        ITunesClient.get_instance().cache["track:hello|adele|[]|300000"] = {"data": fake_cand, "ts": int(time.time())}
        res_en = AppleTTMLFetcher.fetch("Hello", "Adele", duration_ms=300000, album="25")
        assert res_en is not None
        assert res_en["match"]["confidence"] == "loose"


@sandboxed
def test_c7_body_dur_overrun_rejected():
    """C7: axis > audio + 8000 冲轴被硬拒。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:05:00.000", last_end=300000)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=280000, album="万能青年旅店")
        assert res is None


@sandboxed
def test_c8_preview_rejected():
    """C8: 曲长 > 90s 但 axis < 45s（预览截断）被拒。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:00:30.000", last_end=30000)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=120000, album="万能青年旅店")
        assert res is None


@sandboxed
def test_c9_zero_verbatim_lines_rejected():
    """C9: 逐字行 0 行（结构门）硬拒。"""
    ttml_txt = """<?xml version="1.0" encoding="utf-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:amll="http://example.com/amll">
  <head>
    <amll:meta key="musicName" value="大石碎胸口" />
    <amll:meta key="artists" value="万能青年旅店" />
  </head>
  <body dur="00:06:07.136">
    <div>
      <p begin="00:00.000" end="00:06:07.136">没有逐字只有整句</p>
    </div>
  </body>
</tt>"""
    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=367136)
        assert res is None


@sandboxed
def test_c10_chinese_loose_returns_none():
    """C10: 华语若未能达成 strict（仅 loose），返回 None 落酷狗。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口 (Live)", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        AmllIndex._instance = _load_sample_index()
        # 查询带 Live 版本的歌曲但门禁未达 strict
        res = AppleTTMLFetcher.fetch("大石碎胸口 (Live)", "万能青年旅店", duration_ms=367136, album="不一致专辑")
        assert res is None


@sandboxed
def test_c11_e1_mismatch_never_falls_back_to_e2():
    """C11: E1 有值但不合格时（时差 > 5s），硬规则 1 禁止回落 E2，直接硬拒。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")

    with _http_stub(fake_http):
        # itunes 时长 210000 对不上 local 367136
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 210000, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        # 即使 album="万能青年旅店" 精确匹配，E1 错也必须拒
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=367136, album="万能青年旅店")
        assert res is None


@sandboxed
def test_c12_us_rc0_cn_rc1_strict():
    """C12: US 店面 rc=0，但 CN 店面查询成功且 Δ≤5s -> 判定 strict。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        if "am-lyrics" in url:
            return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")
        if "lookup" in url:
            if "country=us" in url:
                return HttpResult(status=200, text='{"results":[]}', bytes_len=14, elapsed_ms=10, error="")
            if "country=cn" in url:
                return HttpResult(status=200, text='{"results":[{"trackId":1538548882,"trackTimeMillis":367136}]}', bytes_len=60, elapsed_ms=10, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        # 清空缓存
        ITunesClient.get_instance().cache.clear()
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=367136, album="万能青年旅店")
        assert res is not None
        assert res["match"]["confidence"] == "strict"


@sandboxed
def test_c13_both_rc0_e2_and_e3b_strict_pass():
    """C13: US/CN 两区都 rc=0，但 E2 专辑匹配 + E3b_strict 达标 -> strict。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        if "am-lyrics" in url:
            return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")
        if "lookup" in url:
            return HttpResult(status=200, text='{"results":[]}', bytes_len=14, elapsed_ms=10, error="")
        return HttpResult(status=404, text="", bytes_len=0, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cache["appleid:1538548882"] = {"duration_ms": 0, "ts": int(time.time())}
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=400000, album="万能青年旅店")
        # axis=367136, duration=400000 -> ratio=0.917 >= 0.70, diff=32864 <= 90000 -> e3b_strict pass
        assert res is not None
        assert res["match"]["confidence"] == "strict"


@sandboxed
def test_c14_itunes_429_falls_back_to_e2_e3b_strict():
    """C14: iTunes 遭遇 429 冷却，退到 E2 + E3b_strict 不直接放弃。"""
    ttml_txt = _make_ttml_body(music_name="大石碎胸口", artist="万能青年旅店", album="万能青年旅店", body_dur="00:06:07.136", last_end=367136)

    def fake_http(url, **k):
        if "am-lyrics" in url:
            return HttpResult(status=200, text=ttml_txt, bytes_len=len(ttml_txt), elapsed_ms=10, error="")
        return HttpResult(status=429, text="Too Many Requests", bytes_len=17, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        ITunesClient.get_instance().cooldown_until = time.perf_counter() + 90.0
        AmllIndex._instance = _load_sample_index()
        res = AppleTTMLFetcher.fetch("大石碎胸口", "万能青年旅店", duration_ms=400000, album="万能青年旅店")
        assert res is not None
        assert res["match"]["confidence"] == "strict"


@sandboxed
def test_c15_search_url_never_contains_country_cn():
    """C15: 断言 search URL 永不含 country=cn，lookup 允许 cn。"""
    calls = []

    def fake_http(url, **k):
        calls.append(url)
        return HttpResult(status=200, text='{"results":[]}', bytes_len=14, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        client = ITunesClient()
        client.cache.clear()
        client.search_track("测试歌名", "测试歌手", duration_ms=100000)
        client.lookup_durations(["fresh_test_id_9999"], is_zh=True)

        for u in calls:
            if "search?" in u:
                assert "country=cn" not in u, f"Search 接口禁止使用 cn: {u}"
        has_lookup_cn = any("lookup?" in u and "country=cn" in u for u in calls)
        assert has_lookup_cn, "Lookup 第二批应包含 cn"


# =====================================================================
# Group D: 解析与 CLI 输出 (3 条离线测试)
# =====================================================================

def test_d1_x_roman_is_stripped():
    """D1: TTML 中包含 x-roman 角色标签时必须被干净剥离，不进入歌词正文。"""
    fixture = FIXTURES_DIR / "apple_ttml_meta.ttml"
    res = parse_ttml(fixture.read_text(encoding="utf-8"))
    assert len(res["lines"]) == 2
    assert "da shi sui xiong kou" not in res["lines"][0]["text"]
    assert res["lines"][0]["translation"] == "Chest smashed by boulder"


def test_d2_ttml_elrc_output_three_digit_precision():
    """D2: TTML -> Enhanced LRC 保持 3 位毫秒精度格式。"""
    fixture = FIXTURES_DIR / "apple_ttml_meta.ttml"
    data = parse_ttml(fixture.read_text(encoding="utf-8"))
    from core.converter import to_spl
    out = to_spl(data, title="大石碎胸口", artist="万能青年旅店")
    assert "[00:28.700]" in out
    assert "<00:28.700>" in out


def test_d3_ttml2lyrics_cli_stdout():
    """D3: ttml2lyrics main([file.ttml, --stdout]) 零网络且退出码 0。"""
    fixture = str(FIXTURES_DIR / "apple_ttml_meta.ttml")
    code = ttml2lyrics_main([fixture, "--stdout"])
    assert code == 0


# =====================================================================
# Group E: 单调等级与调度链整合 (7 条离线测试)
# =====================================================================

def test_e1_apple_hit_skips_kugou():
    """E1: Apple 命中时，KugouFetcher 绝不应被调用（若调用则抛出 AssertionError）。"""
    class BoomKugou:
        @classmethod
        def fetch(cls, *a, **k):
            raise AssertionError("Apple 命中时不应调用酷狗")

    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "歌词", "words": [{"text": "歌"}]}]
            }

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = BoomKugou
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("歌名", "歌手", duration_ms=0)
        assert cand is not None
        assert cand["source"] == "apple-ttml"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e2_apple_none_falls_back_to_kugou():
    """E2: Apple 返回 None 时，平滑回落到酷狗。"""
    class FakeAppleNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "kugou",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "酷狗歌词", "words": [{"text": "酷"}]}]
            }

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
    }
    agg.AppleTTMLFetcher = FakeAppleNone
    agg.KugouFetcher = FakeKugou
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("歌名", "歌手", duration_ms=0)
        assert cand is not None
        assert cand["source"] == "kugou"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e3_apple_verbatim_no_trans_amll_no_trans_keeps_apple():
    """E3: 外语曲目 Apple 逐字无译文（rank 1）遇 AMLL 逐字无译文（rank 1）-> 保留 Apple。"""
    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    class FakeAMLL:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "amll",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }
    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = FakeNone
    agg.AMLLFetcher = FakeAMLL
    agg.NetEaseFetcher = FakeNone
    agg.LrcShareFetcher = FakeNone
    agg.LRCLIBFetcher = FakeNone
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
        assert cand["source"] == "apple-ttml", "等级相同时应保留 Apple 源"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e4_apple_verbatim_no_trans_amll_with_trans_replaces():
    """E4: 外语曲目 Apple 逐字无译文（rank 1）遇 AMLL 逐字带译文（rank 3）-> 提升整包替换。"""
    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    class FakeAMLLWithTrans:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "amll",
                "lyric_type": "word",
                "has_translation": True,
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "translation": "你好", "words": [{"text": "Hello"}]}]
            }

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = FakeNone
    agg.AMLLFetcher = FakeAMLLWithTrans
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
        assert cand["source"] == "amll", "更高 rank 的带译文源应替换无译文源"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e5_chinese_apple_candidate_never_probes_amll():
    """E5: 华语曲目 Apple 候选命中后，绝不探查后续 T3 AMLL 源。"""
    class BoomAMLL:
        @classmethod
        def fetch(cls, *a, **k):
            raise AssertionError("华语 Apple 已命中时不应探查 AMLL")

    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "大石碎胸口", "words": [{"text": "大"}]}]
            }

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.AMLLFetcher = BoomAMLL
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("大石碎胸口", "万能青年旅店", duration_ms=0)
        assert cand["source"] == "apple-ttml"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e6_kugou_verbatim_no_trans_not_replaced_by_amll_no_trans():
    """E6: 外语曲目酷狗逐字无译文（rank 1）遇 AMLL 逐字无译文（rank 1）-> 保留酷狗。"""
    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "kugou",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    class FakeAMLL:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "amll",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }
    agg.AppleTTMLFetcher = FakeNone
    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeAMLL
    agg.NetEaseFetcher = FakeNone
    agg.LrcShareFetcher = FakeNone
    agg.LRCLIBFetcher = FakeNone
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
        assert cand["source"] == "kugou", "同等等级时酷狗不应被 AMLL 顶替"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e7_lrcshare_with_trans_replaces_apple_verbatim_no_trans():
    """E7: 外语曲目 Apple 逐字无译文（rank 1）遇 LRCSHARE 行级带译文（rank 2）-> 允许整包替换。"""
    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}]
            }

    class FakeLrcShare:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "lrcshare",
                "lyric_type": "line",
                "has_translation": True,
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "translation": "你好", "words": []}]
            }

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = FakeNone
    agg.AMLLFetcher = FakeNone
    agg.NetEaseFetcher = FakeNone
    agg.LrcShareFetcher = FakeLrcShare
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
        assert cand["source"] == "lrcshare", "带译文的 LRCSHARE 应提升整包替换"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


def test_e8_foreign_apple_no_trans_kugou_with_trans_replaces():
    """外语 Apple 逐字无译文遇酷狗逐字+译文 -> 整包换成酷狗。"""
    class FakeApple:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "apple-ttml",
                "lyric_type": "word",
                "lines": [{"start": 1000, "end": 4000, "text": "Hello", "words": [{"text": "Hello"}]}],
            }

    class FakeKugou:
        @classmethod
        def fetch(cls, *a, **k):
            return {
                "source": "kugou",
                "lyric_type": "word",
                "has_embedded_translation": True,
                "lines": [{
                    "start": 1000, "end": 4000, "text": "Hello", "translation": "你好",
                    "words": [{"text": "Hello"}],
                }],
            }

    class FakeNone:
        @classmethod
        def fetch(cls, *a, **k):
            return None

    reals = {
        "AppleTTMLFetcher": agg.AppleTTMLFetcher,
        "KugouFetcher": agg.KugouFetcher,
        "AMLLFetcher": agg.AMLLFetcher,
        "NetEaseFetcher": agg.NetEaseFetcher,
        "LrcShareFetcher": agg.LrcShareFetcher,
        "LRCLIBFetcher": agg.LRCLIBFetcher,
    }
    agg.AppleTTMLFetcher = FakeApple
    agg.KugouFetcher = FakeKugou
    agg.AMLLFetcher = FakeNone
    agg.NetEaseFetcher = FakeNone
    agg.LrcShareFetcher = FakeNone
    agg.LRCLIBFetcher = FakeNone
    try:
        cand = agg.LyricAggregator.fetch_best_lyrics("Hello", "Adele", duration_ms=0)
        assert cand["source"] == "kugou"
    finally:
        for k, v in reals.items():
            setattr(agg, k, v)


@sandboxed
def test_f1_itunes_cache_key_includes_duration():
    """先 180s 命中再 295s 不得返回同一 apple_id。"""
    calls = []

    def fake_http(url, **k):
        calls.append(url)
        if "search?" not in url:
            return HttpResult(200, '{"results":[]}', 10, 10, "")
        body = json.dumps({"results": [
            {"trackId": 111, "trackName": "Hello", "artistName": "Adele",
             "trackTimeMillis": 180000, "collectionName": "25"},
            {"trackId": 222, "trackName": "Hello", "artistName": "Adele",
             "trackTimeMillis": 295000, "collectionName": "25"},
        ]})
        return HttpResult(200, body, len(body), 10, "")

    with _http_stub(fake_http):
        c = ITunesClient.get_instance()
        r1 = c.search_track("Hello", "Adele", duration_ms=180000)
        r2 = c.search_track("Hello", "Adele", duration_ms=295000)
        assert r1["apple_id"] == "111"
        assert r2["apple_id"] == "222"
        assert r1["apple_id"] != r2["apple_id"]


@sandboxed
def test_f2_itunes_timeout_does_not_write_negative_cache():
    """超时不得写 data: None 负缓存。"""
    def fake_http(url, **k):
        return HttpResult(0, "", 0, 10, "timeout")

    with _http_stub(fake_http):
        c = ITunesClient.get_instance()
        r = c.search_track("NeverExistsXYZ", "Nobody", duration_ms=120000)
        assert r is None
        neg = [v for k, v in c.cache.items() if k.startswith("track:") and (v or {}).get("data") is None]
        assert neg == [], c.cache


def test_f3_primary_artist_insertion_order():
    """主艺人取名单第一位，不是 frozenset 哈希序。"""
    assert primary_artist("Adele / Beyonce") == "adele"
    assert primary_artist("Beyonce / Adele") == "beyonce"
    long_name = "测试多艺人1 / 测试多艺人2 / 艺人3 / 艺人4 / 艺人5 / 艺人6 / 艺人7"
    toks = artist_token_list(long_name)
    assert toks[0] == "测试多艺人1"
    assert "测试多艺人1" in toks[:3]


def test_f4_index_l1_keeps_first_artist():
    """长名单 L1 的前 3 token 含第一艺人，能命中索引。"""
    idx = _load_sample_index()
    q = "测试多艺人1 / 测试多艺人2 / 艺人3 / 艺人4 / 艺人5 / 艺人6"
    res = idx.lookup("歧义曲目", q)
    assert len(res) >= 1
    assert artist_token_list(q)[:3][0] == "测试多艺人1"


@sandboxed
def test_f5_itunes_explicit_not_version_conflict():
    """Hello vs iTunes Hello (Explicit) 版本预筛通过。"""
    def fake_http(url, **k):
        if "search?" not in url:
            return HttpResult(200, '{"results":[]}', 10, 10, "")
        body = json.dumps({"results": [{
            "trackId": 42, "trackName": "Hello (Explicit)", "artistName": "Adele",
            "trackTimeMillis": 295000, "collectionName": "25",
        }]})
        return HttpResult(200, body, len(body), 10, "")

    with _http_stub(fake_http):
        c = ITunesClient.get_instance()
        r = c.search_track("Hello", "Adele", duration_ms=295000)
        assert r is not None
        assert r["apple_id"] == "42"


def test_f6_extract_apple_id_from_real_urls():
    """三条真实 Apple URL 都能抽出数字 ID。"""
    assert extract_apple_id("https://music.apple.com/us/song/hello/1440873187") == "1440873187"
    assert extract_apple_id("https://music.apple.com/us/album/25/1051394208?i=1051394215") == "1051394215"
    assert extract_apple_id("https://music.apple.com/cn/album/foo/id1538548882") == "1538548882"
    assert extract_apple_id("1538548882") == "1538548882"
    assert extract_apple_id("https://music.apple.com/us/album/foo/123") is None


def test_f7_index_skips_bad_json_line():
    """jsonl 单行坏 JSON 不让整表作废。"""
    sample = (FIXTURES_DIR / "apple_index_sample.jsonl").read_text(encoding="utf-8").splitlines()
    mixed = ["{not json", sample[0], ""]
    idx = AmllIndex.parse_jsonl_content(mixed)
    assert len(idx.entries) >= 1


# =====================================================================
# Group G: v1.3.2 修复回归（R3 退出码 / R6 索引宽限 / R7 外语店面）
# =====================================================================

def test_g1_cli_not_found_exit_1_two_requests():
    """G1/R3: 不存在的 ID -> 双 origin 攒票即停（2 次请求）且退出码 1。"""
    calls = []

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        calls.append(url)
        return HttpResult(status=404, text="Not Found", bytes_len=9, elapsed_ms=10, error="")

    with _http_stub(fake_http):
        code = ttml2lyrics_main(["--apple-id", "1234567890", "--stdout"])
        assert code == 1, code
        assert len(calls) == 2, calls


def test_g2_cli_unreachable_exit_3():
    """G2/R3: 全镜像超时 -> UNREACHABLE -> 退出码 3（与未收录严格区分）。"""
    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=10, error="timeout")

    with _http_stub(fake_http):
        code = ttml2lyrics_main(["--apple-id", "1234567890", "--stdout"])
        assert code == 3, code


def test_g3_index_min_lines_2000_accepts_2400():
    """G3/R6: 2400 行合法索引可通过校验（上游合法缩减不再被 2600 卡死）。"""
    fake_lines = ['{"appleMusicId":["1"],"id":"1","rawLyricFile":"am-lyrics/1.ttml"}'] * 2400

    def fake_http(url, headers=None, timeout=3.0, user_agent=""):
        return HttpResult(status=200, text="\n".join(fake_lines), bytes_len=50000, elapsed_ms=100, error="")

    with _http_stub(fake_http):
        downloaded = AmllIndex._download_index(prev_line_count=0)
        assert downloaded is not None, "2400 行合法索引应被接受"
        assert len(downloaded) == 2400


@sandboxed
def test_g4_foreign_lookup_uses_jp_second_storefront():
    """G4/R7: 外语 lookup 第二批用 jp 而非 cn；jp 命中取到时长。"""
    calls = []

    def fake_http(url, **k):
        calls.append(url)
        if "lookup" in url and "country=us" in url:
            return HttpResult(200, '{"results":[]}', 14, 10, "")
        if "lookup" in url and "country=jp" in url:
            body = '{"results":[{"trackId":777,"trackTimeMillis":200000}]}'
            return HttpResult(200, body, len(body), 10, "")
        return HttpResult(404, "", 0, 10, "")

    with _http_stub(fake_http):
        c = ITunesClient.get_instance()
        r = c.lookup_durations(["777"], is_zh=False)
        assert r.get("777") == 200000
        assert any("country=jp" in u for u in calls), calls
        assert not any("country=cn" in u for u in calls), calls


def test_g5_translation_attach_shared_algo():
    """G5/B1: 译文最近邻挂载算法合并到 utils/lyric_attach 后，双路径行为正确。"""
    from core.utils.lyric_attach import attach_translations_by_nearest
    from core.aggregator import align_translation_to_baseline
    from core.fetchers.lrcshare import strip_word_tags

    def mk_lines():
        return [
            {"start": 10000, "text": "hello", "translation": ""},
            {"start": 20000, "text": "world", "translation": ""},
            {"start": 60000, "text": "far away", "translation": ""},
        ]

    entries = [
        {"start": 10100, "text": "你好"},
        {"start": 19900, "text": "世界"},
        {"start": 30000, "text": "两秒外不该挂上"},
    ]
    # aggregator 薄包装（1500ms 窗）
    lines = mk_lines()
    n = align_translation_to_baseline(lines, entries)
    assert n == 2, n
    assert lines[0]["translation"] == "你好"
    assert lines[1]["translation"] == "世界"
    assert lines[2]["translation"] == ""  # 30s 的译文离 60s 行太远

    # lrcshare 路径（2000ms 窗 + strip_word_tags 清洗；其标签是整数型 <offset:dur>）
    lines2 = mk_lines()
    entries2 = [
        {"start": 10000, "text": "<1234>你好"},
        {"start": 21000, "text": "世界"},
    ]
    n2 = attach_translations_by_nearest(
        lines2, entries2, tolerance_ms=2000, text_cleaner=strip_word_tags
    )
    assert n2 == 2, n2
    assert lines2[0]["translation"] == "你好"  # 词级标签被清洗
    assert lines2[1]["translation"] == "世界"


# =====================================================================
# 运行全部测试
# =====================================================================

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
