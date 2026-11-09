# -*- coding: utf-8 -*-
"""v1.8.2 R2 全曲内容语义识别、制作署名清理与安全保护测试。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cleaners.content_classifier import (
    classify_and_clean_all_lines,
    classify_line_role,
    parse_filename_backup,
)
from core.models.canonical_lyrics import IssueSeverity, LineRole
from tests._testkit import run


def test_filename_backup_last_dash():
    """文件名后备严格按最后一个 ' - ' 切分。"""
    t1, a1 = parse_filename_backup("广场 - 李志.flac")
    assert t1 == "广场" and a1 == "李志"

    t2, a2 = parse_filename_backup("Live - Track 01 - A - B - 歌手名.mp3")
    assert t2 == "Live - Track 01 - A - B" and a2 == "歌手名"

    t3, a3 = parse_filename_backup("/path/to/my/song - with - dash - Singer.wav")
    assert t3 == "song - with - dash" and a3 == "Singer"

    t4, a4 = parse_filename_backup("NoDashSong.flac")
    assert t4 == "NoDashSong" and a4 == ""


def test_full_track_outro_credits_and_sample_retention():
    """§3.4 尾部专项验收：结尾制作名单被清理，但其后的真实念白与采样说明绝对保留。"""
    raw_lines = [
        {"start": 190000, "duration": 5000, "text": "真实演唱结尾句", "words": [{"text": "真实演唱结尾句", "start": 190000, "end": 195000}]},
        {"start": 200000, "duration": 2000, "text": "作词：甲"},
        {"start": 202000, "duration": 2000, "text": "作曲：乙"},
        {"start": 204000, "duration": 2000, "text": "编曲：丙"},
        {"start": 206000, "duration": 2000, "text": "混音：丁"},
        {"start": 208000, "duration": 2000, "text": "特别鸣谢：合唱团"},
        {"start": 211000, "duration": 3000, "text": "这是尾奏中的真实念白补句", "words": [{"text": "这是尾奏中的真实念白补句", "start": 211000, "end": 214000}]},
        {"start": 215000, "duration": 4000, "text": "采样来源：1980年代经典广播录音"},
    ]
    data = {"lines": raw_lines, "tags": {"title": "测试曲目", "artist": "测试歌手"}}
    canonical = classify_and_clean_all_lines(data)

    # 1. 词曲提取自尾部
    assert canonical.song.get("extracted_lyricist") == "甲"
    assert canonical.song.get("extracted_composer") == "乙"

    # 2. 制作名单被清理，保留真实演唱、真实念白、采样说明
    texts = [line.text for line in canonical.lines]
    assert "真实演唱结尾句" in texts
    assert "这是尾奏中的真实念白补句" in texts
    assert "采样来源：1980年代经典广播录音" in texts
    assert "编曲：丙" not in texts
    assert "混音：丁" not in texts
    assert "特别鸣谢：合唱团" not in texts
    assert "作词：甲" not in texts
    assert "作曲：乙" not in texts

    # 3. 真实字块时间逐位保持原样
    first_sing = canonical.lines[0]
    assert first_sing.words[0].start_ms == 190000
    assert first_sing.words[0].end_ms == 195000


def test_traditional_chinese_and_original_singer():
    """繁体制作名单识别与原唱保留。"""
    raw_lines = [
        {"start": 1000, "duration": 2000, "text": "作詞：林夕 作曲：陳輝陽"},
        {"start": 3000, "duration": 2000, "text": "編曲：陳輝陽"},
        {"start": 5000, "duration": 2000, "text": "特別鳴謝：英皇娛樂"},
        {"start": 7000, "duration": 3000, "text": "原唱：王菲"},
        {"start": 15000, "duration": 4000, "text": "天空飘着雪花", "words": [{"text": "天空飘着雪花", "start": 15000, "end": 19000}]},
    ]
    data = {"lines": raw_lines}
    canonical = classify_and_clean_all_lines(data)

    assert canonical.song.get("extracted_lyricist") == "林夕"
    assert canonical.song.get("extracted_composer") == "陳輝陽"

    texts = [line.text for line in canonical.lines]
    assert "原唱：王菲" in texts  # 必须保留原唱
    assert "天空飘着雪花" in texts
    assert "編曲：陳輝陽" not in texts
    assert "特別鳴謝：英皇娛樂" not in texts


def test_performer_cues_and_real_singing_thanks():
    """角色提示安全前缀 vs 歌词中真实唱出的'谢谢'。"""
    raw_lines = [
        {"start": 10000, "duration": 3000, "text": "男：走在乡间的小路上", "words": [{"text": "走在乡间的小路上", "start": 10000, "end": 13000}]},
        {"start": 14000, "duration": 3000, "text": "合：谢谢你的陪伴与守护", "words": [{"text": "谢谢你的陪伴与守护", "start": 14000, "end": 17000}]},
    ]
    data = {"lines": raw_lines}
    canonical = classify_and_clean_all_lines(data)

    assert len(canonical.lines) == 2
    l1 = canonical.lines[0]
    assert l1.speaker_prefix == "男"
    assert l1.role == LineRole.SINGING

    l2 = canonical.lines[1]
    assert l2.speaker_prefix == "合"
    assert l2.role == LineRole.SINGING
    # 唱词包含谢谢，但由于没有冒号或制作名单结构，绝不误判为制作鸣谢
    assert "谢谢你的陪伴与守护" in l2.text


def test_lrc_red_attribution_tag_preserved():
    """lrc.red 的 [re:lrc.red (https://lrc.red)] 署名绝对不被当作垃圾元数据删除。"""
    raw_lines = [
        {"start": 0, "duration": 0, "text": "[re:lrc.red (https://lrc.red)]"},
        {"start": 500, "duration": 0, "text": "[by:some_uploader]"},
        {"start": 12000, "duration": 3000, "text": "Hello world", "words": [{"text": "Hello world", "start": 12000, "end": 15000}]},
    ]
    data = {"lines": raw_lines}
    canonical = classify_and_clean_all_lines(data)

    texts = [l.text for l in canonical.lines]
    assert "[re:lrc.red (https://lrc.red)]" in texts
    assert "[by:some_uploader]" not in texts


def test_ambiguous_cue_reported_as_review_required():
    """歧义行标记 REVIEW_REQUIRED 并保留文本。"""
    raw_lines = [
        {"start": 2000, "duration": 2000, "text": "说唱：张三"},
        {"start": 10000, "duration": 3000, "text": "唱词第一句", "words": [{"text": "唱词第一句", "start": 10000, "end": 13000}]},
    ]
    data = {"lines": raw_lines}
    canonical = classify_and_clean_all_lines(data)

    texts = [l.text for l in canonical.lines]
    assert "说唱：张三" in texts
    review_issues = [iss for iss in canonical.issues if iss.severity == IssueSeverity.REVIEW_REQUIRED]
    assert len(review_issues) >= 1
    assert any("AMBIGUOUS" in iss.code for iss in review_issues)


def test_idempotent_cleaning():
    """清洗纯函数幂等性：重复执行不会反复提取或删除内容。"""
    raw_lines = [
        {"start": 1000, "duration": 2000, "text": "作词：张三"},
        {"start": 20000, "duration": 4000, "text": "真实演唱句子", "words": [{"text": "真实演唱句子", "start": 20000, "end": 24000}]},
    ]
    data = {"lines": raw_lines}
    c1 = classify_and_clean_all_lines(data)
    assert len(c1.lines) == 1
    assert c1.song.get("extracted_lyricist") == "张三"

    # 用 c1 的输出结构转成 dict 再跑一次
    data2 = {"lines": [{"text": l.text, "start": l.start_ms, "end": l.end_ms, "words": [w.to_dict() for w in l.words]} for l in c1.lines]}
    c2 = classify_and_clean_all_lines(data2)
    assert len(c2.lines) == 1
    assert c2.lines[0].text == "真实演唱句子"
    assert c2.lines[0].words[0].start_ms == 20000


if __name__ == "__main__":
    sys.exit(run(globals()))
