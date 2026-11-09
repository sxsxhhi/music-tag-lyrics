# -*- coding: utf-8 -*-
"""开发侧评测脚本契约检查（H13）。

本套件只针对 `scripts/run_v181_ab_benchmark.py`，而该脚本属于**不进分发包**的
开发工具，因此本文件同样不随包分发（见 `scripts/_dist_view.py` 的排除清单）。
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BENCH = os.path.join(REPO_ROOT, "scripts", "run_v181_ab_benchmark.py")


def _src() -> str:
    with open(BENCH, encoding="utf-8") as f:
        return f.read()


def test_benchmark_script_has_no_second_fetch():
    """每首只能执行一次真实处理，不得为了拿来源再抓一次。"""
    src = _src()
    assert "fetch_best_lyrics" not in src, "评测脚本仍在做第二次抓取"
    assert "LyricAggregator" not in src
    assert "return_outcome=True" in src
    assert "outcome.source" in src and "outcome.lyric_type" in src
    assert "A/B 评测" not in src, "脚本仍在宣称双版本 A/B"


def test_benchmark_per_song_output_dir():
    """每首独立输出目录，避免同名不同路径相撞与旧副文件造成假阳性。"""
    src = _src()
    assert 'song_out = OUT_DIR / f"{idx:03d}_' in src, "未按每首独立输出目录隔离产物"


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
