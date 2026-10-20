# -*- coding: utf-8 -*-
"""
Apple Music TTML Lyric Parser (v1.1.3)
Parses TTML XML lyrics (Apple Music Lossless Lyrics format) into unified word-level timestamps.
Supports colon formats (mm:ss.xxx, hh:mm:ss.xxx) as well as SMPTE seconds (e.g. 84.5s).
Zero external dependencies.
"""

import re
from typing import Dict, List, Any

from ..utils.time_utils import parse_time_string_to_ms, apply_time_offset

def parse_ttml(ttml_text: str, adjust: int = 0) -> Dict[str, Any]:
    """
    Parses TTML XML into structured lines and word timestamps.
    Applies adjust offsets with boundary clamping.
    """
    tags = {}
    lines = []
    
    # Extract metadata using regex
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
        raw_p_begin = parse_time_string_to_ms(pm.group(1))
        raw_p_end = parse_time_string_to_ms(pm.group(2))
        p_body = pm.group(3)
        
        p_begin = apply_time_offset(raw_p_begin, adjust)
        p_end = apply_time_offset(raw_p_end, adjust)
        if p_end < p_begin:
            p_end = p_begin + 2500
        
        words = []
        span_matches = re.finditer(r'<span\s+begin="([^"]+)"\s+end="([^"]+)"[^>]*>([^<]*)</span>', p_body)
        for sm in span_matches:
            raw_w_begin = parse_time_string_to_ms(sm.group(1))
            raw_w_end = parse_time_string_to_ms(sm.group(2))
            w_text = sm.group(3)
            
            w_begin = apply_time_offset(raw_w_begin, adjust)
            w_end = apply_time_offset(raw_w_end, adjust)
            w_dur = max(40, w_end - w_begin)
            
            words.append({
                "start": w_begin,
                "duration": w_dur,
                "end": w_begin + w_dur,
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
