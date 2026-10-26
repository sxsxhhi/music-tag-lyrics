# -*- coding: utf-8 -*-
"""语言路由检测器 (Language Route Detector) 单元测试。"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.utils.lang_detect import (
    is_cantopop_artist,
    match_imagery_ngrams,
    detect_language,
)


def test_cantopop_artist_whitelist():
    """测试歌手白名单匹配。"""
    # 典型粤语歌手
    assert is_cantopop_artist("Beyond")
    assert is_cantopop_artist("黄凯芹")
    assert is_cantopop_artist("陳奕迅")
    assert is_cantopop_artist("张国荣")
    assert is_cantopop_artist("李克勤")
    assert is_cantopop_artist("Twins")

    # 典型非纯粤语/普通话歌手
    assert not is_cantopop_artist("周杰伦")
    assert not is_cantopop_artist("薛之谦")
    assert not is_cantopop_artist("金海心")
    assert not is_cantopop_artist("毛不易")


def test_slang_and_imagery_matching():
    """测试口语字与书面意象词判定。"""
    slang_text = "你喺边度啊？我冇嘢讲，快啲嚟啦！"
    res_slang = detect_language(lyrics_text=slang_text, interactive=False)
    assert res_slang["lang"] == "yue", f"Expected 'yue', got: {res_slang}"

    # 书面语粤语歌（海阔天空类，口语字命中率为 0）
    haikuo_text = "今天我 寒夜里看雪飘过 怀着冷却了的心窝漂远方 风雨里追赶 雾里分不清影踪"
    res_haikuo = detect_language(
        lyrics_text=haikuo_text,
        title="海阔天空",
        artist="Beyond",
        interactive=False,
    )
    assert res_haikuo["lang"] == "yue", f"Expected 'yue', got: {res_haikuo}"
    assert res_haikuo["confidence"] >= 0.70


def test_mandarin_plain_lyrics():
    """测试典型普通话歌曲被正确路由至 zh。"""
    mandarin_text = "把你的心我的心串一串 串一株幸运草 串一个同心圆"
    res_mandarin = detect_language(
        lyrics_text=mandarin_text,
        title="爱",
        artist="小虎队",
        interactive=False,
    )
    assert res_mandarin["lang"] == "zh", f"Expected 'zh', got: {res_mandarin}"


def test_low_confidence_fallback():
    """测试模糊文本在非交互模式下安全回退至普通话 (zh)。"""
    ambiguous_text = "今天天气很好 我们一起走走"
    res_ambiguous = detect_language(
        lyrics_text=ambiguous_text,
        title="散步",
        artist="佚名",
        interactive=False,
    )
    assert res_ambiguous["lang"] == "zh", f"Expected 'zh', got: {res_ambiguous}"


def test_audio_probe_triggers_without_metadata():
    """测试元数据完全缺失时，音频探针仍能触发并作为终裁。"""
    import unittest.mock as mock
    with mock.patch("core.utils.lang_detect.probe_audio_cantonese_confidence", return_value=0.55) as mock_probe:
        with mock.patch("os.path.isfile", return_value=True):
            res = detect_language(
                lyrics_text="",
                title="未知曲目",
                artist="未知歌手",
                audio_path="/fake/path/song.flac",
                interactive=False,
            )
            mock_probe.assert_called_once()
            assert res["lang"] == "yue", f"Expected 'yue', got: {res}"
            assert res["confidence"] >= 0.50


def test_embedded_lyrics_detection():
    """测试包含方言词的歌词能正确被识别为 yue。"""
    slang_lyrics = "寻日睇见佢，真系好搞笑！"
    res = detect_language(lyrics_text=slang_lyrics, title="小曲", artist="佚名", interactive=False)
    assert res["lang"] == "yue"
    assert res["confidence"] >= 0.50


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

