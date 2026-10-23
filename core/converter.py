# -*- coding: utf-8 -*-
"""把统一歌词结构导出为 Enhanced LRC；to_lrc 直接委托 to_spl。"""

import re
from typing import Dict, Any, List, Optional

from .utils.time_utils import format_time_tag
from scripts.lyric_validator import is_credit_line

# P1-1: 署名行判定统一走 lyric_validator.is_credit_line()，此前这里有一套
# startswith(CREDIT_PREFIXES) 的独立白名单（9 个中文词、8 行窗口、单字词不要求冒号），
# 与 validator 的 12 行窗口/必须带冒号口径不一致，导致漏补与重复署名。
CREDIT_SCAN_WINDOW = 12


def _first_sing_line(lines: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """跳过空行与署名行后的第一条正文行，用于计算「首唱时间」与标题去重。

    P0-1: 旧实现直接取 lines[0]。而 to_spl 会先调 enrich_prelude_credits()，
    后者把「词 : X」「曲 : Y」插到 lines 最前面，于是 lines[0] 变成署名行，
    首唱时间从真实值（如 46425ms）变成署名行的 6000ms，
    标题行区间被 min(5500, 6000//2)=3000 压缩，每字从 264ms 掉到 117ms，
    低于铁律 4 的 200ms 下限；同时 lines[0] 的文本变成 "词 : X"，
    使「首句本身就是歌名-歌手」的去重判断永远失效、标题重复输出两遍。
    """
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text or is_credit_line(text):
            continue
        return line
    return None

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

    # 1. Whitelist check: 前 12 行内已有署名行则原样保留（P1-1: 与 validator 同窗口同口径）
    has_credits = any(
        is_credit_line(line.get("text") or "")
        for line in lines[:CREDIT_SCAN_WINDOW]
    )
    if has_credits:
        return data

    # 2. Check prelude gap（P0-1: 必须跳过署名行，否则 "Lyrics : X"@0 会把首唱算成 0）
    _first = _first_sing_line(lines)
    first_sing_start = _first.get("start", 0) if _first else 0
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
    """Collapses 0ms duplicate timestamps on punctuation, e.g. <01:46.18>.<01:46.18>.

    P1-5: 分钟位 1-3 位（旧版写死 \\d{2}，≥100 分钟的音频不会被折叠，与铁律 10 冲突）。
    """
    def repl(m):
        t1, punc, t2 = m.group(1), m.group(2), m.group(3)
        if t1 == t2:
            return punc + "<" + t2 + ">"
        return m.group(0)
    prev = None
    while prev != line:
        prev = line
        line = re.sub(r"<(\d{1,3}:\d{2}\.\d{1,3})>([^\w\s])<(\d{1,3}:\d{2}\.\d{1,3})>", repl, line)
    return line

def format_verbatim_words_line(words: List[Dict[str, Any]], start_ms: int, end_ms: int, precision: int = 2) -> str:
    """
    Unified helper to format a sequence of word tokens into standard Enhanced LRC verbatim string.
    Natively enforces Western word boundary spacing (Rule 14) and pause gap tags (>=300ms).

    precision=3 时输出 [mm:ss.xxx] / <mm:ss.xxx>（源为毫秒精度）；默认 2 位厘秒。
    """
    if not words:
        return ""
        
    line_parts = []
    first_w = words[0]
    first_txt = first_w.get("text", "").replace("\xa0", " ")
    line_parts.append(f"{format_time_tag(start_ms, '[]', precision)}{format_time_tag(first_w['start'], '<>', precision)}{first_txt}")
    
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
            line_parts.append(format_time_tag(prev_end, "<>", precision))
        line_parts.append(f"{format_time_tag(w_cur['start'], '<>', precision)}{cur_txt}")
        
    last_end = words[-1].get("end", end_ms)
    line_parts.append(format_time_tag(last_end, "<>", precision))
    return collapse_duplicate_punc_tags("".join(line_parts))

def to_spl(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "") -> str:
    """
    Exports unified lyric data to standard Enhanced LRC (.lrc) and Salt Player (.spl) format.
    Standard Enhanced LRC line format: [01:26.48]<01:26.48>唱<01:27.20>词<01:27.42>字<01:30.00>
    """
    # P0-1: 首唱时间与标题去重都必须用 enrich **之前**的数据快照。
    # enrich 会把署名行插到 lines 最前面，之后再读 lines[0] 会拿到 "词 : X"@6000。
    _raw_lines = data.get("lines", [])
    _first_sing = _first_sing_line(_raw_lines)
    first_sing_start = _first_sing.get("start", 0) if _first_sing else 0
    first_text_snapshot = (_first_sing.get("text") or "").strip() if _first_sing else ""

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

    # v1.2.8 精度策略：源是 3 位毫秒就出 3 位，源只有 1-2 位才退回 2 位。
    # time_precision 由解析层（lrc/ttml/krc parser）写入，缺省按 2 位保持向后兼容。
    try:
        _precision = int(enriched_data.get("time_precision", 2) or 2)
    except (TypeError, ValueError):
        _precision = 2
    if _precision not in (2, 3):
        _precision = 2

    # Intelligent deduplication: 用 enrich 前的首句文本做比对
    has_existing_title = False
    if first_text_snapshot:
        first_clean = re.sub(r"\s*-\s*", " - ", first_text_snapshot)
        target_clean = re.sub(r"\s*-\s*", " - ", title_line)
        if first_clean == target_clean or (t and first_clean == t):
            has_existing_title = True

    if title_line and not has_existing_title:
        if first_sing_start >= 6000:
            st = 1000
            et = min(5500, first_sing_start // 2)
            title_words = make_char_words(title_line, st, et)
            output_lines.append(format_verbatim_words_line(title_words, title_words[0]["start"], et, _precision))
        else:
            output_lines.append(f"{format_time_tag(0, '[]', _precision)}{title_line}")

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
            output_lines.append(format_verbatim_words_line(words, start_ms, end_ms, _precision))
        else:
            output_lines.append(f"{format_time_tag(start_ms, '[]', _precision)}{line_text}")

        # ELRC standard: if translation exists, append subtitle line with identical start timestamp
        if trans_text:
            output_lines.append(f"{format_time_tag(start_ms, '[]', _precision)}{trans_text}")
            
    res = "\n".join(output_lines) + "\n"
    return res.replace("\xa0", " ")

def to_lrc(data: Dict[str, Any], title: str = "", artist: str = "", lyricist: str = "", composer: str = "") -> str:
    """
    Exports unified lyric data to LRC format.
    Delegates directly to to_spl to guarantee 100% SHA-256 trilateral mirror parity.

    注：旧签名里有一个从未被使用的 ``verbatim_lrc`` 形参，v1.2.4 起移除。
    """
    return to_spl(data, title, artist, lyricist, composer)
