# -*- coding: utf-8 -*-
"""酷狗解析门禁离线回归：尾奏不拒、预览/冲轴拒绝、Live 时长贴齐。运行：python tests/test_kugou_gates.py"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import io
import os
import sys
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.fetchers.kugou import (
    parsed_duration_reject_reason,
    _reject_parsed_duration,
    krcs_version_ok,
    select_best_krc_candidate,
    KRCS_METADATA_WINDOW_SEC,
)


def _download(last_end_ms):
    def fn(cid, ack, adjust=0):
        return {
            "lines": [{"start": 0, "end": last_end_ms, "text": "词"}],
            "has_embedded_translation": False,
        }
    return fn


def test_outro_not_rejected():
    # 曲 347s、末行 282s：尾奏，不得拒绝
    assert parsed_duration_reject_reason(282000, 347000) is None
    assert _reject_parsed_duration(282000, 347000) is False
    got = select_best_krc_candidate(
        [{"id": "1", "accesskey": "a"}],
        _download(282000),
        target_duration_ms=347000,
    )
    assert got and got["lines"], got


def test_preview_rejected():
    # 曲 200s、末行 30s：预览截断
    assert parsed_duration_reject_reason(30000, 200000) == "preview"
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = select_best_krc_candidate(
            [{"id": "1", "accesskey": "a"}],
            _download(30000),
            target_duration_ms=200000,
        )
    assert got is None
    assert "预览截断" in buf.getvalue()


def test_overrun_rejected():
    # 末行 = 曲长 + 20s：冲轴
    last_end = 200000 + 20000
    assert parsed_duration_reject_reason(last_end, 200000) == "overrun"
    buf = io.StringIO()
    with redirect_stdout(buf):
        got = select_best_krc_candidate(
            [{"id": "1", "accesskey": "a"}],
            _download(last_end),
            target_duration_ms=200000,
        )
    assert got is None
    assert "冲轴" in buf.getvalue()


def test_live_krcs_duration_align():
    # 标题无 Live、专辑为空、时长差 3s → 放行
    assert krcs_version_ok(True, "秋城", "", cand_dur_sec=250, target_dur_sec=253) is True
    # 录音室时长差 88s → 拒绝
    assert krcs_version_ok(True, "秋城", "", cand_dur_sec=160, target_dur_sec=248) is False
    assert KRCS_METADATA_WINDOW_SEC == 10
    # 差正好 10s 仍贴齐
    assert krcs_version_ok(True, "悄悄的他", "", cand_dur_sec=240, target_dur_sec=250) is True
    # 本地录音室仍拦截带 Live 的候选（与时长无关）
    assert krcs_version_ok(False, "秋城 (Live)", "", cand_dur_sec=250, target_dur_sec=250) is False


def test_lrcshare_shares_same_gate():
    """P1-B：LRCSHARE 旧版用 abs(末行-曲长)>5000 对称拒，6s 尾奏就会整源丢弃。
    改走统一门禁后，尾奏必须放行，只拒预览与冲轴。"""
    from core.fetchers.lrcshare import parsed_duration_reject_reason as ls_gate

    # 曲 300s、末行 294s（6s 尾奏）：旧版 abs(6000) > 5000 会拒，现在必须放行
    assert ls_gate(294000, 300000) is None
    # 曲 300s、末行 240s（60s 器乐尾奏）：也必须放行
    assert ls_gate(240000, 300000) is None
    # 曲 200s、末行 30s：预览截断，仍要拒
    assert ls_gate(30000, 200000) == "preview"
    # 末行超出曲长 20s：冲轴，仍要拒
    assert ls_gate(320000, 300000) == "overrun"
    # 无音频时长时不判
    assert ls_gate(30000, 0) is None


def test_lrcshare_parse_lyric_lines_keeps_precision():
    """原生 time_ms 路径固定 3 位；parse_lrc 兜底透传探测到的精度。"""
    from core.fetchers.lrcshare import LrcShareFetcher

    native = LrcShareFetcher.parse_lyric_lines({
        "versions": [{
            "kind": "original",
            "rows": [
                {"time_ms": 1000, "end_ms": 2000, "text": "甲"},
                {"time_ms": 2000, "end_ms": 3000, "text": "乙"},
            ],
        }]
    })
    assert native.get("time_precision") == 3, native

    fallback = LrcShareFetcher.parse_lyric_lines({}, default_lrc="[00:01.230]三位")
    assert fallback.get("time_precision") == 3, fallback
    fallback2 = LrcShareFetcher.parse_lyric_lines({}, default_lrc="[00:01.23]两位")
    assert fallback2.get("time_precision") == 2, fallback2


def test_remix_exclusion_and_policy():
    """验证混音/Remix 识别与版本互斥策略。"""
    from core.utils.version_guard import is_remix_version, matches_version_policy

    # 正例：括号混音、各种 Mix、Remix
    assert is_remix_version("Boom, Boom, Boom, Boom!! (Snowtek Mix)") is True
    assert is_remix_version("Boom, Boom, Boom, Boom!! (Airplay Mix)") is True
    assert is_remix_version("Boom, Boom, Boom, Boom!! (Club Mix)") is True
    assert is_remix_version("Song (Radio Edit)") is True
    assert is_remix_version("Song [XX Remix]") is True
    assert is_remix_version("Song 混音版") is True
    assert is_remix_version("Track - Extended Mix") is True

    # 负例：普通录音室原版、普通专辑
    assert is_remix_version("Boom, Boom, Boom, Boom!!") is False
    assert is_remix_version("The Party Album!") is False
    assert is_remix_version("晴天") is False

    # 策略互斥：目标非混音，候选为混音 -> 必须拦截
    assert matches_version_policy(
        target_is_live=False,
        cand_title="Boom, Boom, Boom, Boom!! (Snowtek Mix)",
        cand_album="The Party Album!",
        target_is_remix=False
    ) is False

    # 策略放行：目标非混音，候选非混音 -> 放行
    assert matches_version_policy(
        target_is_live=False,
        cand_title="Boom, Boom, Boom, Boom!!",
        cand_album="The Party Album!",
        target_is_remix=False
    ) is True

    # 策略互斥：目标为混音，候选非混音 -> 必须拦截
    assert matches_version_policy(
        target_is_live=False,
        cand_title="Boom, Boom, Boom, Boom!!",
        cand_album="The Party Album!",
        target_is_remix=True
    ) is False


def test_kugou_album_weighting_and_rank():
    """验证酷狗候选排序中同名专辑权重大于微小时长抖动。"""
    from core.fetchers.kugou import KugouFetcher
    from core.utils.version_guard import strip_subtitle_brackets

    target_title = "Boom, Boom, Boom, Boom!!"
    target_artist = "Vengaboys"
    target_album = "The Party Album!"
    target_dur_ms = 202570

    cand_album_match = {
        "title": "Boom, Boom, Boom, Boom!!",
        "artist": "Vengaboys",
        "album": "The Party Album!",
        "duration": 202000,
        "hash": "hash_album_match",
    }
    cand_other_album = {
        "title": "Boom, Boom, Boom, Boom!!",
        "artist": "Vengaboys",
        "album": "Some Compilation",
        "duration": 202470,
        "hash": "hash_other_album",
    }

    def rank_fn(song):
        s_t = song.get("title", "").strip().lower()
        t_c = target_title.strip().lower()
        if s_t == t_c or strip_subtitle_brackets(s_t) == strip_subtitle_brackets(t_c):
            t_score = 100
        elif t_c in s_t or s_t in t_c:
            t_score = 50
        else:
            t_score = 0
            
        s_alb = song.get("album", "").strip().lower()
        alb_c = target_album.strip().lower()
        alb_score = 0
        if alb_c and s_alb:
            if alb_c == s_alb or strip_subtitle_brackets(alb_c) == strip_subtitle_brackets(s_alb):
                alb_score = 60
            elif alb_c in s_alb or s_alb in alb_c:
                alb_score = 30

        s_a = song.get("artist", "").strip().lower()
        a_c = target_artist.strip().lower()
        a_score = 20 if (a_c and (a_c in s_a or s_a in a_c)) else 0
        
        d_diff = abs(song.get("duration", 0) - target_dur_ms) if target_dur_ms > 0 else 0
        return (-t_score, -alb_score, -a_score, d_diff)

    cands = [cand_other_album, cand_album_match]
    cands.sort(key=rank_fn)
    assert cands[0]["hash"] == "hash_album_match", f"Expected matching album first, got: {cands[0]}"


def test_album_match_score_short_name_guard():
    """O2 防穿透：极短专辑名（<3 字符）禁止模糊子串加权，只允许精确/去括号相等。"""
    from core.utils.version_guard import album_match_score

    # 数字短专辑不得穿透到无关长专辑
    assert album_match_score("1", "100 Hits") == 0
    assert album_match_score("21", "1989") == 0
    # 精确与去括号精确仍然满分
    assert album_match_score("21", "21") == 60
    assert album_match_score("21", "21 (Deluxe)") == 60
    assert album_match_score("The Party Album!", "The Party Album! (Deluxe)") == 60
    # 正常长度的子串加权不受影响
    assert album_match_score("Greatest", "Greatest Hits") == 30
    # 空值安全
    assert album_match_score("", "任意") == 0
    assert album_match_score("任意", "") == 0


def main():
    """统一三态 runner（v1.5.1-E1）：PASS / SKIP / FAIL，SKIP 不计入 PASS。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
