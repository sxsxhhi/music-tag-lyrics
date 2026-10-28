# -*- coding: utf-8 -*-
"""网易云 YRC 逐字歌词解析（v1.6.1）。

与 KRC 的三处关键差异（实测 320 行）
------------------------------------
1. **字时间是绝对毫秒**，不是相对行偏移。KRC 的 `<偏移,时长,0>` 要叠加行首，
   YRC 的 `(起,长,标志)` 直接就是绝对时间；按 KRC 的算法叠加行首会把整条轴推后一倍。
2. **歌词文本里可能出现未转义的半角括号**（如 `母带 : Patrick Weyer-Brown (FR)`）。
   用 `[^(]*` 抓文本会在 `(FR)` 处截断，残留字符会让 converter 判定
   「逐字字面与行文本不一致」并整行降级为行级输出。因此必须用**位置切片**：
   先定位所有 `(数字,数字,数字)` 参数，相邻两个参数之间的**原文**才是该字文本。
3. **尾部制作名单按每行固定 1000ms 等距铺排**，会一直铺到曲末之后
   （实测《那么骄傲(重遇版)》末行 268230ms / 曲长 245239ms）。这会让
   `aggregator._probe` 的 overrun 门禁把整个候选丢弃，所以本模块自带尾部截断；
   但只在「末行确实已冲轴」时生效，且识别失败一律回落 lrc，绝不硬砍真实歌词。

三类异常块的处理
----------------
| 形态 | 实测样例 | 处置 |
| :--- | :--- | :--- |
| 空文本块 | `(37100,440,0)` | 丢弃（纯静音占位），不延长末字，与 KRC 口径一致 |
| 纯空白块 | `(18020,30,0) ` | 文本并入前一字，前字 end 延到块 end（保住词间停顿） |
| 零时长块 | `(10650,0,0), ` | 文本并入前一字，时间不变（保留会造同戳连排标签） |

行首出现上述块时记为前缀，并入下一真实字。
"""
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from ..utils.time_utils import apply_time_offset
from ._common import parse_offset_tag

logger = logging.getLogger(__name__)

# 行头：[绝对起, 行时长] 后跟正文
YRC_LINE_RE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
# 字参数：(绝对起, 时长, 标志)。**只认三数字形参**，歌词里的 (FR) 不会匹配
YRC_WORD_PARAM_RE = re.compile(r"\((\d+),(\d+),(\d+)\)")
# 元数据标签（实测 YRC 体里没有，留作接口变更时的防吞行保险）
TAG_RE = re.compile(r"^\[(\w+):([^\]]*)\]$")

# 冲轴容忍与 kugou.OVERRUN_TOLERANCE_MS 同值。不 import 是为了避免 parser → fetcher 反向依赖，
# 一致性由 tests/test_netease_yrc.py 的单测锁死。
OVERRUN_TOL_MS = 8000

# 尾部名单段识别参数
TAIL_MIN_RUN = 3
TAIL_DUR_LO = 800
TAIL_DUR_HI = 1200
TAIL_ZONE_RATIO = 0.85
TAIL_JOIN_TOL_MS = 5
MIN_LINES_AFTER_TRIM = 4

Block = Tuple[int, int, str]


def _split_blocks(body: str) -> List[Block]:
    """位置切片：相邻两个 `(起,长,标志)` 之间的原文才是该字文本。

    这样 `(FR)` 这类非三数字括号会原样留在文本区间内，不会被当成新块起点。
    """
    text = body or ""
    params = list(YRC_WORD_PARAM_RE.finditer(text))
    blocks: List[Block] = []
    for i, m in enumerate(params):
        end = params[i + 1].start() if i + 1 < len(params) else len(text)
        blocks.append((int(m.group(1)), int(m.group(2)), text[m.end():end]))
    return blocks


def _normalize_blocks(blocks: List[Block]) -> List[Dict[str, Any]]:
    """三类异常块归一，保证 text 非空、duration > 0、起点非递减。"""
    words: List[Dict[str, Any]] = []
    pending = ""
    for start, dur, text in blocks:
        if text == "":
            # 空文本块：纯静音占位。留着会在 converter 里生成「<t> 后无字」的畸形标签
            continue
        if dur <= 0 or not text.strip():
            # 纯空白块 / 零时长块：文本并入前一字，不做独立 word
            if words:
                prev = words[-1]
                prev["text"] += text
                if dur > 0:
                    prev["end"] = max(prev["end"], start + dur)
                    prev["duration"] = prev["end"] - prev["start"]
            else:
                pending += text
            continue
        if pending:
            text = pending + text
            pending = ""
        if words and start < words[-1]["end"]:
            start = words[-1]["end"]          # 防御：源内轻微回退时钳成非递减
        words.append({
            "start": start,
            "duration": dur,
            "end": start + dur,
            "text": text,
        })
    return words


