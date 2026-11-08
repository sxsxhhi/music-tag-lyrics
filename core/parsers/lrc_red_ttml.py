# -*- coding: utf-8 -*-
"""lrc.red TTML 解析与元数据适配器 (v1.8.0)。

解决的核心技术差异：
1. lrc.red TTML 的 <head> 缺失 <amll:meta>，导致原 ttml_parser 抽取的 ti/ar/al 为空；
   本模块支持将外部获取的结构化元数据（JSON 顶层或 search 命中项）安全注入 tags，严防下游门禁误杀。
2. 兼容纯浮点秒格式的时间戳（如 begin="16.906"）与 mm:ss.xxx 格式。
3. 支持提取并保留 songwriters 作词/作曲者名单，用于标签治理。
4. 正确处理 <span ttm:role="x-bg"> 和声伴唱与 <span ttm:role="x-translation"> 译文。
5. 保护 CJK 语言词间不误加空格，英文单词间保留标准空格。
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, List, Optional

from ..utils.text_utils import is_fullwidth_char, is_hanzi_char, is_kana_char
from ..utils.time_utils import parse_time_string_to_ms, apply_time_offset

try:
    from scripts.lyric_validator import is_credit_line
except ImportError:
    def is_credit_line(text: str) -> bool:
        t = (text or "").strip().lower()
        return any(t.startswith(p) for p in ("词:", "词：", "曲:", "曲：", "编曲:", "制作:", "lyricist:", "composer:"))

_ATTR_RE = re.compile(r'([\w:.\-]+)\s*=\s*"([^"]*)"')
_ATTR_RE_SQ = re.compile(r"([\w:.\-]+)\s*=\s*'([^']*)'")
_P_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL)
_SPAN_RE = re.compile(r"<span\b([^>]*)>(.*?)</span>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SONGWRITER_RE = re.compile(r"<songwriter\b[^>]*>(.*?)</songwriter>", re.DOTALL | re.IGNORECASE)


def _attrs(tag_str: str) -> Dict[str, str]:
    """把一段属性字符串解析成 dict（双引号优先、兼容单引号）。"""
    found = dict(_ATTR_RE.findall(tag_str or ""))
    for k, v in _ATTR_RE_SQ.findall(tag_str or ""):
        found.setdefault(k, v)
    return found


def _ms(attrs: Dict[str, str], key: str) -> Optional[int]:
    v = attrs.get(key)
    return parse_time_string_to_ms(v) if v else None


def _is_cjk(ch: str) -> bool:
    """判断字符是否属于中日韩表意/假名/全角区（不含谚文）。"""
    return is_hanzi_char(ch) or is_kana_char(ch) or is_fullwidth_char(ch)


def _gap_is_word_boundary(gap: str, prev_text: str, next_text: str) -> bool:
    """两个 <span> 之间的间隙是否代表真实词边界。"""
    if not gap:
        return False
    if "\n" in gap or "\r" in gap:
        return False
    if gap.strip() == "":
        if gap != " ":
            return False
        prev_clean = prev_text.rstrip()
        next_clean = next_text.lstrip()
        prev_ch = prev_clean[-1] if prev_clean else ""
        next_ch = next_clean[0] if next_clean else ""
        return not _is_cjk(prev_ch) and not _is_cjk(next_ch)
    return True


def _strip_span_blocks(body: str, role: str) -> str:
    """移除带指定 ttm:role 的整个 <span> 块。"""
    out = []
    i = 0
    n = len(body)
    while i < n:
        start = body.find("<span", i)
        if start < 0:
            out.append(body[i:])
            break
        out.append(body[i:start])
        depth = 0
        j = start
        end = -1
        while j < n:
            nxt_open = body.find("<span", j)
            nxt_close = body.find("</span>", j)
            if nxt_close < 0:
                break
            if 0 <= nxt_open < nxt_close:
                depth += 1
                j = nxt_open + 5
            else:
                depth -= 1
                j = nxt_close + 7
                if depth == 0:
                    end = j
                    break
        if end < 0:
            out.append(body[start:])
            break
        block = body[start:end]
        open_tag_end = block.find(">")
        open_tag = block[:open_tag_end] if open_tag_end > 0 else ""
        span_attrs = _attrs(open_tag)
        role_val = (span_attrs.get("ttm:role") or span_attrs.get("role") or "").lower()
        if role_val == (role or "").lower():
            pass  # 整块丢弃
        else:
            out.append(block)
        i = end
    return "".join(out)


def normalize_lrc_red_namespaces(ttml_text: str) -> str:
    """将 lrc.red 特有的命名空间规范化为 Apple 标准命名空间。
    
    如 xmlns:lrc="http://lrc.red/lyric-ttml-internal" -> xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    并将标签前缀 lrc: 替换为 itunes:。
    """
    if not ttml_text:
        return ""
    text = ttml_text.replace(
        "http://lrc.red/lyric-ttml-internal",
        "http://music.apple.com/lyric-ttml-internal"
    )
    # 替换命名空间前缀声明
    text = re.sub(r'\bxmlns:lrc\b', 'xmlns:itunes', text)
    # 替换属性前缀
    text = re.sub(r'\blrc:', 'itunes:', text)
    return text


def parse_lrc_red_ttml(
    ttml_text: str,
    metadata: Optional[Dict[str, Any]] = None,
    adjust: int = 0
) -> Dict[str, Any]:
    """解析 lrc.red TTML 文本，并结合外部元数据完成安全组装。

    :param ttml_text: 原始 TTML 文本内容
    :param metadata: 可选的结构化元数据（含 title, artist, album, duration, isrc 等）
    :param adjust: 全局时间轴偏移量 (ms)
    :return: 包含 {"tags": {...}, "lines": [...], "duration_ms": int, "songwriters": [...]} 的标准数据字典
    """
    tags: Dict[str, Any] = {}
    metadata = metadata or {}

    if not ttml_text or not isinstance(ttml_text, str):
        return {
            "tags": tags,
            "lines": [],
            "duration_ms": 0,
            "songwriters": [],
            "has_verbatim": False,
            "has_translation": False,
            "singing_lines": 0,
            "native_word_lines": 0,
            "word_coverage_ratio": 0.0,
            "invalid_word_lines": 0,
            "timeline_overlap_count": 0,
            "timestamp_repair_count": 0,
        }

    # 1. 抽取 songwriters 名单
    songwriters: List[str] = []
    for sm in _SONGWRITER_RE.finditer(ttml_text):
        raw_name = _TAG_RE.sub("", sm.group(1)).strip()
        name = html.unescape(raw_name).strip()
        if name and name not in songwriters:
            songwriters.append(name)
    if not songwriters and metadata.get("songwriters"):
        raw_sw = metadata["songwriters"]
        if isinstance(raw_sw, list):
            songwriters = [html.unescape(str(s)).strip() for s in raw_sw if str(s).strip()]
        elif isinstance(raw_sw, str):
            songwriters = [html.unescape(s).strip() for s in raw_sw.split("/") if s.strip()]

    # 2. 注入歌曲标签：优先使用 metadata 提供的结构化数据，若无则尝试从 amll:meta 解析
    for m in re.finditer(r"<amll:meta\b([^>]*)/?>", ttml_text):
        a = _attrs(m.group(1))
        k = a.get("key", "")
        v = html.unescape((a.get("value") or "").strip())
        if k == "musicName" and "ti" not in tags and v:
            tags["ti"] = v
        elif k == "artists" and "ar" not in tags and v:
            tags["ar"] = v
        elif k == "album" and "al" not in tags and v:
            tags["al"] = v

    if metadata.get("title") and not tags.get("ti"):
        tags["ti"] = html.unescape(str(metadata["title"])).strip()
    if metadata.get("artist") and not tags.get("ar"):
        tags["ar"] = html.unescape(str(metadata["artist"])).strip()
    if metadata.get("album") and not tags.get("al"):
        tags["al"] = html.unescape(str(metadata["album"])).strip()

    # 3. 提取音轨/歌词时长
    body_dur_ms = 0
    _bm = re.search(r"<body\b([^>]*)>", ttml_text)
    if _bm:
        _dur = _attrs(_bm.group(1)).get("dur")
        if _dur:
            _parsed = parse_time_string_to_ms(_dur)
            if _parsed and _parsed > 0:
                body_dur_ms = _parsed

    if not body_dur_ms and metadata.get("duration"):
        try:
            sec = float(metadata["duration"])
            body_dur_ms = int(sec * 1000)
        except Exception:
            pass

    # 4. 逐行与逐词解析
    lines: List[Dict[str, Any]] = []
    for pm in _P_RE.finditer(ttml_text):
        p_attrs = _attrs(pm.group(1))
        p_role = (p_attrs.get("ttm:role") or p_attrs.get("role") or "").lower()
        if p_role in ("x-bg", "x-roman"):
            continue

        p_begin = _ms(p_attrs, "begin")
        if p_begin is None:
            continue
        p_end = _ms(p_attrs, "end")
        if p_end is None:
            p_end = p_begin + 2500
        p_begin = apply_time_offset(p_begin, adjust)
        p_end = apply_time_offset(p_end, adjust)
        if p_end < p_begin:
            p_end = p_begin + 2500

        raw_inner = pm.group(2)
        if p_role == "x-translation":
            t_text = html.unescape(_TAG_RE.sub("", raw_inner)).strip()
            if t_text and lines and not lines[-1].get("translation") and abs(lines[-1]["start"] - p_begin) <= 2000:
                lines[-1]["translation"] = t_text
            continue

        # 移除背景和声与音译，避免污染主歌词
        body = _strip_span_blocks(raw_inner, "x-bg")
        body = _strip_span_blocks(body, "x-roman")

        # 提取译文
        translation = ""
        for sm in _SPAN_RE.finditer(body):
            s_attrs = _attrs(sm.group(1))
            span_role = (s_attrs.get("ttm:role") or s_attrs.get("role") or "").lower()
            if span_role == "x-translation":
                t_text = html.unescape(_TAG_RE.sub("", sm.group(2))).strip()
                if t_text:
                    translation = t_text

        # 剥离译文 span，避免译文干扰主歌词 span 间隙计算与文本回退
        body_main = _strip_span_blocks(body, "x-translation")
        word_spans = list(_SPAN_RE.finditer(body_main))

        has_invalid_span = False
        words: List[Dict[str, Any]] = []
        for idx, sm in enumerate(word_spans):
            s_attrs = _attrs(sm.group(1))
            s_begin = _ms(s_attrs, "begin")
            if s_begin is None:
                continue
            s_end = _ms(s_attrs, "end")
            s_raw_text = _TAG_RE.sub("", sm.group(2))
            s_text = html.unescape(s_raw_text)

            # 缺 end 时优先取下一个 span 的 begin，其次行尾 p_end
            if s_end is None:
                nxt_begin = None
                if idx + 1 < len(word_spans):
                    nxt_begin = _ms(_attrs(word_spans[idx + 1].group(1)), "begin")
                if nxt_begin is not None and nxt_begin >= s_begin:
                    s_end = nxt_begin
                elif p_end > s_begin:
                    s_end = p_end
                else:
                    s_end = s_begin + 500

            if s_end < s_begin:
                has_invalid_span = True
                continue

            # 检查与下一个 span 的空隙是否构成词边界空格
            nxt_pos = word_spans[idx + 1].start() if idx + 1 < len(word_spans) else len(body_main)
            gap = body_main[sm.end():nxt_pos]
            next_text = html.unescape(_TAG_RE.sub("", word_spans[idx + 1].group(2))) if idx + 1 < len(word_spans) else ""
            has_gap = _gap_is_word_boundary(gap, s_text, next_text)

            text = s_text
            if has_gap and not text.endswith(" "):
                text = text + " "

            w_dur = max(40, s_end - s_begin)
            words.append({
                "start": apply_time_offset(s_begin, adjust),
                "duration": w_dur,
                "end": apply_time_offset(s_end, adjust),
                "text": text,
                "join": "space" if has_gap else "none",
            })

        fallback_text = html.unescape(_TAG_RE.sub("", body_main)).strip()
        line_text = "".join(w["text"] for w in words).strip() if words else fallback_text

        if not line_text and not words:
            if translation and lines and not lines[-1].get("translation") and abs(lines[-1]["start"] - p_begin) <= 2000:
                lines[-1]["translation"] = translation
            continue

        line_data: Dict[str, Any] = {
            "start": p_begin,
            "end": p_end,
            "text": line_text,
            "has_invalid_span": has_invalid_span,
        }
        if words:
            line_data["words"] = words
        if translation:
            line_data["translation"] = translation

        lines.append(line_data)

    # 按照实际开始时间排序（P1-5: 兼容多 <div> 交错时序）
    lines.sort(key=lambda l: l.get("start", 0))

    # 5. 覆盖度与质量审计指标统计（P1-6）
    singing_lines = 0
    native_word_lines = 0
    invalid_word_lines = 0
    timeline_overlap_count = 0
    timestamp_repair_count = 0

    prev_line_end = -1
    for line in lines:
        txt = (line.get("text") or "").strip()
        if not txt or is_credit_line(txt):
            continue
        singing_lines += 1
        l_words = line.get("words") or []
        if l_words:
            if any(not w.get("synthetic") for w in l_words):
                native_word_lines += 1
            prev_w_end = -1
            has_inv = False
            for w in l_words:
                w_st = w.get("start", 0)
                w_et = w.get("end", 0)
                if w_et < w_st:
                    has_inv = True
                if prev_w_end > 0 and w_st < prev_w_end - 50:
                    timeline_overlap_count += 1
                prev_w_end = w_et
            if has_inv or line.get("has_invalid_span"):
                invalid_word_lines += 1
        elif line.get("has_invalid_span"):
            invalid_word_lines += 1
        l_st = line.get("start", 0)
        l_et = line.get("end", 0)
        if prev_line_end > 0 and l_st < prev_line_end - 100:
            timeline_overlap_count += 1
        prev_line_end = l_et

    word_coverage_ratio = (native_word_lines / singing_lines) if singing_lines > 0 else 0.0
    has_verbatim = bool(native_word_lines > 0)
    has_translation = any(bool(l.get("translation")) for l in lines)

    return {
        "tags": tags,
        "lines": lines,
        "duration_ms": body_dur_ms,
        "songwriters": songwriters,
        "has_verbatim": has_verbatim,
        "has_translation": has_translation,
        "singing_lines": singing_lines,
        "native_word_lines": native_word_lines,
        "word_coverage_ratio": word_coverage_ratio,
        "invalid_word_lines": invalid_word_lines,
        "timeline_overlap_count": timeline_overlap_count,
        "timestamp_repair_count": timestamp_repair_count,
    }
