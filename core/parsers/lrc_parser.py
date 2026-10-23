# -*- coding: utf-8 -*-
"""LRC / Enhanced LRC 解析：多时间戳行保留、offset 箝位。"""

import re
from typing import Dict, List, Any, Tuple

from ..utils.time_utils import parse_match_to_ms, apply_time_offset
from ._common import parse_offset_tag

LRC_TAG_RE = re.compile(r"^\[([a-zA-Z]+):([^\]]*)\]")
# 分钟位 1-3 位（铁律 10.2「0 丢行」：旧版只认 1-2 位，[123:45.67] 这种 >99 分钟的行会被静默丢弃）
LRC_TIME_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?\]")
WORD_TAG_RE = re.compile(r"<(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?>|\[(\d{1,3}):(\d{1,2})(?:\.(\d{1,3}))?\]")

# 任意时间戳（行级或词级），用于探测源的小数位粒度
_ANY_TIME_RE = re.compile(r"[\[<]\d{1,3}:\d{1,2}(?:\.(\d{1,3}))?[\]>]")


def detect_time_precision(text: str) -> int:
    """探测源里出现过的最细粒度：有 3 位毫秒就返回 3，否则返回 2。

    v1.2.8 精度策略：源是 3 位毫秒就出 3 位，源只有 1-2 位才退回 2 位。
    毫秒整数本身分不出源精度（[00:01.23] 与 [00:01.230] 解析后都是 1230），
    所以必须在解析这一步把粒度记下来，一路传到输出层。
    """
    for m in _ANY_TIME_RE.finditer(text or ""):
        if len(m.group(1) or "") >= 3:
            return 3
    return 2

def parse_enhanced_words(line_text: str, default_start_ms: int, offset_ms: int = 0) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Parses Enhanced LRC or SPL verbatim tags into clean text and structured words list.
    Supports:
      - Standard Enhanced LRC: <01:23.45>头<01:24.00>顶...<01:26.00>
      - SPL private dialect: 头<01:24.00>顶...[01:26.00]
    Returns: (clean_text, words)
    """
    matches = list(WORD_TAG_RE.finditer(line_text))
    if not matches:
        return line_text.strip(), []

    words = []
    # If there is untagged text before the first timestamp tag (SPL dialect)
    first_text = line_text[:matches[0].start()].strip()
    if first_text:
        w_start = apply_time_offset(default_start_ms, offset_ms)
        w_end = apply_time_offset(parse_match_to_ms(matches[0]), offset_ms)
        words.append({
            "start": w_start,
            "end": w_end,
            "text": first_text
        })

    for i in range(len(matches)):
        cur_t = apply_time_offset(parse_match_to_ms(matches[i]), offset_ms)
        next_pos = matches[i + 1].start() if i + 1 < len(matches) else len(line_text)
        w_text = line_text[matches[i].end():next_pos]
        if i + 1 < len(matches):
            next_t = apply_time_offset(parse_match_to_ms(matches[i + 1]), offset_ms)
            if w_text:
                words.append({
                    "start": cur_t,
                    "end": next_t,
                    "text": w_text
                })
        elif w_text.strip():
            words.append({
                "start": cur_t,
                "end": cur_t + 500,
                "text": w_text
            })

    for w in words:
        w["duration"] = max(0, w["end"] - w["start"])

    clean_text = "".join(w["text"] for w in words).strip()
    return clean_text, words

def parse_lrc(lrc_text: str, adjust: int = 0) -> Dict[str, Any]:
    """
    Parses standard LRC or Enhanced LRC / SPL text into unified dictionary.
    Supports [offset:xxx] metadata and external adjust parameters.
    Properly preserves multiple timestamp tags on repeating lines without loss.
    """
    tags = {}
    raw_lines = []
    
    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Match metadata tag [ar:...], [ti:...], [offset:...]
        tag_match = LRC_TAG_RE.match(line)
        if tag_match and not LRC_TIME_RE.match(line):
            tags[tag_match.group(1).lower()] = tag_match.group(2).strip()
            continue

        # Match line time tags [01:23.45]
        time_matches = list(LRC_TIME_RE.finditer(line))
        if not time_matches:
            continue

        # Extract text following all line-level time tags
        last_match = time_matches[-1]
        text = line[last_match.end():].strip()

        # Preserve every timestamp tag (handles repeating chorus lines like [01:10.00][02:20.00]Chorus)
        for tm in time_matches:
            raw_ms = parse_match_to_ms(tm)
            raw_lines.append((raw_ms, text))

    # Calculate overall offset (internal [offset:xxx] + external adjust)
    total_offset = parse_offset_tag(tags) + adjust

    # Apply offset and sort by timestamp
    adjusted_entries = [
        (apply_time_offset(raw_ms, total_offset), text)
        for raw_ms, text in raw_lines
    ]
    adjusted_entries.sort(key=lambda x: x[0])

    lines = []
    for i in range(len(adjusted_entries)):
        start_ms, raw_text = adjusted_entries[i]
        # P1-4: 这里必须传**未叠加 offset** 的原始起点。start_ms 已经 apply 过一次，
        # 再传进 parse_enhanced_words 会让首个伪词的起点被加两次、终点只加一次，
        # 结果 end < start、duration 被压成 0。
        clean_text, words = parse_enhanced_words(raw_text, start_ms - total_offset, total_offset)

        if words:
            end_ms = words[-1]["end"]
            duration = max(200, end_ms - start_ms)
        elif i + 1 < len(adjusted_entries):
            next_start = adjusted_entries[i + 1][0]
            duration = max(500, min(8000, next_start - start_ms))
            end_ms = start_ms + duration
        else:
            duration = 3500
            end_ms = start_ms + duration

        lines.append({
            "start": start_ms,
            "duration": duration,
            "end": end_ms,
            "text": clean_text,
            "words": words
        })

    return {
        "tags": tags,
        "lines": lines,
        # v1.2.8：把源的时间戳粒度带出去，输出层据此决定出 2 位还是 3 位
        "time_precision": detect_time_precision(lrc_text),
    }
