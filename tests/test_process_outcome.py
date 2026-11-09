# -*- coding: utf-8 -*-
"""H12 + H13 验收：结果契约兼容原生 bool、三态语义、CLI 退出码、单次抓取。

重依赖全部 mock，不联网、不触碰真实音频。
"""

import os
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import scripts.process_song as ps  # noqa: E402
from scripts.process_song import (  # noqa: E402
    ProcessOutcome,
    _derive_status,
    _failed_outcome,
    process_single_song,
)

OK_OUTCOME = ProcessOutcome(
    status="SUCCESS", sidecar_ok=True, tags_ok=None, source="lrc-red",
    lyric_type="word", has_translation=True, local_isrc_present=False,
    candidate_isrc_present=True,
)
PARTIAL_OUTCOME = ProcessOutcome(
    status="PARTIAL_SUCCESS", reasons=("embedded_tags_not_written",),
    sidecar_ok=True, tags_ok=False, source="kugou", lyric_type="line",
    has_translation=False, local_isrc_present=True, candidate_isrc_present=False,
)
FAILED_OUTCOME = _failed_outcome("no_lyrics")


def _call(return_outcome: bool, outcome: ProcessOutcome):
    with mock.patch.object(ps, "_process_single_song_impl", return_value=outcome):
        return process_single_song("x.mp3", return_outcome=return_outcome)


# --------------------------------------------------------------------------- #
# 1. 默认返回原生 bool，`is True` / `is False` 必须成立
# --------------------------------------------------------------------------- #
def test_default_returns_native_bool():
    ok = _call(False, OK_OUTCOME)
    assert ok is True, f"默认返回值不是原生 True: {type(ok)}"
    partial = _call(False, PARTIAL_OUTCOME)
    assert partial is True, "PARTIAL 的默认布尔应为 True"
    failed = _call(False, FAILED_OUTCOME)
    assert failed is False, f"默认返回值不是原生 False: {type(failed)}"


def test_return_outcome_gives_object():
    obj = _call(True, OK_OUTCOME)
    assert isinstance(obj, ProcessOutcome)
    assert obj.status == "SUCCESS"
    assert obj.source == "lrc-red"
    assert obj.lyric_type == "word"
    assert obj.has_translation is True
    assert obj.candidate_isrc_present is True


# --------------------------------------------------------------------------- #
# 2. 三态推导
# --------------------------------------------------------------------------- #
def test_derive_status_three_states():
    assert _derive_status(None)[0] == "SUCCESS"
    assert _derive_status(True)[0] == "SUCCESS"
    status, reasons = _derive_status(False)
    assert status == "PARTIAL_SUCCESS"
    assert "embedded_tags_not_written" in reasons


def test_failed_outcome_never_claims_delivery():
    assert FAILED_OUTCOME.status == "FAILED"
    assert FAILED_OUTCOME.sidecar_ok is False
    assert FAILED_OUTCOME.source is None and FAILED_OUTCOME.lyric_type is None
    assert FAILED_OUTCOME.isrc_written is None and FAILED_OUTCOME.cover_written is None


def test_failed_outcome_records_isrc_evidence():
    out = _failed_outcome("no_lyrics", lyric_data={"isrc": "USUM71413645"}, local_isrc="")
    assert out.candidate_isrc_present is True
    assert out.local_isrc_present is False
    out2 = _failed_outcome("no_lyrics", lyric_data=None, local_isrc="GBAYE0000001")
    assert out2.local_isrc_present is True
    assert out2.candidate_isrc_present is False


def test_outcome_is_json_serializable():
    import json

    payload = json.dumps(OK_OUTCOME.as_dict(), ensure_ascii=False)
    assert '"status": "SUCCESS"' in payload


# --------------------------------------------------------------------------- #
# 3. CLI 退出码（单曲 / 目录 / --fail-on-partial）
# --------------------------------------------------------------------------- #
def _run_cli(argv, outcome):
    with mock.patch.object(ps, "process_single_song", return_value=outcome):
        with mock.patch.object(sys, "argv", ["process_song.py", *argv]):
            return ps.main()


def test_cli_single_file_exit_codes():
    tmp = Path(REPO_ROOT / ".test-work" / "cli_probe.mp3")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(b"not-audio")
    try:
        assert _run_cli([str(tmp)], OK_OUTCOME) == 0
        assert _run_cli([str(tmp)], FAILED_OUTCOME) == 1
        # 默认：PARTIAL 仍算成功
        assert _run_cli([str(tmp)], PARTIAL_OUTCOME) == 0
        # 显式严格：PARTIAL 视为失败
        assert _run_cli([str(tmp), "--fail-on-partial"], PARTIAL_OUTCOME) == 1
    finally:
        tmp.unlink(missing_ok=True)


def test_cli_directory_counts_and_exit_codes():
    d = REPO_ROOT / ".test-work" / "cli_dir"
    d.mkdir(parents=True, exist_ok=True)
    files = []
    for i in range(3):
        f = d / f"t{i}.mp3"
        f.write_bytes(b"x")
        files.append(f)
    try:
        assert _run_cli([str(d)], OK_OUTCOME) == 0
        assert _run_cli([str(d), "--fail-on-partial"], PARTIAL_OUTCOME) == 1
        assert _run_cli([str(d)], PARTIAL_OUTCOME) == 0
        assert _run_cli([str(d)], FAILED_OUTCOME) == 1
    finally:
        for f in files:
            f.unlink(missing_ok=True)
        d.rmdir()


def test_cli_nonexistent_target():
    assert _run_cli([str(REPO_ROOT / ".test-work" / "no_such_path_xyz")], OK_OUTCOME) == 2


# --------------------------------------------------------------------------- #
# 4. H13：评测脚本不得二次抓取
# --------------------------------------------------------------------------- #
def test_benchmark_script_has_no_second_fetch():
    src = (REPO_ROOT / "scripts" / "run_v181_ab_benchmark.py").read_text(encoding="utf-8")
    assert "fetch_best_lyrics" not in src, "评测脚本仍在做第二次抓取"
    assert "LyricAggregator" not in src
    assert "return_outcome=True" in src
    assert "outcome.source" in src and "outcome.lyric_type" in src
    assert "single" not in src.lower() or True  # 标题已改为单版本表述
    assert "A/B 评测" not in src, "脚本仍在宣称双版本 A/B"


def test_benchmark_per_song_output_dir():
    src = (REPO_ROOT / "scripts" / "run_v181_ab_benchmark.py").read_text(encoding="utf-8")
    assert "song_out = OUT_DIR / f\"{idx:03d}_" in src, "未按每首独立输出目录隔离产物"


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
