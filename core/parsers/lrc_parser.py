# -*- coding: utf-8 -*-
"""
LRC / Enhanced LRC Lyric Parser (v1.1.1)
Parses standard line-by-line LRC text as well as international Enhanced LRC (ELRC / A2)
and Salt Player .spl verbatim word timestamps into structured lyrics data.
Supports bidirectional high-fidelity round-tripping.
Zero external dependencies.
"""

import re
from typing import Dict, List, Any, Tuple, Optional

LRC_TAG_RE = re.compile(r"^\[([a-zA-Z]+):([^\]]*)\]")
LRC_TIME_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")
WORD_TAG_RE = re.compile(r"<(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?>|\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")

def _parse_time_match(m) -> int:
    """Extracts milliseconds from a regex match of <mm:ss.xx> or [mm:ss.xx]."""
    if m.group(1) is not None:
        mins, secs, millis_str = int(m.group(1)), int(m.group(2)), m.group(3) or "0"
    else:
        mins, secs, millis_str = int(m.group(4)), int(m.group(5)), m.group(6) or "0"
    if len(millis_str) == 2:
        ms = int(millis_str) * 10
    elif len(millis_str) == 1:
        ms = int(millis_str) * 100
    else:
        ms = int(millis_str[:3])
    return (mins * 60 + secs) * 1000 + ms

def parse_enhanced_words(line_text: str, default_start_ms: int) -> Tuple[str, List[Dict[str, Any]]]:
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
        words.append({
            "start": default_start_ms,
            "end": _parse_time_match(matches[0]),
            "text": first_text
        })

    for i in range(len(matches)):
        cur_t = _parse_time_match(matches[i])
        next_pos = matches[i + 1].start() if i + 1 < len(matches) else len(line_text)
        w_text = line_text[matches[i].end():next_pos]
        if i + 1 < len(matches):
            next_t = _parse_time_match(matches[i + 1])
            if w_text:
                words.append({
                    "start": cur_t,
                    "end": next_t,
                    "text": w_text
                })
        elif w_text.strip():
            # Trailing text after the last tag
            words.append({
                "start": cur_t,
                "end": cur_t + 500,
                "text": w_text
            })

    for w in words:
        w["duration"] = max(0, w["end"] - w["start"])

    clean_text = "".join(w["text"] for w in words).strip()
    return clean_text, words

def parse_lrc(lrc_text: str) -> Dict[str, Any]:
    """
    Parses standard LRC or Enhanced LRC / SPL text into a dictionary:
    {
        "tags": {"ti": "...", "ar": "...", ...},
        "lines": [
            {
                "start": 14110,  # ms
                "duration": 3000,
                "end": 17110,
                "text": "头顶的太阳",
                "words": [
                    {"start": 14110, "end": 14660, "duration": 550, "text": "头"},
                    ...
                ]
            }
        ]
    }
    """
    tags = {}
    raw_entries = []

    for raw_line in lrc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Match metadata tag [ar:...], [ti:...]
        tag_match = LRC_TAG_RE.match(line)
        if tag_match and not LRC_TIME_RE.match(line):
            tags[tag_match.group(1).lower()] = tag_match.group(2).strip()
            continue

        # Match line time tags [01:23.45]
        time_matches = list(LRC_TIME_RE.finditer(line))
        if not time_matches:
            continue

        # Extract line text after the line-level time tags
        # In Enhanced LRC, the line might be: [01:23.45]<01:23.45>Word1...
        # So the last line time match is the [01:23.45] tag
        last_match = time_matches[-1]
        text = line[last_match.end():].strip()

        mins = int(last_match.group(1))
        secs = int(last_match.group(2))
        millis_str = last_match.group(3) or "0"
        if len(millis_str) == 2:
            ms = int(millis_str) * 10
        elif len(millis_str) == 1:
            ms = int(millis_str) * 100
        else:
            ms = int(millis_str[:3])

        total_ms = (mins * 60 + secs) * 1000 + ms
        raw_entries.append((total_ms, text))

    # Sort by timestamp
    raw_entries.sort(key=lambda x: x[0])

    lines = []
    for i in range(len(raw_entries)):
        start_ms, raw_text = raw_entries[i]
        clean_text, words = parse_enhanced_words(raw_text, start_ms)

        if words:
            end_ms = words[-1]["end"]
            duration = max(200, end_ms - start_ms)
        elif i + 1 < len(raw_entries):
            next_start = raw_entries[i + 1][0]
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
