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

import re
from typing import Any, Dict, List, Optional

from ..utils.text_utils import is_fullwidth_char, is_hanzi_char, is_kana_char
from ..utils.time_utils import parse_time_string_to_ms, apply_time_offset

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
        prev_ch = prev_text[-1] if prev_text else ""
        next_ch = next_text[0] if next_text else ""
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

    # 1. 抽取 songwriters 名单
    songwriters: List[str] = []
    for sm in _SONGWRITER_RE.finditer(ttml_text):
        name = _TAG_RE.sub("", sm.group(1)).strip()
        if name and name not in songwriters:
            songwriters.append(name)
    if not songwriters and metadata.get("songwriters"):
        raw_sw = metadata["songwriters"]
        if isinstance(raw_sw, list):
            songwriters = [str(s).strip() for s in raw_sw if str(s).strip()]
        elif isinstance(raw_sw, str):
            songwriters = [s.strip() for s in raw_sw.split("/") if s.strip()]

    # 2. 注入歌曲标签：优先使用 metadata 提供的结构化数据，若无则尝试从 amll:meta 解析
    for m in re.finditer(r"<amll:meta\b([^>]*)/?>", ttml_text):
        a = _attrs(m.group(1))
        k = a.get("key", "")
        v = (a.get("value") or "").strip()
        if k == "musicName" and "ti" not in tags:
            tags["ti"] = v
        elif k == "artists" and "ar" not in tags:
            tags["ar"] = v
        elif k == "album" and "al" not in tags:
            tags["al"] = v

    if metadata.get("title") and not tags.get("ti"):
        tags["ti"] = str(metadata["title"]).strip()
    if metadata.get("artist") and not tags.get("ar"):
        tags["ar"] = str(metadata["artist"]).strip()
    if metadata.get("album") and not tags.get("al"):
        tags["al"] = str(metadata["album"]).strip()

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
        # 移除背景和声与音译，避免污染主歌词
        body = _strip_span_blocks(raw_inner, "x-bg")
        body = _strip_span_blocks(body, "x-roman")
        spans = list(_SPAN_RE.finditer(body))

        words: List[Dict[str, Any]] = []
        translation = ""
        for idx, sm in enumerate(spans):
            s_attrs = _attrs(sm.group(1))
            span_role = (s_attrs.get("ttm:role") or s_attrs.get("role") or "").lower()
            if span_role == "x-translation":
                t_text = _TAG_RE.sub("", sm.group(2)).strip()
                if t_text:
                    translation = t_text
                continue

            s_begin = _ms(s_attrs, "begin")
            s_end = _ms(s_attrs, "end")
            s_text = sm.group(2)
            s_text = _TAG_RE.sub("", s_text)  # 去除 span 内部可能嵌套的格式标签

            if s_begin is not None and s_end is not None and s_end >= s_begin:
                # 检查与前一个 span 的空隙是否构成词边界空格
                if words:
                    prev_end_pos = spans[idx - 1].end()
                    curr_start_pos = sm.start()
                    gap = body[prev_end_pos:curr_start_pos]
                    if _gap_is_word_boundary(gap, words[-1]["text"], s_text):
                        words[-1]["text"] = words[-1]["text"] + " "

                words.append({
                    "start": apply_time_offset(s_begin, adjust),
                    "end": apply_time_offset(s_end, adjust),
                    "text": s_text,
                })

        line_text = "".join(w["text"] for w in words).strip() if words else _TAG_RE.sub("", body).strip()

        line_data: Dict[str, Any] = {
            "start": p_begin,
            "end": p_end,
            "text": line_text,
        }
        if words:
            line_data["words"] = words
        if translation:
            line_data["translation"] = translation

        lines.append(line_data)

    has_verbatim = any(bool(l.get("words")) for l in lines)
    has_translation = any(bool(l.get("translation")) for l in lines)

    return {
        "tags": tags,
        "lines": lines,
        "duration_ms": body_dur_ms,
        "songwriters": songwriters,
        "has_verbatim": has_verbatim,
        "has_translation": has_translation,
    }
