# -*- coding: utf-8 -*-
"""
Lyric Converter (v1.1.3)
Converts unified parsed lyric structures into standard Enhanced LRC (.lrc) and Salt Player (.spl) formats.
Adheres strictly to the international Enhanced LRC (ELRC / A2 extension) standard.
Line format: [01:26.48]<01:26.48>我<01:27.20>就<01:27.42>在<01:30.00>
Supports Trilateral 100% SHA-256 verbatim mirror mode between audio embedded lyrics, .spl, and .lrc.
Refactored as pure functions (zero in-place mutation of input data) and reuses time_utils.
Zero external dependencies.
"""

import re
from typing import Dict, Any, List

from .utils.time_utils import format_time_tag

CREDIT_PREFIXES = ("词", "曲", "编", "作词", "作曲", "编曲", "制作", "录音", "混音")

def make_char_words(text: str, start_ms: int, end_ms: int) -> List[Dict[str, Any]]:
    """Helper to generate smooth character-level verbatim timestamps for credit lines."""
    dur = max(100, end_ms - start_ms)
    n = len(text)
    if n == 0:
        return []
    step = dur / n
    return [
        {
            "text": char,
            "start": int(start_ms + i * step),
            "end": int(start_ms + (i + 1) * step)
        }
        for i, char in enumerate(text)
    ]

