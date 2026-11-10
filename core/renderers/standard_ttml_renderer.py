# -*- coding: utf-8 -*-
"""v1.8.2 TTML serialization: line-keyed translation, cue and gap preservation."""
from __future__ import annotations
import xml.etree.ElementTree as ET
from ..models.canonical_lyrics import CanonicalLyrics, LineRole, word_boundary_space

NS_TTML = "http://www.w3.org/ns/ttml"
NS_ITUNES = "http://music.apple.com/lyric-ttml-internal"
NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_XML = "http://www.w3.org/XML/1998/namespace"


def ms_to_ttml_time(ms: int) -> str:
    """Format milliseconds into strict W3C TTML clock-time (HH:MM:SS.mmm).
    
    Uses pure integer division without floating-point accumulation errors.
    Hours may exceed 99 if needed; minutes and seconds are always 00-59.
    """
    if ms < 0:
        raise ValueError("Negative canonical timestamp")
    hours, rem = divmod(ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    seconds, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def _prefix(line) -> str:
    if line.words and ''.join(w.text for w in line.words).strip() == line.text.strip():
        return ""
    if not line.speaker_prefix: return ""
    pre = line.speaker_prefix
    return line.text[:len(pre)+1] if line.text.startswith((pre+":", pre+"：")) else pre+"："


def render_ttml_v182(canonical: CanonicalLyrics, lang: str = "", translation_lang: str = "zh-Hans-CN") -> str:
    ET.register_namespace("", NS_TTML)
    ET.register_namespace("itunes", NS_ITUNES)
    ET.register_namespace("ttm", NS_TTM)
    lang = lang or str(canonical.song.get("language") or "und")
    root = ET.Element(f"{{{NS_TTML}}}tt", {f"{{{NS_ITUNES}}}timing": "Word", f"{{{NS_XML}}}lang": lang})
    head = ET.SubElement(root, f"{{{NS_TTML}}}head")
    meta = ET.SubElement(head, f"{{{NS_TTML}}}metadata")
    if canonical.song.get('attribution_re') == 'lrc.red (https://lrc.red)':
        ET.SubElement(meta, f"{{{NS_TTM}}}desc").text = 'Lyrics provided by lrc.red (https://lrc.red)'
    trans = [(line.stable_id, line.translation.strip()) for line in canonical.lines
             if line.is_singing and line.translation and line.translation.strip()]
    if trans:
        im = ET.SubElement(meta, f"{{{NS_ITUNES}}}iTunesMetadata")
        group = ET.SubElement(im, f"{{{NS_ITUNES}}}translations")
        layer = ET.SubElement(group, f"{{{NS_ITUNES}}}translation",
                              {f"{{{NS_XML}}}lang":translation_lang,"type":"subtitle"})
        for key, content in trans:
            ET.SubElement(layer, f"{{{NS_ITUNES}}}text", {"for":key}).text = content
    body = ET.SubElement(root, f"{{{NS_TTML}}}body")
    div = ET.SubElement(body, f"{{{NS_TTML}}}div")
    spans = []

    def write_p(start, end, key, display_text=None, words=None, role=""):
        if end <= start: raise ValueError("TTML cannot represent zero/negative p duration")
        attrs={"begin":ms_to_ttml_time(start),"end":ms_to_ttml_time(end),f"{{{NS_ITUNES}}}key":key}
        if role: attrs[f"{{{NS_ITUNES}}}role"]=role
        node=ET.SubElement(div, f"{{{NS_TTML}}}p", attrs)
        if words:
            node.text=display_text or ""
            previous=None
            previous_span=None
            for w in words:
                if w.end_ms <= w.start_ms: raise ValueError("Nonpositive canonical word span")
                if previous is not None and w.start_ms < previous.end_ms:
                    raise ValueError("Overlapping word spans")
                if previous_span is not None and word_boundary_space(previous,w):
                    if not (previous_span.tail or "").endswith(" ") and not w.text.startswith(" "):
                        previous_span.tail = " "
                previous_span=ET.SubElement(node, f"{{{NS_TTML}}}span",
                       {"begin":ms_to_ttml_time(w.start_ms), "end":ms_to_ttml_time(w.end_ms)})
                previous_span.text=w.text
                previous=w
        else:
            ET.SubElement(node, f"{{{NS_TTML}}}span", {"begin":ms_to_ttml_time(start),
                         "end":ms_to_ttml_time(end)}).text=display_text or ""
        spans.append((start,end))

    p=canonical.prelude
    if p and p.mode in ("A","B"):
        for key,window,display in (("P_TITLE",p.title_window,p.display_title),
                                   ("P_LYRICIST",p.lyricist_window,p.display_lyricist),
                                   ("P_COMPOSER",p.composer_window,p.display_composer)):
            if window and display and window[1]>window[0]:
                write_p(*window,key,display,role="PRELUDE")
    for ln in canonical.lines:
        if ln.role in (LineRole.TITLE,LineRole.LYRICIST,LineRole.COMPOSER,
                       LineRole.PRODUCTION_CREDIT,LineRole.PLACEHOLDER,LineRole.TRANSLATION):
            continue
        if not ln.text.strip():continue
        if ln.role==LineRole.SINGING and not ln.words:
            raise ValueError('SINGING_WORDS_ABSENT: do not manufacture false acoustic word spans')
        if ln.is_singing and ln.words:
            if ln.words[-1].end_ms != ln.end_ms:
                raise ValueError("Line end differs from last word end")
            write_p(ln.start_ms,ln.end_ms,ln.stable_id,_prefix(ln),ln.words,role=ln.role.value)
        else:
            write_p(ln.start_ms,ln.end_ms,ln.stable_id,ln.text,role=ln.role.value)
    if spans:
        div.set("begin",ms_to_ttml_time(min(x[0] for x in spans)))
        div.set("end",ms_to_ttml_time(max(x[1] for x in spans)))
    return ET.tostring(root,encoding="unicode",short_empty_elements=True)
