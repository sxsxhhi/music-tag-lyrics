# -*- coding: utf-8 -*-
"""酷狗解析门禁离线回归：尾奏不拒、预览/冲轴拒绝、Live 时长贴齐。运行：python tests/test_kugou_gates.py"""

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


def main():
    cases = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in cases:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}  -> {e}")
    print(f"\n{len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
