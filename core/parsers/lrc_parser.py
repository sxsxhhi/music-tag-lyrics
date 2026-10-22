# -*- coding: utf-8 -*-
"""
LRC / Enhanced LRC Lyric Parser (v1.1.3)
Parses standard line-by-line LRC text, international Enhanced LRC (ELRC / A2),
and Salt Player .spl verbatim word timestamps into structured lyrics data.
Supports [offset:xxx] tags, multi-timestamp line retention, and boundary offset clamping.
Zero external dependencies.
"""

import re
from typing import Dict, List, Any, Tuple

from ..utils.time_utils import parse_match_to_ms, apply_time_offset

LRC_TAG_RE = re.compile(r"^\[([a-zA-Z]+):([^\]]*)\]")
LRC_TIME_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")
WORD_TAG_RE = re.compile(r"<(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?>|\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")

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
    internal_offset = 0
    if "offset" in tags:
        try:
            internal_offset = int(tags["offset"])
        except ValueError:
            internal_offset = 0
    total_offset = internal_offset + adjust

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
        "lines": lines
    }
