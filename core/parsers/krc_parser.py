# -*- coding: utf-8 -*-
"""
KRC Lyric Parser (v1.1.3)
Parses raw KRC text into structured line-by-line and word-by-word timestamps.
Supports [offset:xxx] tags from lyrics metadata and server-side adjust offsets.
Zero external dependencies.
"""

import re
from typing import Dict, List, Any, Optional

from ..utils.time_utils import apply_time_offset

TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
KRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
KRC_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")

def parse_krc(krc_text: str, adjust: int = 0) -> Dict[str, Any]:
    """
    Parses decrypted KRC text into structured format.
    Applies metadata [offset:xxx] and caller-specified adjust offsets to all timestamps.
    """
    tags = {}
    raw_lines = []
    
    for raw_line in krc_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
            
        # Check tag
        tag_match = TAG_RE.match(line)
        if tag_match:
            tags[tag_match.group(1)] = tag_match.group(2).strip()
            continue
            
        # Check KRC timed line
        line_match = KRC_LINE_RE.match(line)
        if not line_match:
            continue
            
        line_start = int(line_match.group(1))
        line_duration = int(line_match.group(2))
        content = line_match.group(3)
        raw_lines.append((line_start, line_duration, content))

    # Calculate overall offset (internal [offset:xxx] + external adjust)
    internal_offset = 0
    if "offset" in tags:
        try:
            internal_offset = int(tags["offset"])
        except ValueError:
            internal_offset = 0
    total_offset = internal_offset + adjust

    lines = []
    for raw_start, line_duration, content in raw_lines:
        adj_line_start = apply_time_offset(raw_start, total_offset)
        adj_line_end = adj_line_start + line_duration
        
        words = []
        for wm in KRC_WORD_RE.finditer(content):
            word_offset = int(wm.group(1))
            word_duration = int(wm.group(2))
            word_text = wm.group(3)
            
            raw_w_start = raw_start + word_offset
            adj_w_start = apply_time_offset(raw_w_start, total_offset)
            adj_w_end = adj_w_start + word_duration
            
            words.append({
                "start": adj_w_start,
                "duration": word_duration,
                "end": adj_w_end,
                "text": word_text
            })
            
        if not words and content.strip():
            words.append({
                "start": adj_line_start,
                "duration": line_duration,
                "end": adj_line_end,
                "text": content.strip()
            })
            
        line_text = "".join(w["text"] for w in words)
        lines.append({
            "start": adj_line_start,
            "duration": line_duration,
            "end": adj_line_end,
            "text": line_text,
            "words": words
        })
        
    return {
        "tags": tags,
        "lines": lines
    }
