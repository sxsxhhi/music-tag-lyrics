# -*- coding: utf-8 -*-
"""QQ 音乐 QRC 逐字歌词解析（v1.7.0）。

与 YRC / KRC 的三处关键差异
--------------------------
1. **字块是「文本在前、参数在后」**：`晴(0,160)天(160,160)`。
   YRC 是 `(起,长,标志)字`，KRC 是 `<偏移,时长,0>字`，方向相反。
   切片时要取「上一个参数结束 → 当前参数开始」之间的原文。
2. **字时间同样是绝对毫秒**（与 YRC 同语义），不需要叠加行首。
3. **解密后是 XML 包装**：`<QrcInfos>…<Lyric_1 LyricContent="[ti:…]&#10;[0,2250]晴(0,160)…"/>`。
   歌词正文在 `LyricContent` 属性值里，取出来要 `html.unescape`（实测无转义，
   但接口可能返回 `&#10;` / `&quot;`，必须兼容）。

数字括号误判的防御
------------------
正文里可能出现 `(FR)`、`(2024,1)` 这类字面。`(FR)` 不匹配 `(数字,数字)` 不受影响；
`(2024,1)` 会命中参数正则，因此用「行范围 + 时长上限」双重过滤，
不满足的参数**不作为块边界**，原样留在文本里。
"""
import logging
import re
from typing import Any, Dict, List, Tuple

from ..utils.time_utils import apply_time_offset
from ._common import normalize_word_blocks, parse_offset_tag
from .yrc_parser import OVERRUN_TOL_MS, trim_tail_staff

logger = logging.getLogger(__name__)

# 行头：[绝对起, 行时长] 后跟正文
QRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
# 字参数：(绝对起, 时长)，两个数字
QRC_WORD_PARAM_RE = re.compile(r"\((\d+),(\d+)\)")
# 元数据标签 [ti:…] / [ar:…] / [offset:…]
TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")
# XML 包装里的歌词正文（贪婪到最后一个 "/> 之前，避免歌词内含引号时提前截断）
LYRIC_CONTENT_RE = re.compile(r'LyricContent="(.*)"\s*/>', re.S)

# 参数合法性：单字时长上限 60s；字起点相对行范围的容忍窗
WORD_DUR_MAX_MS = 60000
LINE_RANGE_TOL_MS = 2000

Block = Tuple[int, int, str]


def unwrap_qrc(text: str) -> str:
    """若是 QrcInfos XML 包装，取出 LyricContent 并解转义；否则原样返回。"""
    if not text:
        return ""
    if "<QrcInfos" not in text and 'LyricContent="' not in text:
        return text
    m = LYRIC_CONTENT_RE.search(text)
    if not m:
        # 个别版本的结尾不是 "/>"，退化为「取 LyricContent=" 之后的全部」
        idx = text.find('LyricContent="')
        if idx < 0:
            return text
        body = text[idx + len('LyricContent="'):]
        body = body.rsplit('"', 1)[0]
    else:
        body = m.group(1)
    import html
    return html.unescape(body)


def _split_blocks(body: str, line_start: int, line_end: int) -> List[Block]:
    """位置切片：每一个 `(起,长)` 之前、上一个参数之后的那段原文才是该字文本。

    非法参数（时长 > 60s、起点离行范围超过 2s）不当块边界，原样留在文本里。
    """
    text = body or ""
    params: List[Tuple[Any, int, int]] = []
    for m in QRC_WORD_PARAM_RE.finditer(text):
        start = int(m.group(1))
        dur = int(m.group(2))
        if dur > WORD_DUR_MAX_MS:
            continue
        if start < line_start - LINE_RANGE_TOL_MS or start > line_end + LINE_RANGE_TOL_MS:
            continue
        params.append((m, start, dur))

    blocks: List[Block] = []
    prev_end = 0
    for m, start, dur in params:
        blocks.append((start, dur, text[prev_end:m.start()]))
        prev_end = m.end()
    if blocks and prev_end < len(text):
        # 尾部残留（含被判定为非法的参数）并入最后一个字，宁可留着也不丢词
        start, dur, txt = blocks[-1]
        blocks[-1] = (start, dur, txt + text[prev_end:])
    return blocks


def parse_qrc(qrc_text: str, adjust: int = 0, duration_ms: int = 0) -> Dict[str, Any]:
    """解析 QRC 文本为统一歌词结构。

    返回 ``{"tags", "lines", "time_precision": 3}``：

    - 每行 ``start`` 取**首字起点**，``end`` 取末字终点，保证
      ``line.start == words[0].start`` 与 ``line.end == words[-1].end`` 两条恒等式成立。
    - ``text`` 是 words 的原样拼接，供 converter 的逐字字面一致性校验。
    - **不加** ``duration_ms``（会改变 compute_duration_axis 语义）、
      **不加** ``has_embedded_translation`` / ``tlyric``（QRC 本通道不消费译文）。
    - 尾部名单截断失败时返回空 ``lines``，由 fetcher 降级到下一个源。
    """
    body_text = unwrap_qrc(qrc_text)
    tags: Dict[str, str] = {}
    rows: List[Tuple[int, int, str]] = []

    for line in (body_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = QRC_LINE_RE.match(s)
        if not m:
            tm = TAG_RE.match(s)
            if tm:
                tags[tm.group(1).lower()] = tm.group(2).strip()
            continue
        rows.append((int(m.group(1)), int(m.group(2)), m.group(3)))

    total_offset = parse_offset_tag(tags) + adjust

    lines: List[Dict[str, Any]] = []
    for line_start, line_dur, body in rows:
        line_end = line_start + line_dur
        words = normalize_word_blocks(_split_blocks(body, line_start, line_end))
        if not words:
            # 整行没有任何有效字块：用行头兜一个覆盖整行的字，避免丢词
            text = body.strip()
            if not text:
                continue                     # 空行丢弃，不产幽灵行
            words = [{
                "start": line_start,
                "duration": line_dur,
                "end": line_end,
                "text": text,
            }]
        if total_offset:
            for w in words:
                w["start"] = apply_time_offset(w["start"], total_offset)
                w["end"] = apply_time_offset(w["end"], total_offset)
        first = int(words[0]["start"])
        last_end = int(words[-1]["end"])
        lines.append({
            "start": first,
            "duration": max(0, last_end - first),
            "end": last_end,
            "text": "".join(w["text"] for w in words),
            "translation": "",
            "words": words,
        })

    lines.sort(key=lambda ln: ln["start"])

    trimmed = trim_tail_staff(lines, duration_ms)
    if trimmed is None:
        logger.warning(
            "   ⚠️ [qrc] 尾部冲轴且识别不出名单段（末行 %sms / 曲长 %sms），放弃 QRC 候选",
            int(lines[-1]["end"]) if lines else 0, duration_ms,
        )
        return {"tags": tags, "lines": [], "time_precision": 3}

    return {"tags": tags, "lines": trimmed, "time_precision": 3}


__all__ = ["parse_qrc", "unwrap_qrc", "OVERRUN_TOL_MS"]
