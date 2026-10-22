# -*- coding: utf-8 -*-
"""
KRC Lyric Parser (v1.2.4)
Parses raw KRC text into structured line-by-line and word-by-word timestamps.
Supports [offset:xxx] tags from lyrics metadata and server-side adjust offsets.
Natively decodes Kugou Base64 JSON [language:...] tag into 1:1 line-level embedded translations.
Zero external dependencies.
"""

import re
import base64
import json
from typing import Dict, List, Any, Optional

from ..utils.time_utils import apply_time_offset
from ._common import parse_offset_tag

TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
KRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
KRC_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")

def extract_krc_language_translation(krc_text: str, tags: Optional[Dict[str, str]] = None) -> List[str]:
    """
    Parses and extracts embedded translations from Kugou KRC [language:...] tag.
    Accepts either raw krc_text or already parsed tags dictionary.
    Returns a list of translation strings matching KRC lines index 1:1.
    """
    b64_str = ""
    if tags and "language" in tags:
        b64_str = tags["language"].strip()
    else:
        for line in krc_text.splitlines():
            if line.startswith("[language:"):
                b64_str = line[10:-1].strip()
                break
                
    if not b64_str:
        return []
        
    try:
        decoded_json = base64.b64decode(b64_str).decode("utf-8")
        data = json.loads(decoded_json)
        content = data.get("content", [])
        # Enforce strict Semantic Translation Gate:
        # Kugou defines: type == 1 -> Chinese semantic translation
        #                type == 0 -> Phonetic transliteration / Romaji / 空耳 (STRICTLY PROHIBITED)
        # We must iterate all blocks and only accept type == 1 blocks that contain genuine Hanzi.
        for cnt in content:
            if cnt.get("type") == 1:
                lyric_content = cnt.get("lyricContent", [])
                lines = []
                for item in lyric_content:
                    if isinstance(item, list) and item:
                        lines.append(str(item[0]).strip())
                    elif isinstance(item, str):
                        lines.append(item.strip())
                    else:
                        lines.append("")
                # Validate semantic validity: must contain genuine Chinese characters
                combined = "".join(lines)
                if re.search(r"[\u4e00-\u9fff]", combined):
                    return lines
    except Exception:
        pass
        
    return []

def parse_krc(krc_text: str, adjust: int = 0) -> Dict[str, Any]:
    """
    Parses decrypted KRC text into structured format.
    Applies metadata [offset:xxx] and caller-specified adjust offsets to all timestamps.
    Natively maps embedded [language:...] translations to lines 1:1.
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
    total_offset = parse_offset_tag(tags) + adjust

    # Single-point extraction of embedded translations
    trans_list = extract_krc_language_translation(krc_text, tags)

    lines = []
    for i, (raw_start, line_duration, content) in enumerate(raw_lines):
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
        trans_text = trans_list[i] if i < len(trans_list) else ""
        lines.append({
            "start": adj_line_start,
            "duration": line_duration,
            "end": adj_line_end,
            "text": line_text,
            "translation": trans_text,
            "words": words
        })
        
    return {
        "tags": tags,
        "lines": lines,
        "has_embedded_translation": bool(any(t.strip() for t in trans_list)),
        "embedded_translations": trans_list
    }
