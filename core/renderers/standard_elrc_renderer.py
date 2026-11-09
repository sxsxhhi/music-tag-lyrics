# -*- coding: utf-8 -*-
"""严格三位毫秒 ELRC 序列化器 (v1.8.2 R4-A)。

满足《歌曲检测与修补标准》§6 第一条及方案铁律：
1. 统一 [mm:ss.xxx] 行时间与 <mm:ss.xxx> 字时间，严格 3 位毫秒精度；
2. 逐字行强制闭合标签 <mm:ss.xxx>，不得早于最后一个字块结束；
3. 保留英文/韩文词界空格，停顿块结构化表示；
4. 译文行紧随主行，继承相同 3 位毫秒起点，纯文本无独立逐字轴；
5. 前奏 A/B 显示行（标题、词、曲）按 PreludePlan 正确插入，且不干扰正文；
6. 严格保留 lrc.red 的 [re:lrc.red (https://lrc.red)] 署名；
7. 输出显式 newline="\n"，保证 .spl 与 .lrc 逐字节哈希一致。
"""

from __future__ import annotations

from typing import List

from ..models.canonical_lyrics import CanonicalLine, CanonicalLyrics, LineRole


def ms_to_timestamp(ms: int, tag_bracket: str = "[") -> str:
    """毫秒转换为三位毫秒时间标签，如 [01:23.456] 或 <01:23.456>。"""
    if ms < 0:
        ms = 0
    total_sec = ms // 1000
    millis = ms % 1000
    minutes = total_sec // 60
    seconds = total_sec % 60
    close_bracket = "]" if tag_bracket == "[" else ">"
    return f"{tag_bracket}{minutes:02d}:{seconds:02d}.{millis:03d}{close_bracket}"


def render_elrc_v182(canonical: CanonicalLyrics) -> str:
    """把 CanonicalLyrics 数据序列化为严格标准的 3 位毫秒 Enhanced LRC。"""
    out_lines: List[str] = []

    # 1. 检查是否有需要保留的顶层署名（如 lrc.red 回链）
    for line in canonical.lines:
        if line.role == LineRole.PRODUCTION_CREDIT and line.metadata.get("is_lrc_red_re"):
            out_lines.append(line.text.strip())

    # 2. 前奏 A/B 显示投影（如果存在有效规划）
    prelude = canonical.prelude
    if prelude and prelude.mode in ("A", "B"):
        # 标题行
        t_st, t_et = prelude.title_window
        if prelude.display_title and t_et > t_st:
            out_lines.append(
                f"{ms_to_timestamp(t_st)}<{ms_to_timestamp(t_st, '<')[1:-1]}>{prelude.display_title}<{ms_to_timestamp(t_et, '<')[1:-1]}>"
            )
        # 作词行
        if prelude.lyricist_window and prelude.display_lyricist:
            l_st, l_et = prelude.lyricist_window
            if l_et > l_st:
                out_lines.append(
                    f"{ms_to_timestamp(l_st)}<{ms_to_timestamp(l_st, '<')[1:-1]}>{prelude.display_lyricist}<{ms_to_timestamp(l_et, '<')[1:-1]}>"
                )
        # 作曲行
        if prelude.composer_window and prelude.display_composer:
            c_st, c_et = prelude.composer_window
            if c_et > c_st:
                out_lines.append(
                    f"{ms_to_timestamp(c_st)}<{ms_to_timestamp(c_st, '<')[1:-1]}>{prelude.display_composer}<{ms_to_timestamp(c_et, '<')[1:-1]}>"
                )

    # 3. 正文各行序列化
    for line in canonical.lines:
        # 跳过已由前奏处理的旧标题行与制作行
        if line.role in (LineRole.TITLE, LineRole.PRODUCTION_CREDIT, LineRole.PLACEHOLDER):
            continue

        st_tag = ms_to_timestamp(line.start_ms, "[")

        # 演唱行或对白行
        if line.is_singing:
            if line.words:
                body_parts: List[str] = []
                last_end = line.start_ms
                for w in line.words:
                    w_tag = ms_to_timestamp(w.start_ms, "<")
                    w_text = w.text
                    # 处理词界空格
                    if w.join == " " and body_parts and not body_parts[-1].endswith(" "):
                        w_text = " " + w_text
                    body_parts.append(f"{w_tag}{w_text}")
                    last_end = max(last_end, w.end_ms)
                # 末尾闭合标签
                close_tag = ms_to_timestamp(max(last_end, line.end_ms), "<")
                rendered_main = f"{st_tag}{''.join(body_parts)}{close_tag}"
            else:
                # 仅有行时间
                rendered_main = f"{st_tag}{line.text}"

            out_lines.append(rendered_main)

            # 行级译文紧随主行，继承相同行起点
            if line.translation:
                trans_text = line.translation.strip()
                if trans_text:
                    out_lines.append(f"{st_tag}{trans_text}")

        elif line.role in (LineRole.SPOKEN_CONTENT, LineRole.SAMPLE_EXPLANATION, LineRole.UNKNOWN):
            # 原样保留的特殊行与歧义行
            out_lines.append(f"{st_tag}{line.text}")

    return "\n".join(out_lines) + "\n"
