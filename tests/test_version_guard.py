# -*- coding: utf-8 -*-
"""H04 版本关键词 ASCII 边界匹配验收（修复前为子串判定）。

负例全部取自真实歌曲名，修复前会被误判成现场版/翻唱版/小样。
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

from core.utils.version_guard import (  # noqa: E402
    is_cover_version,
    is_demo_version,
    is_instrumental_version,
    is_live_version,
    is_remix_version,
    matches_version_policy,
    strip_subtitle_brackets,
)

# 修复前误判 → 修复后必须为 False
LIVE_FALSE = ["Alive", "Olive", "Deliver", "Lived In Bars", "Delivering", "Outlived"]
COVER_FALSE = ["Discover", "Recovery", "Recovering", "Undercover", "Covered", "Covered By"]
DEMO_FALSE = ["Demons", "Demolition", "Democracy"]

# 必须保持为 True 的正例（含中文相邻与括号/连字符变体）
LIVE_TRUE = [
    "Song (Live)", "Song [Live]", "Live现场", "2024 Live 现场版", "Live（现场）",
    "現場 Live", "Live版", "Live's", "Live-Edit", "Live@Home", "Live123",
    "live_edit", "Acoustic Version", "Unplugged 不插电",
]
COVER_TRUE = ["Song (Cover)", "翻唱版", "Cover版", "cover2"]
DEMO_TRUE = ["Song - Demo", "Demo Version", "小样"]
INSTR_TRUE = ["Song (Instrumental)", "伴奏", "纯音乐", "Karaoke Version", "off vocal", "off  vocal"]


def test_live_false_positives_removed():
    for name in LIVE_FALSE:
        assert is_live_version(name) is False, f"{name} 仍被误判为 Live"


def test_cover_false_positives_removed():
    for name in COVER_FALSE:
        assert is_cover_version(name) is False, f"{name} 仍被误判为 Cover"


def test_demo_false_positives_removed():
    for name in DEMO_FALSE:
        assert is_demo_version(name) is False, f"{name} 仍被误判为 Demo"


def test_live_true_positives_kept():
    for name in LIVE_TRUE:
        assert is_live_version(name) is True, f"正例被误伤: {name}"


def test_cover_true_positives_kept():
    for name in COVER_TRUE:
        assert is_cover_version(name) is True, f"正例被误伤: {name}"


def test_demo_true_positives_kept():
    for name in DEMO_TRUE:
        assert is_demo_version(name) is True, f"正例被误伤: {name}"


def test_instrumental_boundaries():
    for name in INSTR_TRUE:
        assert is_instrumental_version(name) is True, f"正例被误伤: {name}"
    for name in ("Instrument", "Instrumentality Sample"):
        assert is_instrumental_version(name) is False, f"{name} 被误判为伴奏"


def test_remix_chinese_adjacency():
    """Remix 紧邻中文时仍需命中（原 `\\b` 因汉字属 \\w 而漏判）。"""
    assert is_remix_version("Song Remix版") is True
    assert is_remix_version("Song 混音版") is True
    assert is_remix_version("Song (Club Mix)") is True
    assert is_remix_version("Premixed") is False


def test_version_policy_uses_fixed_predicates():
    """互斥策略必须继承修复后的判定：Alive 不再被当作 Live 而排除。"""
    assert matches_version_policy(False, "Alive", "") is True
    assert matches_version_policy(False, "Song (Live)", "") is False
    assert matches_version_policy(True, "Live现场", "") is True
    assert matches_version_policy(False, "", "Discover") is True


def test_subtitle_brackets_protection_unchanged():
    """括号清洗仍保护真实版本标记，且不再因误判而锁死普通副标题。"""
    assert strip_subtitle_brackets("一腔诗意喂了狗 (Live)") == "一腔诗意喂了狗 (Live)"
    assert strip_subtitle_brackets("不擅生长的树 (Isness)") == "不擅生长的树"
    assert strip_subtitle_brackets("Alive (Remastered)") == "Alive"


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())
