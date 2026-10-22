# -*- coding: utf-8 -*-
"""
Lyric Converter (v1.2.4)
Converts unified parsed lyric structures into standard Enhanced LRC (.lrc) and Salt Player (.spl) formats.
Adheres strictly to the international Enhanced LRC (ELRC / A2 extension) standard.
Line format: [01:26.48]<01:26.48>唱<01:27.20>词<01:27.42>字<01:30.00>
Supports Trilateral 100% SHA-256 verbatim mirror mode between audio embedded lyrics, .spl, and .lrc.
Natively enforces Western word boundary spacing normalization and global \\xa0 fake space elimination.
Supports ELRC bilingual subtitle pairing without screen jumping.
Refactored as pure functions (zero in-place mutation of input data) and reuses time_utils.
Zero external dependencies.
"""

import re
from typing import Dict, Any, List

from .utils.time_utils import format_time_tag

CREDIT_PREFIXES = ("作词", "作曲", "编曲", "制作", "监制", "词", "曲", "录音", "混音")

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
            text = f"词 : {lyricist}"
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
            text = f"曲 : {composer}"
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

def format_verbatim_words_line(words: List[Dict[str, Any]], start_ms: int, end_ms: int) -> str:
    """
    Unified helper to format a sequence of word tokens into standard Enhanced LRC verbatim string.
    Natively enforces Western word boundary spacing (Rule 14) and pause gap tags (>=300ms).
    """
    if not words:
        return ""
        
    line_parts = []
    first_w = words[0]
    first_txt = first_w.get("text", "").replace("\xa0", " ")
    line_parts.append(f"{format_time_tag(start_ms, '[]')}{format_time_tag(first_w['start'], '<>')}{first_txt}")
    
    for i in range(1, len(words)):
        w_prev = words[i - 1]
        w_cur = words[i]
        prev_txt = w_prev.get("text", "").replace("\xa0", " ")
        cur_txt = w_cur.get("text", "").replace("\xa0", " ")
        prev_end = w_prev.get("end", w_prev.get("start", 0))

        # 词边界空格（Rule 14）
        # join 是解析层给出的"源里有没有空格"的权威信息：
        #   None  → 该源不提供边界信息（KRC / LRC / 合成词），沿用 ASCII 启发式
        #   space → 源里明确有空格，补上
        #   none  → 源里就是紧邻的（音节切分），绝不能插空格
        join = w_prev.get("join")
        if prev_txt and cur_txt and not prev_txt.endswith(" ") and not cur_txt.startswith(" "):
            if join is None:
                if re.search(r"[a-zA-Z0-9,\.!\?;:'\"]$", prev_txt) and re.search(r"^[a-zA-Z0-9]", cur_txt):
                    cur_txt = " " + cur_txt
            elif join == "space":
                cur_txt = " " + cur_txt

        # Insert explicit pause gap tag if inter-word gap >= 300ms
        if w_cur.get("start", 0) - prev_end >= 300:
            line_parts.append(format_time_tag(prev_end, "<>"))
        line_parts.append(f"{format_time_tag(w_cur['start'], '<>')}{cur_txt}")
        
    last_end = words[-1].get("end", end_ms)
    line_parts.append(format_time_tag(last_end, "<>"))
    return collapse_duplicate_punc_tags("".join(line_parts))

def to_spl(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "") -> str:
    """
    Exports unified lyric data to standard Enhanced LRC (.lrc) and Salt Player (.spl) format.
    Standard Enhanced LRC line format: [01:26.48]<01:26.48>唱<01:27.20>词<01:27.42>字<01:30.00>
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
            title_words = make_char_words(title_line, st, et)
            output_lines.append(format_verbatim_words_line(title_words, title_words[0]["start"], et))
        else:
            output_lines.append(f"[00:00.00]{title_line}")

    for line in lines:
        start_ms = line.get("start", 0)
        end_ms = line.get("end", start_ms + line.get("duration", 3000))
        words = line.get("words", [])
        line_text = line.get("text", "").replace("\xa0", " ").strip()
        trans_text = line.get("translation", "").replace("\xa0", " ").strip() if line.get("translation") else ""
        
        if not line_text:
            continue
            
        # Word-level verbatim line (Standard Enhanced LRC format)
        if words:
            output_lines.append(format_verbatim_words_line(words, start_ms, end_ms))
        else:
            output_lines.append(f"{format_time_tag(start_ms, '[]')}{line_text}")

        # ELRC standard: if translation exists, append subtitle line with identical start timestamp
        if trans_text:
            output_lines.append(f"{format_time_tag(start_ms, '[]')}{trans_text}")
            
    res = "\n".join(output_lines) + "\n"
    return res.replace("\xa0", " ")

def to_lrc(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "") -> str:
    """
    Exports unified lyric data to LRC format.
    Delegates directly to to_spl to guarantee 100% SHA-256 trilateral mirror parity.

    注：旧签名里有一个从未被使用的 ``verbatim_lrc`` 形参，v1.2.4 起移除。
    """
    return to_spl(data, title, artist, lyricist, composer)
