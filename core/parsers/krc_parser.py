# -*- coding: utf-8 -*-
"""
KRC Lyric Parser (v1.2.2)
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

TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
KRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
KRC_WORD_RE = re.compile(r"<(\d+),(\d+),\d+>([^<]*)")

def extract_krc_language_translation(krc_text: str) -> List[str]:
    """
    Parses and extracts embedded translations from Kugou KRC [language:...] tag.
    Returns a list of translation strings matching KRC lines index 1:1.
    """
    for line in krc_text.splitlines():
        if line.startswith("[language:"):
            try:
                b64_str = line[10:-1].strip()
                decoded_json = base64.b64decode(b64_str).decode("utf-8")
                data = json.loads(decoded_json)
                content = data.get("content", [])
                if content:
                    lyric_content = content[0].get("lyricContent", [])
                    return [item[0] if item else "" for item in lyric_content]
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
    internal_offset = 0
    if "offset" in tags:
        try:
            internal_offset = int(tags["offset"])
        except ValueError:
            internal_offset = 0
    total_offset = internal_offset + adjust

    # Extract embedded translations if present
    trans_list = extract_krc_language_translation(krc_text)
    if not trans_list and "language" in tags:
        try:
            decoded_json = base64.b64decode(tags["language"]).decode("utf-8")
            data = json.loads(decoded_json)
            content = data.get("content", [])
            if content:
                trans_list = [item[0] if item else "" for item in content[0].get("lyricContent", [])]
        except Exception:
            trans_list = []

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
