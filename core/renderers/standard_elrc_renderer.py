# -*- coding: utf-8 -*-
"""v1.8.2 verified Enhanced LRC renderer. The original canonical word axis is
not repaired, stretched or quantized; pause endpoints and performer cues survive.
"""
from __future__ import annotations
from typing import List
from ..models.canonical_lyrics import CanonicalLyrics, LineRole, word_boundary_space


def ms_to_timestamp(ms: int, tag_bracket: str = "[") -> str:
    if ms < 0:
        raise ValueError("Negative time in canonical lyrics")
    minutes, rem = divmod(ms, 60000)
    seconds, millis = divmod(rem, 1000)
    close = "]" if tag_bracket == "[" else ">"
    return f"{tag_bracket}{minutes:02d}:{seconds:02d}.{millis:03d}{close}"


def _display_line(st: int, en: int, text: str) -> str:
    if en <= st:
        raise ValueError("Non-positive display line duration")
    return f"{ms_to_timestamp(st)}{ms_to_timestamp(st, '<')}{text}{ms_to_timestamp(en, '<')}"


def _inline_prefix(line) -> str:
    # Some input sources already include the display cue inside the timed
    # token text. Never serialize the same cue a second time.
    if line.words and ''.join(w.text for w in line.words).strip() == line.text.strip():
        return ""
    prefix = line.speaker_prefix or ""
    if not prefix:
        return ""
    marker = prefix + "："
    if line.text.startswith(marker) or line.text.startswith(prefix + ":"):
        return line.text[:len(prefix) + 1]
    return marker


def render_elrc_v182(canonical: CanonicalLyrics) -> str:
    out: List[str] = []
    if canonical.song.get("attribution_re") == "lrc.red (https://lrc.red)":
        out.append("[re:lrc.red (https://lrc.red)]")
    for ln in canonical.lines:
        if ln.metadata.get("is_lrc_red_re") and "[re:lrc.red (https://lrc.red)]" not in out:
            out.append("[re:lrc.red (https://lrc.red)]")
    p = canonical.prelude
    if p and p.mode in ("A", "B"):
        # 前奏显示行（标题/词曲）为字符级逐字平均分配，与日常 to_spl 共用同一算法；
        # 铁律 20 仅保护正文演唱字轴，前奏显示投影不在演唱正文内、不受其约束。
        from ..utils.text_utils import make_char_words, format_verbatim_words_line
        slots = [(p.title_window, p.display_title),
                 (p.lyricist_window, p.display_lyricist),
                 (p.composer_window, p.display_composer)]
        for window, title in slots:
            if window and title and window[1] > window[0]:
                cw = make_char_words(title, window[0], window[1])
                # 窗口不足每字 1ms（合并行/极短前奏）时 fail-closed 退回单词块，
                # 严禁伪造 0ms 字块；两入口同判定，保持一致。
                if cw and len(title) <= (window[1] - window[0]):
                    out.append(format_verbatim_words_line(cw, cw[0]["start"], window[1], 3))
                else:
                    out.append(_display_line(*window, title))
    for line in canonical.lines:
        if line.role in (LineRole.TITLE, LineRole.LYRICIST, LineRole.COMPOSER,
                         LineRole.PRODUCTION_CREDIT, LineRole.PLACEHOLDER,
                         LineRole.TRANSLATION):
            continue
        if not line.text.strip():
            continue
        if line.role==LineRole.SINGING and not line.words:
            raise ValueError('SINGING_WORDS_ABSENT: do not manufacture false acoustic word spans')
        if line.words and line.is_singing:
            valid_words = [w for w in line.words if w.text or w.is_pause]
            if not valid_words:
                raise ValueError("Singing line has only empty undifferentiated words")
            # If the cue is not part of timed lyric words, serialize it as
            # untimed p-text; do not invent acoustic word timestamps for it.
            body = [ms_to_timestamp(line.start_ms), _inline_prefix(line)]
            last_end = None
            prior = None
            displayed = ""
            for w in valid_words:
                if w.end_ms <= w.start_ms:
                    # 带上行号与字块区间，便于把 BLOCKED 直接定位到源数据问题，
                    # 而不是只看到一个无法追溯的渲染失败。
                    raise ValueError(
                        "Invalid canonical word span: %s @ %d-%d"
                        % (line.stable_id, w.start_ms, w.end_ms))
                if last_end is not None:
                    if w.start_ms < last_end:
                        raise ValueError("Overlapping canonical word spans")
                    if w.start_ms > last_end:
                        body.append(ms_to_timestamp(last_end, "<"))  # empty pause token
                text = w.text
                if prior and word_boundary_space(prior, w) and not text.startswith(" ") and not displayed.endswith(" "):
                    text = " " + text
                body.append(ms_to_timestamp(w.start_ms, "<") + text)
                displayed += text
                last_end, prior = w.end_ms, w
            # Exact word end is independent of line end. If these differ,
            # ELRC cannot carry both without inventing a lyric word.
            if last_end != line.end_ms:
                raise ValueError("Line end differs from last word end; ELRC cannot encode both")
            body.append(ms_to_timestamp(last_end, "<"))
            out.append("".join(body))
        else:
            out.append(_display_line(line.start_ms, line.end_ms, line.text))
        if line.translation and line.translation.strip():
            out.append(f"{ms_to_timestamp(line.start_ms)}{line.translation.strip()}")
    return "\n".join(out) + "\n"
