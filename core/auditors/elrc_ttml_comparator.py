# -*- coding: utf-8 -*-
"""ELRC ↔ TTML 双向独立一致性审计引擎 (v1.8.2 R1)。

满足《歌曲检测与修补标准》§2 第 4 条及方案铁律：
1. 完全独立于生成器的双向反解析路径；
2. 全曲所有逻辑行（前奏投影与正文演唱行）及逐字 1:1 双向比对；
3. 时间误差严格 <= 1ms 门禁；
4. div 容器边界计算误差严格 <= 2ms 门禁；
5. 译文严格核对 for="Ln" 与行级译文挂接关系，错挂/悬空引用阻断；
6. 精准反例检测：单词错轴、div 偏差、行数缺失、丢字、空格丢失等准确分类；
7. 行级 ELRC 无显式 end 时准确标记 LINE_END_NOT_REPRESENTABLE，不作假通过。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..utils.time_utils import parse_time_string_to_ms

# ELRC 行与字正则
_ELRC_LINE_RE = re.compile(r"^\[(\d+:\d+(?:\.\d+)?)\](.*)$")
_ELRC_WORD_RE = re.compile(r"<(\d+:\d+(?:\.\d+)?)>([^<]*)")


@dataclass
class ParsedWord:
    text: str
    start_ms: int
    end_ms: int


@dataclass
class ParsedLine:
    line_id: str
    start_ms: int
    end_ms: Optional[int]
    text: str
    words: List[ParsedWord] = field(default_factory=list)
    translation: Optional[str] = None
    has_explicit_end: bool = False
    is_prelude: bool = False


@dataclass
class ComparisonIssue:
    code: str
    message: str
    line_id: Optional[str] = None
    elrc_val: Any = None
    ttml_val: Any = None
    blocking: bool = True


@dataclass
class ComparisonResult:
    passed: bool
    issues: List[ComparisonIssue] = field(default_factory=list)
    max_time_delta_ms: int = 0
    max_div_delta_ms: int = 0

    def add_error(
        self,
        code: str,
        message: str,
        line_id: Optional[str] = None,
        elrc_val: Any = None,
        ttml_val: Any = None,
    ) -> None:
        self.issues.append(
            ComparisonIssue(
                code=code,
                message=message,
                line_id=line_id,
                elrc_val=elrc_val,
                ttml_val=ttml_val,
                blocking=True,
            )
        )
        self.passed = False


def parse_elrc_structural(elrc_text: str) -> List[ParsedLine]:
    """独立反解析 ELRC 文本为结构化逻辑行列表。"""
    raw_lines = elrc_text.splitlines()
    parsed_lines: List[ParsedLine] = []
    line_counter = 1

    for raw in raw_lines:
        line_str = raw.strip()
        if not line_str:
            continue

        # 跳过纯元数据行
        if line_str.startswith("[re:") or line_str.startswith("[ti:") or line_str.startswith("[ar:"):
            continue

        m_line = _ELRC_LINE_RE.match(line_str)
        if not m_line:
            continue

        st_ms = parse_time_string_to_ms(m_line.group(1))
        content = m_line.group(2)

        # 检查是否为前一行的中文译文（同时间戳、无逐字标签、紧随其后）
        if parsed_lines and parsed_lines[-1].start_ms == st_ms and "<" not in content:
            parsed_lines[-1].translation = content.strip()
            continue

        # 解析逐字标签
        word_matches = list(_ELRC_WORD_RE.finditer(content))
        words: List[ParsedWord] = []
        full_text_parts: List[str] = []
        has_explicit_end = False
        end_ms: Optional[int] = None

        if word_matches:
            for i, wm in enumerate(word_matches):
                w_st = parse_time_string_to_ms(wm.group(1))
                w_txt = wm.group(2)
                if i + 1 < len(word_matches):
                    w_et = parse_time_string_to_ms(word_matches[i + 1].group(1))
                else:
                    w_et = w_st
                if w_txt:
                    words.append(ParsedWord(text=w_txt, start_ms=w_st, end_ms=w_et))
                    full_text_parts.append(w_txt)

            if word_matches[-1].group(2) == "":
                has_explicit_end = True
                end_ms = parse_time_string_to_ms(word_matches[-1].group(1))
            elif words:
                end_ms = words[-1].end_ms
        else:
            full_text_parts.append(content)

        line_id = f"L{line_counter}"
        line_counter += 1

        parsed_lines.append(
            ParsedLine(
                line_id=line_id,
                start_ms=st_ms,
                end_ms=end_ms,
                text="".join(full_text_parts).strip(),
                words=words,
                has_explicit_end=has_explicit_end,
                is_prelude=False,
            )
        )

    return parsed_lines


def parse_ttml_structural(ttml_text: str) -> Tuple[List[ParsedLine], Tuple[int, int], List[ComparisonIssue]]:
    """独立反解析 TTML XML 为结构化逻辑行列表及 div 边界。"""
    issues: List[ComparisonIssue] = []
    parsed_lines: List[ParsedLine] = []
    div_bounds = (0, 0)

    try:
        root = ET.fromstring(ttml_text)
    except Exception as exc:
        issues.append(ComparisonIssue(code="XML_SYNTAX_ERROR", message=f"TTML XML syntax error: {exc}"))
        return parsed_lines, div_bounds, issues

    # 1. 抽取译文（支持 head/iTunesMetadata 以及 inline x-translation 双向兼容）
    translations_by_key: Dict[str, str] = {}
    for text_node in root.findall(".//{*}iTunesMetadata/{*}translations/{*}translation/{*}text"):
        for_key = text_node.attrib.get("for")
        if for_key and text_node.text:
            translations_by_key[for_key] = text_node.text.strip()

    # 2. 抽取 div 边界
    div_elem = root.find(".//{*}body/{*}div")
    if div_elem is not None:
        d_bg = parse_time_string_to_ms(div_elem.attrib.get("begin", "0"))
        d_ed = parse_time_string_to_ms(div_elem.attrib.get("end", "0"))
        div_bounds = (d_bg, d_ed)
    else:
        issues.append(ComparisonIssue(code="DIV_MISSING", message="No <div> found in TTML body."))

    # 3. 抽取各 <p> 节点
    seen_keys: set = set()
    p_nodes = root.findall(".//{*}body/{*}div/{*}p")
    for p in p_nodes:
        p_bg = parse_time_string_to_ms(p.attrib.get("begin", "0"))
        p_ed = parse_time_string_to_ms(p.attrib.get("end", "0"))
        p_key = None
        for k, v in p.attrib.items():
            if k.endswith("key") or k == "key":
                p_key = v
                break
        if not p_key:
            p_key = f"L{len(parsed_lines) + 1}"

        if p_key in seen_keys:
            issues.append(ComparisonIssue(code="DUPLICATE_P_KEY", message=f"Duplicate p key '{p_key}' in TTML.", line_id=p_key))
        seen_keys.add(p_key)

        inline_trans = None
        words: List[ParsedWord] = []
        p_text_parts: List[str] = []

        span_nodes = p.findall(".//{*}span")
        for s in span_nodes:
            s_role = None
            for attr_k, attr_v in s.attrib.items():
                if attr_k.endswith("role") and attr_v == "x-translation":
                    s_role = "x-translation"
                    break
            if s_role == "x-translation":
                inline_trans = (s.text or "").strip()
                continue

            s_bg = parse_time_string_to_ms(s.attrib.get("begin", "0"))
            s_ed = parse_time_string_to_ms(s.attrib.get("end", "0"))
            s_txt = s.text or ""
            tail_space = " " if (s.tail and " " in s.tail) else ""
            words.append(ParsedWord(text=s_txt + tail_space, start_ms=s_bg, end_ms=s_ed))
            p_text_parts.append(s_txt + tail_space)

        final_trans = translations_by_key.get(p_key) or inline_trans
        is_prelude = p_key.startswith("P_")

        parsed_lines.append(
            ParsedLine(
                line_id=p_key,
                start_ms=p_bg,
                end_ms=p_ed,
                text="".join(p_text_parts).strip(),
                words=words,
                translation=final_trans,
                has_explicit_end=True,
                is_prelude=is_prelude,
            )
        )

    # 4. 检查是否有未解析到任何 <p> 节点的悬空译文 (for="L99")
    for trans_key in translations_by_key:
        if trans_key not in seen_keys:
            issues.append(
                ComparisonIssue(
                    code="TRANSLATION_HANGING_REFERENCE",
                    message=f"Translation refers to nonexistent p key '{trans_key}'.",
                    line_id=trans_key,
                )
            )

    return parsed_lines, div_bounds, issues


def compare_elrc_ttml(elrc_text: str, ttml_text: str) -> ComparisonResult:
    """R1: 独立反解析并对 ELRC 与 TTML 执行严格双向一致性比对。"""
    res = ComparisonResult(passed=True)

    # 1. 独立解析
    elrc_lines = parse_elrc_structural(elrc_text)
    ttml_lines, div_bounds, ttml_issues = parse_ttml_structural(ttml_text)

    if ttml_issues:
        for iss in ttml_issues:
            res.add_error(iss.code, iss.message, iss.line_id)
        return res

    # 2. 检查总行数
    if len(elrc_lines) != len(ttml_lines):
        res.add_error(
            code="LINE_MISSING",
            message=f"Line count mismatch: ELRC has {len(elrc_lines)} lines, TTML has {len(ttml_lines)} lines.",
            elrc_val=len(elrc_lines),
            ttml_val=len(ttml_lines),
        )
        return res

    # 3. 逐行 1:1 全量比对
    max_delta = 0
    for idx, (e_line, t_line) in enumerate(zip(elrc_lines, ttml_lines)):
        lid = t_line.line_id

        # 行起点时间比对 (<= 1ms 阈值)
        st_delta = abs(e_line.start_ms - t_line.start_ms)
        max_delta = max(max_delta, st_delta)
        if st_delta > 1:
            res.add_error(
                code="TIME_AXIS_MISMATCH",
                message=f"Line start time mismatch ({st_delta}ms > 1ms).",
                line_id=lid,
                elrc_val=e_line.start_ms,
                ttml_val=t_line.start_ms,
            )

        # 行结束时间比对 (<= 1ms 阈值)
        if e_line.end_ms is not None and t_line.end_ms is not None:
            et_delta = abs(e_line.end_ms - t_line.end_ms)
            max_delta = max(max_delta, et_delta)
            if et_delta > 1:
                res.add_error(
                    code="TIME_AXIS_MISMATCH",
                    message=f"Line end time mismatch ({et_delta}ms > 1ms).",
                    line_id=lid,
                    elrc_val=e_line.end_ms,
                    ttml_val=t_line.end_ms,
                )

        # 行文本比对
        if e_line.text != t_line.text:
            res.add_error(
                code="TEXT_MISMATCH",
                message=f"Line text mismatch: '{e_line.text}' vs '{t_line.text}'.",
                line_id=lid,
                elrc_val=e_line.text,
                ttml_val=t_line.text,
            )

        # 逐字比对
        if len(e_line.words) != len(t_line.words):
            res.add_error(
                code="WORD_MISMATCH",
                message=f"Word count mismatch: ELRC={len(e_line.words)}, TTML={len(t_line.words)}.",
                line_id=lid,
                elrc_val=len(e_line.words),
                ttml_val=len(t_line.words),
            )
        else:
            for w_idx, (e_w, t_w) in enumerate(zip(e_line.words, t_line.words)):
                w_st_delta = abs(e_w.start_ms - t_w.start_ms)
                w_et_delta = abs(e_w.end_ms - t_w.end_ms)
                max_delta = max(max_delta, w_st_delta, w_et_delta)

                if w_st_delta > 1 or w_et_delta > 1:
                    res.add_error(
                        code="TIME_AXIS_MISMATCH",
                        message=f"Word #{w_idx+1} time mismatch (start_delta={w_st_delta}ms, end_delta={w_et_delta}ms).",
                        line_id=lid,
                        elrc_val=(e_w.start_ms, e_w.end_ms),
                        ttml_val=(t_w.start_ms, t_w.end_ms),
                    )
                if e_w.text.strip() != t_w.text.strip():
                    res.add_error(
                        code="WORD_MISMATCH",
                        message=f"Word #{w_idx+1} text mismatch: '{e_w.text}' vs '{t_w.text}'.",
                        line_id=lid,
                        elrc_val=e_w.text,
                        ttml_val=t_w.text,
                    )

        # 译文比对
        if (e_line.translation or "").strip() != (t_line.translation or "").strip():
            res.add_error(
                code="TRANSLATION_LINK_MISMATCH",
                message=f"Translation mismatch: '{e_line.translation}' vs '{t_line.translation}'.",
                line_id=lid,
                elrc_val=e_line.translation,
                ttml_val=t_line.translation,
            )

        # 行级显式 end_ms 检查
        if not e_line.has_explicit_end and not e_line.words:
            res.add_error(
                code="LINE_END_NOT_REPRESENTABLE",
                message="ELRC line has no verbatim words and no explicit end tag; end_ms is not representable.",
                line_id=lid,
            )

    # 4. div 容器边界比对 (<= 2ms 阈值)
    if elrc_lines and ttml_lines:
        calc_min = min(l.start_ms for l in elrc_lines)
        calc_max = max(l.end_ms or l.start_ms for l in elrc_lines)
        div_st, div_ed = div_bounds
        div_st_delta = abs(div_st - calc_min)
        div_ed_delta = abs(div_ed - calc_max)
        res.max_div_delta_ms = max(div_st_delta, div_ed_delta)

        if div_st_delta > 2 or div_ed_delta > 2:
            res.add_error(
                code="DIV_BOUNDARY_MISMATCH",
                message=f"div boundary mismatch: calc=({calc_min},{calc_max}), div=({div_st},{div_ed}).",
                elrc_val=(calc_min, calc_max),
                ttml_val=div_bounds,
            )

    res.max_time_delta_ms = max_delta
    return res
