# -*- coding: utf-8 -*-
"""把统一歌词结构导出为 Enhanced LRC；to_lrc 直接委托 to_spl。"""

import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

from .utils.time_utils import format_time_tag
from .utils.text_utils import (
    make_char_words,
    collapse_duplicate_punc_tags,  # noqa: F401  兼容再导出：唯一实现在 text_utils
    format_verbatim_words_line,
)
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
        # B3 链式前奏时序调度器（v1.4.0）：标题行 -> 作词 -> 作曲 -> 首唱，
        # 各段严格留 ≥100ms 间距，消除标题行（≤5.5s）与署名行（旧算法 4.84s）的数学重叠。
        title_end_ms = min(5500, first_sing_start // 2) if first_sing_start >= 6000 else 0
        curr_cursor = max(1000, title_end_ms + 100)

        new_credits = []
        if lyricist:
            st = max(curr_cursor, min(6000, first_sing_start // 3))
            et = st + 3000
            text = f"词 : {lyricist}"
            new_credits.append({
                "start": st,
                "duration": 3000,
                "end": et,
                "text": text,
                "words": make_char_words(text, st, et),
                "synthetic": True,      # v1.6.0: 合成署名行，禁止进对齐目标/门禁/精度评价
            })
            curr_cursor = et + 100
        if composer:
            st = max(curr_cursor, min(9500, (first_sing_start * 2) // 3))
            et = st + 3000
            text = f"曲 : {composer}"
            new_credits.append({
                "start": st,
                "duration": 3000,
                "end": et,
                "text": text,
                "words": make_char_words(text, st, et),
                "synthetic": True,      # v1.6.0: 同上
            })
            curr_cursor = et + 100
        # 链条不得压迫首唱（数学上在 enrich 定义域 first_sing≥12000 恒成立，保底断言）
        assert curr_cursor <= first_sing_start - 100, "Prelude credits exceed first singing start!"
        if new_credits:
            return {**data, "lines": new_credits + lines}

    return data

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

    # H18: 当数据来源为 lrc-red 时，输出标准回链署名标签
    src = str(data.get("source") or "").lower()
    prov = data.get("provenance")
    prov_src = str(prov.get("source") or "").lower() if isinstance(prov, dict) else ""
    if src == "lrc-red" or prov_src == "lrc-red":
        output_lines.append("[re:lrc.red (https://lrc.red)]")

    from .utils.text_utils import format_display_title, match_display_title
    title_line = format_display_title(t, a)

    # v1.2.8 精度策略：源是 3 位毫秒就出 3 位，源只有 1-2 位才退回 2 位。
    # time_precision 由解析层（lrc/ttml/krc parser）写入，缺省按 2 位保持向后兼容。
    try:
        _precision = int(enriched_data.get("time_precision", 2) or 2)
    except (TypeError, ValueError):
        _precision = 2
    if _precision not in (2, 3):
        _precision = 2

    # Intelligent deduplication: 用 enrich 前的首句文本做比对。
    # 标题识别必须走共享 matcher（与 R2 / is_title_line 同一出处）：此前的
    # " - " 空格归一化比较只认唯一一种写法，倒序、全角连字符、括号别名等
    # 真实变体会被判为非标题，导致重复合成标题行。
    has_existing_title = False
    if first_text_snapshot:
        if t and first_text_snapshot.strip() == t.strip():
            has_existing_title = True
        elif match_display_title(first_text_snapshot, t, a):
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
            # 逐字文本与行文本一致性校验（P0-4 绝对红线守护：过滤全部空白抖动，中英文标点与字词 100% 严密守护）
            joined_words_text = re.sub(r'\s+', '', "".join(w.get("text", "") for w in words))
            clean_line_text = re.sub(r'\s+', '', line_text)
            if joined_words_text != clean_line_text:
                logger.warning("   ⚠️ [converter] 逐字字面与行文本不一致，降级为行级输出: %r vs %r", joined_words_text, clean_line_text)
                output_lines.append(f"{format_time_tag(start_ms, '[]', _precision)}{line_text}")
            else:
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
