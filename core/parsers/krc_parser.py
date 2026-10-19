# -*- coding: utf-8 -*-
"""
KRC Lyric Parser
Parses raw KRC text into structured line-by-line and word-by-word timestamps.
"""

import re
from typing import Dict, List, Any

TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
KRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
KRC_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")

def parse_krc(krc_text: str) -> Dict[str, Any]:
    """
    Parses decrypted KRC text into a dictionary:
    {
        "tags": {"ti": "...", "ar": "...", ...},
        "lines": [
            {
                "start": 1234,  # ms
                "duration": 2500,
                "end": 3734,
                "text": "头顶的太阳",
                "words": [
                    {"start": 1234, "duration": 680, "end": 1914, "text": "头"},
                    ...
                ]
            }
        ]
    }
    """
    tags = {}
    lines = []
    
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
        line_end = line_start + line_duration
        content = line_match.group(3)
        
        words = []
        for wm in KRC_WORD_RE.finditer(content):
            word_offset = int(wm.group(1))
            word_duration = int(wm.group(2))
            word_text = wm.group(3)
            
            w_start = line_start + word_offset
            w_end = w_start + word_duration
            words.append({
                "start": w_start,
                "duration": word_duration,
                "end": w_end,
                "text": word_text
            })
            
        # If no word tags parsed, fallback to whole line
        if not words and content.strip():
            words.append({
                "start": line_start,
                "duration": line_duration,
                "end": line_end,
                "text": content.strip()
            })
            
        line_text = "".join(w["text"] for w in words)
        lines.append({
            "start": line_start,
            "duration": line_duration,
            "end": line_end,
            "text": line_text,
            "words": words
        })
        
    return {
        "tags": tags,
        "lines": lines
    }