def trim_tail_staff(
    lines: List[Dict[str, Any]],
    duration_ms: int = 0,
) -> Optional[List[Dict[str, Any]]]:
    """截掉尾部等距铺排的 staff 名单段（只在网易通道内部使用）。

    返回：
        - 原列表：未冲轴，或没有可信基准轴（此时 `_probe` 也不做时长门禁）
        - 截断后的新列表
        - ``None``：已冲轴但识别不出名单段 / 截完仍冲轴 / 剩余行数过少
          —— 一律交回调用方回落 lrc，绝不硬砍真实歌词

    四条判据必须同时满足，且只从尾部向前连续扩：
    A 起点落在曲末 15% 内；B 每行时长 800~1200ms；C 相邻首尾相接（≤5ms）；
    D 连续 ≥3 行。真实歌词末段同时满足这四条的几率极低。
    """
    if not lines or duration_ms <= 0:
        return lines
    if int(lines[-1].get("end", 0)) <= duration_ms + OVERRUN_TOL_MS:
        return lines

    zone = int(duration_ms * TAIL_ZONE_RATIO)
    j = len(lines)
    while j > 0:
        cur = lines[j - 1]
        start = int(cur.get("start", 0))
        end = int(cur.get("end", 0))
        dur = end - start
        if start < zone:
            break
        if not (TAIL_DUR_LO <= dur <= TAIL_DUR_HI):
            break
        if j < len(lines) and abs(end - int(lines[j].get("start", 0))) > TAIL_JOIN_TOL_MS:
            break
        j -= 1

    if len(lines) - j < TAIL_MIN_RUN:
        return None

    trimmed = lines[:j]
    if trimmed and int(trimmed[-1].get("end", 0)) > duration_ms + OVERRUN_TOL_MS:
        return None
    if len(trimmed) < MIN_LINES_AFTER_TRIM:
        return None
    return trimmed


def parse_yrc(yrc_text: str, adjust: int = 0, duration_ms: int = 0) -> Dict[str, Any]:
    """解析网易云 YRC 文本为统一歌词结构。

    返回 ``{"tags", "lines", "time_precision": 3}``：
    - 每行的 ``start`` 取**首字起点**（实测 320/320 与行头相等，lead 恒为 0），
      ``end`` 取末字终点，保证 `line.start == words[0].start` 与
      `line.end == words[-1].end` 两条恒等式在源头就成立。
    - ``text`` 是 words 的原样拼接，喂给 converter 的逐字字面一致性校验。
    - **不加** ``duration_ms``（会改变 compute_duration_axis 的语义），
      **不加** ``has_embedded_translation``（YRC 无内嵌译文，译文走 ytlrc）。
    - 尾部名单截断失败时返回空 ``lines``，由 fetcher 回落 lrc。
    """
    tags: Dict[str, str] = {}
    bodies: List[str] = []

    for line in (yrc_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        m = YRC_LINE_RE.match(s)
        if not m:
            tm = TAG_RE.match(s)
            if tm:
                tags[tm.group(1).lower()] = tm.group(2).strip()
            continue
        bodies.append(m.group(3))

    total_offset = parse_offset_tag(tags) + adjust

    lines: List[Dict[str, Any]] = []
    for body in bodies:
        words = _normalize_blocks(_split_blocks(body))
        if not words:
            continue                       # 整行无有效块：丢弃，不产幽灵行
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
            "   ⚠️ [yrc] 尾部冲轴且识别不出名单段（末行 %sms / 曲长 %sms），放弃 YRC 候选",
            int(lines[-1]["end"]) if lines else 0, duration_ms,
        )
        return {"tags": tags, "lines": [], "time_precision": 3}

    return {"tags": tags, "lines": trimmed, "time_precision": 3}


__all__ = ["parse_yrc", "trim_tail_staff", "OVERRUN_TOL_MS"]
