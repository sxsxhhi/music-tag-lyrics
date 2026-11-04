# -*- coding: utf-8 -*-
"""解析器共享：KRC/LRC 的 [offset] 标签、逐字块归一。"""

from typing import Any, Dict, List, Mapping, Tuple

__all__ = ["parse_offset_tag", "normalize_word_blocks", "WordBlock"]

WordBlock = Tuple[int, int, str]


def parse_offset_tag(tags: Mapping[str, Any]) -> int:
    """
    读取并解析 ``[offset:xxx]`` 标签值，单位毫秒。

    - 标签缺失 → 0
    - 值为空/非数字 → 0（旧实现只捕获 ValueError，None 会漏成 TypeError）
    """
    raw = tags.get("offset", None) if tags else None
    if raw is None:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def normalize_word_blocks(blocks: List[WordBlock]) -> List[Dict[str, Any]]:
    """三类异常块归一，保证 text 非空、duration > 0、起点非递减。

    逐字源（网易 YRC / QQ QRC）共用同一口径，避免两套实现漂移：

    | 形态 | 处置 |
    | :--- | :--- |
    | 空文本块（纯静音占位） | 丢弃，不延长末字 |
    | 纯空白块 / 零时长块 | 文本并入前一字，前字 end 延到块 end |
    | 行首出现上述块 | 记为 pending，并入下一真实字 |
    """
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
