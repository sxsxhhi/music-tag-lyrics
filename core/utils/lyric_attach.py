# -*- coding: utf-8 -*-
"""译文最近邻挂载（B1 去重）：aggregator（网易 tlyric，1500ms）与 lrcshare（2000ms）共用。

原先 core/aggregator.py 与 core/fetchers/lrcshare.py 各有一份手写实现，
算法相同（全量扫描未用条目取最近邻 + 已用集合 + 汉字门槛），仅容差与
译文预清洗不同。主行 start/end/words 不可变（铁律 18）。
"""

from typing import Any, Callable, Dict, List, Optional

from .text_utils import has_hanzi


def attach_translations_by_nearest(
    lines: List[Dict[str, Any]],
    translation_entries: List[Dict[str, Any]],
    tolerance_ms: int = 1500,
    text_cleaner: Optional[Callable[[str], str]] = None,
) -> int:
    """按最近邻把译文挂到主行，返回成功挂载的行数。

    - translation_entries: [{"start": 毫秒, "text": str}, ...]（无需预排序）
    - tolerance_ms: 行 start 与译文 start 差值的上限（含）
    - text_cleaner: 译文文本预清洗（lrcshare 需 strip_word_tags；默认不清洗）
    - 只挂含汉字的译文；每个译文条目最多被使用一次

    注：lrcshare 原实现的窗是严格 `< 2000`，合并为 `<= tolerance_ms` 后
    tolerance=2000 在恰好 2000ms 边界上放宽了 1ms（真实时间戳不会命中）。
    """
    if not lines or not translation_entries:
        return 0

    used_indices = set()
    matched_count = 0

    for line in lines:
        k_time = line.get("start", 0)
        best_diff = tolerance_ms + 1
        best_idx = -1
        best_text = ""

        for idx, entry in enumerate(translation_entries):
            if idx in used_indices:
                continue
            e_time = entry.get("start", 0)
            diff = abs(e_time - k_time)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx
                best_text = (entry.get("text") or "").strip()
                if text_cleaner:
                    best_text = text_cleaner(best_text)

        if best_idx >= 0 and best_diff <= tolerance_ms and has_hanzi(best_text):
            line["translation"] = best_text
            used_indices.add(best_idx)
            matched_count += 1

    return matched_count