def enrich_prelude_credits(data: Dict[str, Any], lyricist: str = "", composer: str = "") -> Dict[str, Any]:
    """
    Enriches prelude with standardized credits under strict whitelist protection:
    1. If original lyrics already contain '词' / '曲', 100% preserve untouched.
    2. If missing and lyricist/composer info exists, supplement standardized lines.
    3. If neither exists, keep prelude clean without empty tags.
    Pure function: Never mutates input dictionary in-place.
    """
    lines = list(data.get("lines", []))
    if not lines:
        return data

    # 1. Whitelist check: check first 8 lines
    has_credits = any(
        any(line.get("text", "").strip().startswith(p) for p in CREDIT_PREFIXES)
        for line in lines[:8]
    )
    if has_credits:
        return data

    # 2. Check prelude gap
    first_sing_start = lines[0].get("start", 0)
    if first_sing_start >= 12000 and (lyricist or composer):
        new_credits = []
        if lyricist:
            st = min(6000, first_sing_start // 3)
            et = st + 3000
            text = f"作词 : {lyricist}"
            new_credits.append({
                "start": st,
                "duration": 3000,
                "end": et,
                "text": text,
                "words": make_char_words(text, st, et)
            })
        if composer:
            st = min(9500, (first_sing_start * 2) // 3)
            et = st + 3000
            text = f"作曲 : {composer}"
            new_credits.append({
                "start": st,
                "duration": 3000,
                "end": et,
                "text": text,
                "words": make_char_words(text, st, et)
            })
        if new_credits:
            return {**data, "lines": new_credits + lines}

    return data

def collapse_duplicate_punc_tags(line: str) -> str:
    """Collapses 0ms duplicate timestamps on punctuation, e.g. <01:46.18>.<01:46.18>."""
    def repl(m):
        t1, punc, t2 = m.group(1), m.group(2), m.group(3)
        if t1 == t2:
            return punc + "<" + t2 + ">"
        return m.group(0)
    prev = None
    while prev != line:
        prev = line
        line = re.sub(r"<(\d{2}:\d{2}\.\d{2,3})>([^\w\s])<(\d{2}:\d{2}\.\d{2,3})>", repl, line)
    return line

def to_spl(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "") -> str:
    """
    Exports unified lyric data to standard Enhanced LRC (.lrc) and Salt Player (.spl) format.
    Standard Enhanced LRC line format: [01:26.48]<01:26.48>我<01:27.20>就<01:27.42>在<01:30.00>
    """
    enriched_data = enrich_prelude_credits(data, lyricist, composer)
    tags = enriched_data.get("tags", {})
    lines = enriched_data.get("lines", [])
    
    t = title or tags.get("ti", "")
    a = artist or tags.get("ar", "")
    
    output_lines = []
    if t:
        output_lines.append(f"[ti:{t}]")
    if a:
        output_lines.append(f"[ar:{a}]")

    title_line = f"{t} - {a}" if (t and a) else t
    first_sing_start = lines[0].get("start", 0) if lines else 0

    # Intelligent deduplication: check if existing first line is already the title
    has_existing_title = False
    if lines:
        first_clean = re.sub(r"\s*-\s*", " - ", lines[0].get("text", "").strip())
        target_clean = re.sub(r"\s*-\s*", " - ", title_line)
        if first_clean == target_clean or (t and first_clean == t):
            has_existing_title = True

    if title_line and not has_existing_title:
        if first_sing_start >= 6000:
            st = 1000
            et = min(5500, first_sing_start // 2)
            words = make_char_words(title_line, st, et)
            line_parts = [f"{format_time_tag(words[0]['start'], '[]')}{format_time_tag(words[0]['start'], '<>')}{words[0]['text']}"]
            for i in range(1, len(words)):
                w_prev = words[i - 1]
                w_cur = words[i]
                prev_end = w_prev.get("end", w_prev.get("start", 0))
                # If there is a noticeable pause (>= 300ms) between words, insert explicit pause gap tag
                if w_cur.get("start", 0) - prev_end >= 300:
                    line_parts.append(format_time_tag(prev_end, "<>"))
                line_parts.append(f"{format_time_tag(w_cur['start'], '<>')}{w_cur['text']}")
            line_parts.append(format_time_tag(et, "<>"))
            output_lines.append("".join(line_parts))
        else:
            output_lines.append(f"[00:00.00]{title_line}")

    for line in lines:
        start_ms = line.get("start", 0)
        end_ms = line.get("end", start_ms + line.get("duration", 3000))
        words = line.get("words", [])
        line_text = line.get("text", "").strip()
        
        if not line_text:
            continue
            
        # If line has word-level timestamps (Standard Enhanced LRC format)
        if words:
            line_parts = []
            first_w = words[0]
            line_parts.append(f"{format_time_tag(start_ms, '[]')}{format_time_tag(first_w['start'], '<>')}{first_w['text']}")
            
            for i in range(1, len(words)):
                w_prev = words[i - 1]
                w_cur = words[i]
                prev_end = w_prev.get("end", w_prev.get("start", 0))
                # If there is a noticeable pause (>= 300ms) between words, insert explicit pause gap tag
                if w_cur.get("start", 0) - prev_end >= 300:
                    line_parts.append(format_time_tag(prev_end, "<>"))
                line_parts.append(f"{format_time_tag(w_cur['start'], '<>')}{w_cur['text']}")
                
            last_end = words[-1].get("end", end_ms)
            line_parts.append(format_time_tag(last_end, "<>"))
            
            full_line = collapse_duplicate_punc_tags("".join(line_parts))
            output_lines.append(full_line)
        else:
            # Fallback for lines without words
            start_tag = format_time_tag(start_ms, "[]")
            output_lines.append(f"{start_tag}{line_text}")
            
    return "\n".join(output_lines) + "\n"

def to_lrc(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "", verbatim_lrc: bool = True) -> str:
    """
    Exports unified lyric data to LRC format.
    If verbatim_lrc=True (default), produces identical verbatim timing to to_spl for 100% SHA-256 mirror.
    If verbatim_lrc=False, produces standard line-level [mm:ss.xx] text format.
    """
    if verbatim_lrc:
        return to_spl(data, title, artist, lyricist, composer)
        
    enriched_data = enrich_prelude_credits(data, lyricist, composer)
    tags = enriched_data.get("tags", {})
    lines = enriched_data.get("lines", [])
    
    t = title or tags.get("ti", "")
    a = artist or tags.get("ar", "")
    
    output_lines = []
    if t:
        output_lines.append(f"[ti:{t}]")
    if a:
        output_lines.append(f"[ar:{a}]")
    has_existing_title = False
    target_clean = re.sub(r"\s*-\s*", " - ", f"{t} - {a}" if (t and a) else t)
    if lines:
        first_clean = re.sub(r"\s*-\s*", " - ", lines[0].get("text", "").strip())
        if first_clean == target_clean or (t and first_clean == t):
            has_existing_title = True

    if not has_existing_title:
        if t and a:
            output_lines.append(f"[00:00.00]{t} - {a}")
        elif t:
            output_lines.append(f"[00:00.00]{t}")
        
    for line in lines:
        start_ms = line.get("start", 0)
        text = line.get("text", "").strip()
        if not text:
            continue
        time_tag = format_time_tag(start_ms, "[]")
        output_lines.append(f"{time_tag}{text}")
        
    return "\n".join(output_lines) + "\n"
