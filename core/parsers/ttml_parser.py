# -*- coding: utf-8 -*-
"""
Apple Music TTML Lyric Parser
Parses TTML XML lyrics (Apple Music Lossless Lyrics format) into unified word-level timestamps.
Zero external dependencies.
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Any

def parse_time_str(time_str: str) -> int:
    """Converts '00:29.231' or '01:05.50' into milliseconds."""
    if not time_str:
        return 0
    parts = time_str.split(":")
    if len(parts) == 2:
        mins = int(parts[0])
        secs_parts = parts[1].split(".")
        secs = int(secs_parts[0])
        ms = int(secs_parts[1].ljust(3, "0")[:3]) if len(secs_parts) > 1 else 0
        return (mins * 60 + secs) * 1000 + ms
    elif len(parts) == 3:
        hrs = int(parts[0])
        mins = int(parts[1])
        secs_parts = parts[2].split(".")
        secs = int(secs_parts[0])
        ms = int(secs_parts[1].ljust(3, "0")[:3]) if len(secs_parts) > 1 else 0
        return (hrs * 3600 + mins * 60 + secs) * 1000 + ms
    return 0

def parse_ttml(ttml_text: str) -> Dict[str, Any]:
    """
    Parses TTML XML into structured lines and word timestamps.
    """
    tags = {}
    lines = []
    
    # Extract metadata using regex or XML
    for m in re.finditer(r'<amll:meta\s+key="([^"]+)"\s+value="([^"]*)"', ttml_text):
        key = m.group(1)
        val = m.group(2)
        if key == "musicName":
            tags["ti"] = val
        elif key == "artists":
            tags["ar"] = val
        elif key == "album":
            tags["al"] = val
            
    # Parse <p> and <span> elements
    p_matches = re.finditer(r'<p\s+begin="([^"]+)"\s+end="([^"]+)"[^>]*>(.*?)</p>', ttml_text, re.DOTALL)
    for pm in p_matches:
        p_begin = parse_time_str(pm.group(1))
        p_end = parse_time_str(pm.group(2))
        p_body = pm.group(3)
        
        words = []
        span_matches = re.finditer(r'<span\s+begin="([^"]+)"\s+end="([^"]+)"[^>]*>([^<]*)</span>', p_body)
        for sm in span_matches:
            w_begin = parse_time_str(sm.group(1))
            w_end = parse_time_str(sm.group(2))
            w_text = sm.group(3)
            words.append({
                "start": w_begin,
                "duration": max(50, w_end - w_begin),
                "end": w_end,
                "text": w_text
            })
            
        line_text = "".join(w["text"] for w in words) if words else re.sub(r'<[^>]+>', '', p_body).strip()
        if not words and line_text:
            words.append({
                "start": p_begin,
                "duration": max(500, p_end - p_begin),
                "end": p_end,
                "text": line_text
            })
            
        lines.append({
            "start": p_begin,
            "duration": p_end - p_begin,
            "end": p_end,
            "text": line_text,
            "words": words
        })
        
    return {
        "tags": tags,
        "lines": lines
    }
