# -*- coding: utf-8 -*-
"""Apple Music TTML 解析：保留词边界 join、x-translation、x-bg 不进主唱。"""

import re
from typing import Any, Dict, List

from ..utils.time_utils import parse_time_string_to_ms, apply_time_offset

_ATTR_RE = re.compile(r'([\w:.\-]+)\s*=\s*"([^"]*)"')
_P_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL)
_SPAN_RE = re.compile(r"<span\b([^>]*)>(.*?)</span>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _attrs(tag_str: str) -> Dict[str, str]:
    """把一段属性字符串解析成 dict（与属性顺序无关）。"""
    return dict(_ATTR_RE.findall(tag_str or ""))


def _ms(attrs: Dict[str, str], key: str) -> Any:
    v = attrs.get(key)
    return parse_time_string_to_ms(v) if v else None


def _strip_span_blocks(body: str, role: str) -> str:
    """移除带指定 ttm:role 的整个 <span> 块（按嵌套深度配对），避免污染主词序列。"""
    out = []
    i = 0
    n = len(body)
    while i < n:
        start = body.find("<span", i)
        if start < 0:
            out.append(body[i:])
            break
        out.append(body[i:start])
        # 找这个 span 的结束位置（考虑嵌套）
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
        if f'ttm:role="{role}"' in open_tag:
            pass  # 整块丢弃
        else:
            out.append(block)
        i = end
    return "".join(out)


def parse_ttml(ttml_text: str, adjust: int = 0) -> Dict[str, Any]:
    """解析 TTML 文本，返回 {"tags": {...}, "lines": [...]}。"""
    tags: Dict[str, Any] = {}

    # 元数据：同一个 key 可能有多语言重复值。musicName/album 取第一个非空，
    # artists 用 "/" 合并去重（旧版"后写覆盖"会取到最后一个，标题取繁体、艺人丢掉一个）。
    meta: Dict[str, List[str]] = {}
    for m in re.finditer(r"<amll:meta\b([^>]*)/?>", ttml_text):
        a = _attrs(m.group(1))
        key = a.get("key", "")
        val = (a.get("value") or "").strip()
        if key and val and val not in meta.setdefault(key, []):
            meta[key].append(val)
    if meta.get("musicName"):
        tags["ti"] = meta["musicName"][0]
    if meta.get("artists"):
        tags["ar"] = "/".join(meta["artists"])
    if meta.get("album"):
        tags["al"] = meta["album"][0]

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

        body = _strip_span_blocks(pm.group(2), "x-bg")
        spans = list(_SPAN_RE.finditer(body))

        words: List[Dict[str, Any]] = []
        translation = ""
        for idx, sm in enumerate(spans):
            s_attrs = _attrs(sm.group(1))
            role = (s_attrs.get("ttm:role") or s_attrs.get("role") or "").lower()
            inner_text = _TAG_RE.sub("", sm.group(2))

            if role == "x-translation":
                translation = (translation + inner_text).strip()
                continue

            w_begin = _ms(s_attrs, "begin")
            if w_begin is None:
                continue
            w_end = _ms(s_attrs, "end")
            w_begin = apply_time_offset(w_begin, adjust)
            w_end = apply_time_offset(w_end, adjust) if w_end is not None else w_begin + 500

            # 源里两个 span 之间的空白就是真实的词边界
            nxt = spans[idx + 1].start() if idx + 1 < len(spans) else len(body)
            gap = body[sm.end():nxt]
            has_gap = bool(re.search(r"\s", gap))
            text = inner_text
            if has_gap and not text.endswith(" "):
                text = text + " "

            w_dur = max(40, w_end - w_begin)
            words.append({
                "start": w_begin,
                "duration": w_dur,
                "end": w_begin + w_dur,
                "text": text,
                "join": "space" if has_gap else "none",
            })

        line_text = "".join(w["text"] for w in words).strip() if words else _TAG_RE.sub("", body).strip()
        if not words and line_text:
            words.append({
                "start": p_begin,
                "duration": max(80, p_end - p_begin),
                "end": p_end,
                "text": line_text,
                "join": "none",
            })

        lines.append({
            "start": p_begin,
            "duration": p_end - p_begin,
            "end": p_end,
            "text": line_text,
            "translation": translation,
            "words": words,
        })

    return {"tags": tags, "lines": lines}
