# -*- coding: utf-8 -*-
"""合规单行 XML TTML 序列化器 (v1.8.2 R4-B)。

满足《歌曲检测与修补标准》§6 第二条及方案铁律：
1. 规范 XML 树构建与单行 XML 序列化输出；
2. 锁定 Apple/AMLL 标准命名空间 http://music.apple.com/lyric-ttml-internal；
3. 译文结构统一在 <head><metadata><iTunesMetadata> 中以 <text for="Ln"> 挂载；
4. 真实演唱逐字转换为标准 <span> 节点，词界空格作为文本节点保存；
5. 自动根据正文起止点精确计算 <div> 容器边界；
6. 前奏 A/B 显示行（标题、词、曲）以独立显示段挂载，不污染演唱逐字统计。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from ..models.canonical_lyrics import CanonicalLyrics, LineRole


def ms_to_ttml_time(ms: int) -> str:
    """毫秒转换为标准 TTML 时间戳 mm:ss.xxx。"""
    if ms < 0:
        ms = 0
    total_sec = ms // 1000
    millis = ms % 1000
    minutes = total_sec // 60
    seconds = total_sec % 60
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def render_ttml_v182(
    canonical: CanonicalLyrics,
    lang: str = "zh-CN",
    translation_lang: str = "zh-Hans-CN",
) -> str:
    """把 CanonicalLyrics 导出为单行 iTunes/Apple Music 规范 TTML。"""
    # 注册命名空间前缀
    NS_TTML = "http://www.w3.org/ns/ttml"
    NS_ITUNES = "http://music.apple.com/lyric-ttml-internal"
    NS_TTM = "http://www.w3.org/ns/ttml#metadata"

    ET.register_namespace("", NS_TTML)
    ET.register_namespace("itunes", NS_ITUNES)
    ET.register_namespace("ttm", NS_TTM)

    root = ET.Element(
        f"{{{NS_TTML}}}tt",
        {
            f"{{{NS_ITUNES}}}timing": "Word",
            "{http://www.w3.org/XML/1998/namespace}lang": lang,
        },
    )

    # 1. 收集行级译文并构建 <head><metadata><iTunesMetadata>
    translations_list = []
    for line in canonical.lines:
        if line.is_singing and line.translation and line.translation.strip():
            translations_list.append((line.stable_id, line.translation.strip()))

    if translations_list:
        head = ET.SubElement(root, f"{{{NS_TTML}}}head")
        meta = ET.SubElement(head, f"{{{NS_TTML}}}metadata")
        itunes_meta = ET.SubElement(meta, f"{{{NS_ITUNES}}}iTunesMetadata")
        translations_elem = ET.SubElement(itunes_meta, "translations")
        trans_block = ET.SubElement(
            translations_elem,
            "translation",
            {
                "{http://www.w3.org/XML/1998/namespace}lang": translation_lang,
                "type": "subtitle",
            },
        )
        for line_id, text in translations_list:
            t_node = ET.SubElement(trans_block, "text", {"for": line_id})
            t_node.text = text

    # 2. 计算整个 body 的起止范围
    min_ms = 999999999
    max_ms = 0

    # 包含前奏范围
    prelude = canonical.prelude
    if prelude and prelude.mode in ("A", "B"):
        min_ms = min(min_ms, prelude.title_window[0])
        max_ms = max(max_ms, prelude.title_window[1])
        if prelude.lyricist_window:
            max_ms = max(max_ms, prelude.lyricist_window[1])
        if prelude.composer_window:
            max_ms = max(max_ms, prelude.composer_window[1])

    for line in canonical.lines:
        if line.is_singing:
            min_ms = min(min_ms, line.start_ms)
            max_ms = max(max_ms, line.end_ms)
            for w in line.words:
                min_ms = min(min_ms, w.start_ms)
                max_ms = max(max_ms, w.end_ms)

    if min_ms > max_ms:
        min_ms = 0
        max_ms = 1000

    body = ET.SubElement(root, f"{{{NS_TTML}}}body")
    div = ET.SubElement(
        body,
        f"{{{NS_TTML}}}div",
        {
            "begin": ms_to_ttml_time(min_ms),
            "end": ms_to_ttml_time(max_ms),
        },
    )

    # 3. 渲染前奏显示行（独立段落，不进入真实演唱统计）
    if prelude and prelude.mode in ("A", "B"):
        t_st, t_et = prelude.title_window
        if prelude.display_title and t_et > t_st:
            p_title = ET.SubElement(
                div,
                f"{{{NS_TTML}}}p",
                {
                    "begin": ms_to_ttml_time(t_st),
                    "end": ms_to_ttml_time(t_et),
                    f"{{{NS_ITUNES}}}key": "P_TITLE",
                },
            )
            s_title = ET.SubElement(
                p_title,
                f"{{{NS_TTML}}}span",
                {
                    "begin": ms_to_ttml_time(t_st),
                    "end": ms_to_ttml_time(t_et),
                },
            )
            s_title.text = prelude.display_title

        if prelude.lyricist_window and prelude.display_lyricist:
            l_st, l_et = prelude.lyricist_window
            if l_et > l_st:
                p_lyr = ET.SubElement(
                    div,
                    f"{{{NS_TTML}}}p",
                    {
                        "begin": ms_to_ttml_time(l_st),
                        "end": ms_to_ttml_time(l_et),
                        f"{{{NS_ITUNES}}}key": "P_LYRICIST",
                    },
                )
                s_lyr = ET.SubElement(
                    p_lyr,
                    f"{{{NS_TTML}}}span",
                    {
                        "begin": ms_to_ttml_time(l_st),
                        "end": ms_to_ttml_time(l_et),
                    },
                )
                s_lyr.text = prelude.display_lyricist

        if prelude.composer_window and prelude.display_composer:
            c_st, c_et = prelude.composer_window
            if c_et > c_st:
                p_cmp = ET.SubElement(
                    div,
                    f"{{{NS_TTML}}}p",
                    {
                        "begin": ms_to_ttml_time(c_st),
                        "end": ms_to_ttml_time(c_et),
                        f"{{{NS_ITUNES}}}key": "P_COMPOSER",
                    },
                )
                s_cmp = ET.SubElement(
                    p_cmp,
                    f"{{{NS_TTML}}}span",
                    {
                        "begin": ms_to_ttml_time(c_st),
                        "end": ms_to_ttml_time(c_et),
                    },
                )
                s_cmp.text = prelude.display_composer

    # 4. 渲染正文各行
    for line in canonical.lines:
        if not line.is_singing:
            continue

        p_begin = ms_to_ttml_time(line.start_ms)
        p_end = ms_to_ttml_time(line.end_ms)
        p_elem = ET.SubElement(
            div,
            f"{{{NS_TTML}}}p",
            {
                "begin": p_begin,
                "end": p_end,
                f"{{{NS_ITUNES}}}key": line.stable_id,
            },
        )

        if line.words:
            for idx, w in enumerate(line.words):
                span = ET.SubElement(
                    p_elem,
                    f"{{{NS_TTML}}}span",
                    {
                        "begin": ms_to_ttml_time(w.start_ms),
                        "end": ms_to_ttml_time(w.end_ms),
                    },
                )
                span.text = w.text
                # 若带词间空格，置于 span.tail
                if w.join == " " and idx > 0:
                    # 设在前一个 span 的 tail 上
                    prev_span = p_elem[idx - 1]
                    prev_span.tail = " "
        else:
            # 纯行级回退
            span = ET.SubElement(
                p_elem,
                f"{{{NS_TTML}}}span",
                {
                    "begin": p_begin,
                    "end": p_end,
                },
            )
            span.text = line.text

    # 单行 XML 序列化
    xml_bytes = ET.tostring(root, encoding="utf-8")
    return xml_bytes.decode("utf-8")
