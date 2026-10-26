# -*- coding: utf-8 -*-
"""Apple Music TTML 解析：保留词边界 join、x-translation、x-bg 不进主唱。"""

import re
from typing import Any, Dict, List

from ..utils.time_utils import parse_time_string_to_ms, apply_time_offset

_ATTR_RE = re.compile(r'([\w:.\-]+)\s*=\s*"([^"]*)"')
# P1-2: 属性也可能是单引号，旧版只认双引号会让整行静默丢弃
_ATTR_RE_SQ = re.compile(r"([\w:.\-]+)\s*=\s*'([^']*)'")
_P_RE = re.compile(r"<p\b([^>]*)>(.*?)</p>", re.DOTALL)
_SPAN_RE = re.compile(r"<span\b([^>]*)>(.*?)</span>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _is_cjk(ch: str) -> bool:
    """判断字符是否属于中日韩表意/假名/全角区（这些语言词间不加空格）。"""
    if not ch or len(ch) != 1:
        return False
    o = ord(ch)
    return (
        0x3040 <= o <= 0x30FF        # 日文假名
        or 0x3400 <= o <= 0x4DBF     # CJK 扩展 A
        or 0x4E00 <= o <= 0x9FFF     # CJK 基本区
        or 0xF900 <= o <= 0xFAFF     # CJK 兼容表意
        or 0x20000 <= o <= 0x323AF   # CJK 扩展 B~I
        or 0x2F800 <= o <= 0x2FA1F   # CJK 兼容表意补充
        or 0xFF00 <= o <= 0xFFEF     # 全角标点/字母
    )


def _gap_is_word_boundary(gap: str, prev_text: str, next_text: str) -> bool:
    """两个 <span> 之间的间隙是否代表真实词边界。

    P1-2: 旧版 `bool(re.search(r"\\s", gap))` 把任何空白都当词边界，
    「一拍一行」的美化 TTML（span 之间是换行+缩进）会给中文插空格，变成「我 爱 你」。
    规则：
      - 含换行/回车 -> 属于排版缩进，不是词边界
      - 只有一个 ASCII 空格且两侧都非 CJK -> 是词边界
      - 其余空白（多空格/制表符）-> 排版，不算
    """
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


def _attrs(tag_str: str) -> Dict[str, str]:
    """把一段属性字符串解析成 dict（与属性顺序无关，双引号优先、兼容单引号）。"""
    found = dict(_ATTR_RE.findall(tag_str or ""))
    for k, v in _ATTR_RE_SQ.findall(tag_str or ""):
        found.setdefault(k, v)
    return found


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
        span_attrs = _attrs(open_tag)
        role_val = (span_attrs.get("ttm:role") or span_attrs.get("role") or "").lower()
        if role_val == (role or "").lower():
            pass  # 整块丢弃（双引号 / 单引号属性都认）
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

    # P1-A: <body dur> 是离线场景下唯一的整曲时长来源。旧版完全不读，
    # 导致「只给 TTML、没有音频」时 preview/overrun 门禁彻底失效，
    # 尾部被截断的歌词会静默通过。这里只做参考值，不参与时间轴计算。
    body_dur_ms = 0
    _bm = re.search(r"<body\b([^>]*)>", ttml_text)
    if _bm:
        _dur = _attrs(_bm.group(1)).get("dur")
        if _dur:
            _parsed = parse_time_string_to_ms(_dur)
            if _parsed and _parsed > 0:
                body_dur_ms = _parsed

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
        body = _strip_span_blocks(body, "x-roman")
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
            if w_end is not None:
                w_end = apply_time_offset(w_end, adjust)
            else:
                # P1-4: 缺 end 时优先取下一个 span 的 begin，其次行尾 p_end。
                # 旧版兜底 w_begin + 500 会把 2s 长音压成 0.5s、末端提前 1.5s。
                nxt_begin = None
                if idx + 1 < len(spans):
                    nxt_begin = _ms(_attrs(spans[idx + 1].group(1)), "begin")
                if nxt_begin is not None:
                    w_end = apply_time_offset(nxt_begin, adjust)
                elif p_end > w_begin:
                    w_end = p_end
                else:
                    w_end = w_begin + 500
                if w_end <= w_begin:
                    w_end = w_begin + 500

            # 源里两个 span 之间的间隙可能代表词边界（P1-2: 需排除排版换行/缩进）
            nxt = spans[idx + 1].start() if idx + 1 < len(spans) else len(body)
            gap = body[sm.end():nxt]
            next_text = _TAG_RE.sub("", spans[idx + 1].group(2)) if idx + 1 < len(spans) else ""
            has_gap = _gap_is_word_boundary(gap, inner_text, next_text)
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

        # P1-17: 纯译文段落幽灵行修复：若段落没有主词（words 为空），
        # 且剩下的文本全是译文（剥离 x-translation 后无内容），
        # 则 line_text 置空，不得拿译文作为原文文本合成假字。
        body_no_trans = _strip_span_blocks(body, "x-translation")
        fallback_text = _TAG_RE.sub("", body_no_trans).strip()
        line_text = "".join(w["text"] for w in words).strip() if words else fallback_text

        p_role = (p_attrs.get("ttm:role") or p_attrs.get("role") or "").lower()
        if p_role == "x-translation":
            if not translation:
                translation = _TAG_RE.sub("", body).strip()
            line_text = ""
            words = []

        if not words and line_text:
            words.append({
                "start": p_begin,
                "duration": max(80, p_end - p_begin),
                "end": p_end,
                "text": line_text,
                "join": "none",
                "synthetic": True,
            })

        # P1-4 & P1-17: 纯 x-bg 段或纯 x-translation 段被抽空后会剩下 text="" 与 words=[] 的幽灵行，
        # 它会占 len(lines) 计数（影响 30% 译文阈值与纯音乐占位判断），
        # 还可能成为 lines[-1] 影响 Outro 判定。若存在 translation 则尝试附着到前一行，随后跳过。
        if not line_text and not words:
            if translation and lines and not lines[-1].get("translation") and abs(lines[-1]["start"] - p_begin) <= 2000:
                lines[-1]["translation"] = translation
            continue

        lines.append({
            "start": p_begin,
            "duration": p_end - p_begin,
            "end": p_end,
            "text": line_text,
            "translation": translation,
            "words": words,
        })

    # P0-3: 按 begin 稳定排序。TTML 若按 ttm:agent 分 <div>（对唱/多声部），
    # 文档顺序不等于时间顺序。不排序时 validator 会检测到倒序并做
    # start = last + 100 的整行平移，把两个音节压到同一时刻。
    # 只排序，不按 agent 拆轨——本 skill 交付单轨 Enhanced LRC，时间交错即可。
    lines.sort(key=lambda l: l["start"])
    result: Dict[str, Any] = {
        "tags": tags,
        "lines": lines,
        # v1.2.8：TTML 的时间戳本来就是毫秒精度（00:46.425），固定按 3 位输出
        "time_precision": 3,
    }
    if body_dur_ms:
        result["duration_ms"] = body_dur_ms
    return result
