# -*- coding: utf-8 -*-
r"""
Lyric Timestamp Monotonicity, Acoustic Gate, Intro & Outro Validator (v1.2.4)
Ensures that all generated lyric timestamps strictly advance forward in time ($t_k \ge t_{k-1}$).
Protects long musical instrument outros (solos) from being erroneously flagged as 'truncated'.
Enforces Natural Acoustic Gate (flags/prevents 40ms dummy compression artifacts).
Enforces Intro Gate (warns on unusually delayed singing start > 32s).
Natively pairs interleaved legacy dual-track translation lines into standard ELRC format.
Zero external dependencies.
"""

import re
from typing import Dict, Any, Tuple, List

from core.utils.text_utils import is_translation_line

# Preamble credit keywords for whitelist protection
# P1-8: 单字 "词/曲" 必须带冒号才算署名行，否则 "词不达意"、"曲终人散" 会被误判
CREDIT_KEYWORD_PATTERN = re.compile(
    r"^(作词|作曲|编曲|制作|监制|录音|混音|吉他|贝斯|鼓|键盘|母带|弦乐|出品|OP|SP|"
    r"Lyrics|Composed|Arranged|Produced|Written|Mixed|Mastered|작사|작곡|편곡|作詞|作曲|編曲)"
    r"|^(词|曲)\s*[:：]",
    re.IGNORECASE
)

def is_credit_line(line_text: str) -> bool:
    clean = re.sub(r"\[\d{2}:\d{2}\.\d{2,3}\]", "", line_text).strip()
    clean = re.sub(r"<\d{2}:\d{2}\.\d{2,3}>", "", clean).strip()
    return bool(CREDIT_KEYWORD_PATTERN.search(clean))

def check_acoustic_naturalness(words: List[Dict[str, Any]]) -> Tuple[bool, float]:
    """
    Checks whether word durations conform to natural acoustic human singing (>= 100ms).
    Returns: (is_healthy, tiny_ratio)
    """
    if not words or len(words) < 2:
        return True, 0.0
        
    tiny_count = 0
    for w in words:
        dur = w.get("duration", 0)
        if dur <= 80:  # Machine dummy compression threshold
            tiny_count += 1
            
    ratio = tiny_count / len(words)
    return (ratio <= 0.15), ratio

def strip_translation_lines_from_parsed(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pairs interleaved translation lines from legacy dual-track LRCs into modern ELRC format,
    attaching the translation to the preceding singing line without causing screen jumping.
    """
    lines = data.get("lines", [])
    if not lines:
        return data
        
    cleaned_lines = []
    for line in lines:
        text = line.get("text", "").strip()
        words = line.get("words", [])
        
        # Keep credit lines
        if is_credit_line(text):
            cleaned_lines.append(line)
            continue
            
        # If line has words -> keep
        if words:
            cleaned_lines.append(line)
            continue
            
        # Check translation line using shared text_utils
        if is_translation_line(text, has_words=bool(words)):
            # Attach to previous singing line if available
            if cleaned_lines and not cleaned_lines[-1].get("translation"):
                cleaned_lines[-1]["translation"] = text
            continue
        else:
            cleaned_lines.append(line)
            
    return {**data, "lines": cleaned_lines}

def validate_and_fix_lyrics(data: Dict[str, Any], audio_duration_ms: int = 0, is_non_zh: bool = False) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validates and auto-repairs minor monotonicity deviations in parsed lyrics.
    Enforces Natural Acoustic Gate, Intro Gate, and Outro protection.
    Returns: (is_valid, repaired_data, warnings_list)
    """
    if is_non_zh:
        data = strip_translation_lines_from_parsed(data)
        
    lines = list(data.get("lines", []))
    warnings = []
    
    if not lines:
        return False, data, ["Empty lyrics track"]

    last_line_start = -1
    first_sing_start = None

    for i, line in enumerate(lines):
        line_start = line.get("start", 0)
        line_text = line.get("text", "").strip()
        
        # Track first sung lyric start (ignoring credit preamble)
        if first_sing_start is None and not is_credit_line(line_text):
            first_sing_start = line_start
        
        # 1. Check line monotonic progression
        if line_start < last_line_start:
            warnings.append(f"Line {i} timestamp inversion: {line_start}ms < previous {last_line_start}ms. Auto-correcting.")
            line["start"] = last_line_start + 100
            line_start = line["start"]
            
        last_line_start = line_start
        
        # 2. Check word monotonic progression & acoustic naturalness
        words = line.get("words", [])
        last_word_start = line_start
        for k, w in enumerate(words):
            w_start = w.get("start", 0)
            if w_start < last_word_start:
                warnings.append(f"Line {i} Word {k} timestamp inversion: {w_start}ms < {last_word_start}ms. Auto-correcting.")
                w["start"] = last_word_start
                w_start = last_word_start
                
            w_dur = max(80, w.get("duration", 150))
            w["duration"] = w_dur
            w["end"] = w_start + w_dur
            last_word_start = w_start

        # Natural Acoustic Gate
        if words:
            healthy, ratio = check_acoustic_naturalness(words)
            if not healthy:
                warnings.append(f"Line {i} Natural Acoustic Alert: {ratio*100:.1f}% words <= 80ms.")

        if words:
            line["end"] = max(line_start + 500, words[-1]["end"])
        else:
            line["end"] = line_start + line.get("duration", 3000)

    # 3. Intro Gate: Check unusually delayed singing start
    if audio_duration_ms > 90000 and first_sing_start is not None:
        if first_sing_start > 32000:
            warnings.append(f"Intro Gate Alert: First lyric starts unusually late ({first_sing_start / 1000.0:.2f}s). Please verify if intro stage banter or cut exists.")

    # 4. Outro check: Musical instrument solo differentiation
    if audio_duration_ms > 0 and lines:
        last_lyric_ms = lines[-1].get("end", lines[-1].get("start", 0))
        tail_gap = audio_duration_ms - last_lyric_ms
        if tail_gap > 35000:
            warnings.append(f"Instrumental outro detected ({tail_gap / 1000.0:.1f}s solo). Preserving natural musical silence.")

    return True, {**data, "lines": lines}, warnings
