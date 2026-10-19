# -*- coding: utf-8 -*-
r"""
Lyric Timestamp Monotonicity, Acoustic Gate & Outro Validator (v1.1.1)
Ensures that all generated lyric timestamps strictly advance forward in time ($t_k \ge t_{k-1}$).
Protects long musical instrument outros (solos) from being erroneously flagged as 'truncated'.
Enforces Natural Acoustic Gate (flags/prevents 40ms dummy compression artifacts).
Supports foreign lyrics translation line stripping.
Zero external dependencies.
"""

import re
from typing import Dict, Any, Tuple, List

# Preamble credit keywords for whitelist protection
CREDIT_KEYWORD_PATTERN = re.compile(
    r"^(作词|作曲|编曲|制作|监制|和声|混音|录音|吉他|贝斯|鼓|键盘|弦乐|母带|发行|出品|OP|SP|Lyrics|Composed|Arranged|Produced|Written|Mixed|Mastered|词曲|词|曲|此曲为纯音乐)",
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
        if dur <= 80: # Machine dummy compression threshold
            tiny_count += 1
            
    ratio = tiny_count / len(words)
    return (ratio <= 0.15), ratio

def strip_translation_lines_from_parsed(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strips interleaved translation lines (lines with Chinese characters and no verbatim word tags)
    from non-Chinese songs to eliminate jumpy display.
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
            
        # Check Chinese characters
        has_hanzi = bool(re.search(r"[\u4e00-\u9fa5]", text))
        has_kana = bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF]", text))
        
        # If line has Hanzi and no Kana, and no words -> translation line -> strip
        if has_hanzi and not has_kana:
            continue
        else:
            cleaned_lines.append(line)
            
    data["lines"] = cleaned_lines
    return data

def validate_and_fix_lyrics(data: Dict[str, Any], audio_duration_ms: int = 0, is_non_zh: bool = False) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validates and auto-repairs minor monotonicity deviations in parsed lyrics.
    Enforces Natural Acoustic Gate and Outro protection.
    Returns: (is_valid, repaired_data, warnings_list)
    """
    if is_non_zh:
        data = strip_translation_lines_from_parsed(data)
        
    lines = data.get("lines", [])
    warnings = []
    
    if not lines:
        return False, data, ["Empty lyrics track"]

    last_line_start = -1
    for i, line in enumerate(lines):
        line_start = line.get("start", 0)
        
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

    # 3. Outro check: Musical instrument solo differentiation
    if audio_duration_ms > 0 and lines:
        last_lyric_ms = lines[-1].get("end", lines[-1].get("start", 0))
        tail_gap = audio_duration_ms - last_lyric_ms
        if tail_gap > 35000:
            # Long tail gap: Legitimate instrumental outro
            warnings.append(f"Instrumental outro detected ({tail_gap / 1000.0:.1f}s solo). Preserving natural musical silence.")

    return True, data, warnings
