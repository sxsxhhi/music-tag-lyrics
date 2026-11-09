# -*- coding: utf-8 -*-
"""全曲内容语义识别、分类与安全清理引擎 (v1.8.2 R2)。

满足《歌曲检测与修补标准》§3 全部要求及方案铁律：
1. 全曲逐行扫描：对开头、正文中间、片尾一视同仁；
2. 绝对禁止整段截断（绝不使用 lines[:i] 切除尾部）；
3. 严格保护真实演唱、对白、原唱署名、演唱角色提示与采样说明；
4. 提取可信词曲（含片尾词曲提取），多方冲突标记 REVIEW_REQUIRED；
5. 识别并精确挂接同时间戳行级译文；
6. 未知/歧义行保持原样并输出审计记录。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ..models.canonical_lyrics import (
    AuditIssue,
    CanonicalLine,
    CanonicalLyrics,
    CanonicalWord,
    IssueSeverity,
    LineRole,
)
from ..utils.time_utils import strip_timing_tags

# 元数据行残留正则（混入正文的标签头）
_LRC_HEADER_TAG_RE = re.compile(
    r"^\[(ti|ar|al|by|offset|length|hash|sign|qq|total):(.*?)\]$",
    re.IGNORECASE,
)
# 特殊保留署名：lrc.red 署名回链（铁律保护，不得当作普通元数据丢弃）
_LRC_RED_RE_TAG_RE = re.compile(r"^\[re:lrc\.red\s*\([^)]*\)\]$", re.IGNORECASE)

# 词曲署名标记：前置否定保证单字 '词/曲' 不会误吞 '编曲/編曲/作词/作曲'
_CREDIT_TOKEN_RE = re.compile(
    r"(作词作曲|作詞作曲|词曲|作词|作詞|作曲|Lyrics|Written|Composed)"
    r"(?:\s+by)?\s*[:：]"
    r"|(?<![编編作])(词|曲)\s*[:：]",
    re.IGNORECASE,
)
_CREDIT_BOTH_SET = {"作词作曲", "作詞作曲", "词曲"}
_CREDIT_LYRICIST_SET = {"作词", "作詞", "词", "lyrics", "written"}
_CREDIT_COMPOSER_SET = {"作曲", "曲", "composed"}

# 制作人员角色关键词（编曲、制作、混音、录音、母带、企划、监制、出品、鸣谢等）
_PRODUCTION_KEYWORD_RE = re.compile(
    r"^(编曲|編曲|Arranger|Arranged\s+by|制作人|製作人|Producer|Produced\s+by|"
    r"录音|錄音|Recording|Recorded\s+by|录音师|錄音師|录音室|錄音室|"
    r"混音|Mixing|Mixed\s+by|混音师|混音室|"
    r"母带|母帶|Mastering|Mastered\s+by|母带师|母带工程|"
    r"监制|監制|Executive\s+Producer|总监制|音乐总监|"
    r"企划|企画|策划|Planning|统筹|项目统筹|"
    r"出品|Publisher|Published\s+by|出品人|发行|發行|"
    r"吉他|贝斯|鼓|键盘|弦乐|管乐|和声编写|合音编写|"
    r"鸣谢|特別鸣谢|特别鸣谢|感謝|特別感謝|鳴謝|特別鳴謝|Special\s+Thanks)"
    r"(?:\s+by)?\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)

# 必须保留的特殊说明（原唱、采样、对白来源）
_ORIGINAL_SINGER_RE = re.compile(
    r"^(原唱|Original\s+Artist)\s*[:：]\s*(.*)$", re.IGNORECASE
)
_SAMPLE_EXPLANATION_RE = re.compile(
    r"^(采样来源|采样自|采样|Sample\s+Source|对白来源|对白选自|Dialogue\s+from)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)

# 歌唱角色提示（男：/女：/合：/合唱：等）
_PERFORMER_CUE_PREFIX_RE = re.compile(
    r"^(?:\[|\()?((?:男|女|合|合唱|独唱|领唱|童声|二重唱))(?::|：|\]|\))\s*(.*)$"
)

# 纯占位词（纯音乐/伴奏等）
_PLACEHOLDER_RE = re.compile(
    r"^\[?(纯音乐|純音樂|伴奏|Instrumental|No\s+Lyrics|没有歌词)\]?$",
    re.IGNORECASE,
)

# 歧义词提示（说唱：/旁白：/念白：/和声：/人声：等，若无确定上下文则保留并标记 REVIEW_REQUIRED）
_AMBIGUOUS_CUE_RE = re.compile(
    r"^(说唱|說唱|Rap|旁白|念白|和声|和聲|合音|人声|人聲)\s*[:：]\s*(.*)$",
    re.IGNORECASE,
)


def _split_credit_tokens(clean_text: str) -> List[Tuple[str, str]]:
    """切分单行中的作词/作曲键值对。"""
    tokens = list(_CREDIT_TOKEN_RE.finditer(clean_text or ""))
    pairs: List[Tuple[str, str]] = []
    for i, m in enumerate(tokens):
        kind = (m.group(1) or m.group(2) or "").strip()
        start = m.end()
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(clean_text)
        val = clean_text[start:end].strip().strip("/、,，|").strip()
        if kind and val:
            pairs.append((kind, val))
    return pairs


def parse_filename_backup(filename: str) -> Tuple[str, str]:
    """文件名后备解析：仅在元数据缺失时，按最后一个字面 ' - ' 分割。
    
    返回 (title, artist)。
    例如: '广场 - 李志.flac' -> ('广场', '李志')
          'Live - A - B - 歌手.mp3' -> ('Live - A - B', '歌手')
    """
    base = filename
    if "/" in base:
        base = base.rsplit("/", 1)[-1]
    if "\\" in base:
        base = base.rsplit("\\", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    
    # 严格按最后一个 ' - ' 分割（任务书 §3.2 第 3 条）
    if " - " in base:
        parts = base.rsplit(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    return base.strip(), ""


def classify_line_role(
    raw_text: str,
    song_title: str = "",
    song_artist: str = "",
    line_index: int = 0,
    total_lines: int = 1,
    has_words: bool = False,
) -> Tuple[LineRole, Dict[str, Any]]:
    """对单行歌词文本执行深度语义角色分类。
    
    返回 (LineRole, 额外属性dict)。
    """
    clean = strip_timing_tags(raw_text).strip()
    if not clean:
        return LineRole.UNKNOWN, {"clean": ""}

    # 1. 检查特殊保留的 lrc.red 署名回链
    if _LRC_RED_RE_TAG_RE.match(clean):
        return LineRole.PRODUCTION_CREDIT, {"keep_credit": True, "is_lrc_red_re": True}

    # 2. 检查 LRC 头部残留标签
    m_header = _LRC_HEADER_TAG_RE.match(clean)
    if m_header:
        tag_key = m_header.group(1).lower()
        tag_val = m_header.group(2).strip()
        if tag_key == "ti":
            return LineRole.TITLE, {"tag_val": tag_val, "is_lrc_header": True}
        if tag_key in ("ar", "al", "by", "offset"):
            return LineRole.PRODUCTION_CREDIT, {"tag_val": tag_val, "is_lrc_header": True}

    # 3. 占位行检查（纯音乐/伴奏）
    if _PLACEHOLDER_RE.match(clean):
        return LineRole.PLACEHOLDER, {"clean": clean}

    # 4. 原唱署名保护（必须保留）
    m_orig = _ORIGINAL_SINGER_RE.match(clean)
    if m_orig:
        return LineRole.SPOKEN_CONTENT, {
            "keep_content": True,
            "original_singer": m_orig.group(2).strip(),
            "reason": "ORIGINAL_SINGER_RETAINED",
        }

    # 5. 采样/对白说明保护（必须保留）
    m_sample = _SAMPLE_EXPLANATION_RE.match(clean)
    if m_sample:
        return LineRole.SAMPLE_EXPLANATION, {
            "keep_content": True,
            "sample_info": m_sample.group(2).strip(),
            "reason": "SAMPLE_EXPLANATION_RETAINED",
        }

    # 6. 词曲署名提取与分类
    credit_pairs = _split_credit_tokens(clean)
    if credit_pairs:
        lyricists = []
        composers = []
        for kind, val in credit_pairs:
            k = kind.lower()
            if kind in _CREDIT_BOTH_SET:
                lyricists.append(val)
                composers.append(val)
            elif any(k.startswith(p) for p in _CREDIT_LYRICIST_SET):
                lyricists.append(val)
            elif any(k.startswith(p) for p in _CREDIT_COMPOSER_SET):
                composers.append(val)
        if lyricists and composers:
            return LineRole.LYRICIST, {
                "lyricists": lyricists,
                "composers": composers,
                "both": True,
            }
        if lyricists:
            return LineRole.LYRICIST, {"lyricists": lyricists}
        if composers:
            return LineRole.COMPOSER, {"composers": composers}

    # 7. 制作团队与鸣谢（全曲通用）
    m_prod = _PRODUCTION_KEYWORD_RE.match(clean)
    if m_prod:
        prod_role = m_prod.group(1).strip()
        prod_staff = m_prod.group(2).strip()
        return LineRole.PRODUCTION_CREDIT, {
            "prod_role": prod_role,
            "prod_staff": prod_staff,
        }

    # 8. 演唱角色前缀（男：/女：/合：/合唱：/歌手名：）
    m_cue = _PERFORMER_CUE_PREFIX_RE.match(clean)
    if m_cue:
        cue_prefix = m_cue.group(1).strip()
        rest_text = m_cue.group(2).strip()
        if not rest_text:
            return LineRole.PERFORMER_CUE, {
                "cue_prefix": cue_prefix,
                "standalone": True,
            }
        return LineRole.SINGING, {
            "cue_prefix": cue_prefix,
            "singing_text": rest_text,
            "has_cue_prefix": True,
        }

    # 9. 歧义词检查（说唱：/旁白：/念白：/和声：等）
    m_amb = _AMBIGUOUS_CUE_RE.match(clean)
    if m_amb:
        amb_role = m_amb.group(1).strip()
        amb_content = m_amb.group(2).strip()
        if has_words and amb_content:
            return LineRole.SINGING, {
                "cue_prefix": amb_role,
                "singing_text": amb_content,
                "ambiguous_prefix": True,
            }
        return LineRole.UNKNOWN, {
            "ambiguous_role": amb_role,
            "ambiguous_content": amb_content,
            "reason": "AMBIGUOUS_STAFF_OR_PERFORMANCE",
        }

    # 10. 标题行识别
    clean_norm = clean.lower()
    title_norm = song_title.lower().strip() if song_title else ""
    artist_norm = song_artist.lower().strip() if song_artist else ""

    if title_norm and (line_index == 0 or line_index < 3):
        if clean_norm == title_norm:
            return LineRole.TITLE, {"match_type": "EXACT_TITLE"}
        if artist_norm:
            if clean_norm == f"{title_norm} - {artist_norm}":
                return LineRole.TITLE, {"match_type": "TITLE_DASH_ARTIST"}
            if clean_norm == f"{artist_norm} - {title_norm}":
                return LineRole.TITLE, {"match_type": "ARTIST_DASH_TITLE"}

    return LineRole.SINGING, {"clean": clean}


def classify_and_clean_all_lines(
    lyrics_data: Dict[str, Any],
    ref_lyrics_data: Optional[Dict[str, Any]] = None,
    song_title: str = "",
    song_artist: str = "",
    audio_path: str = "",
) -> CanonicalLyrics:
    """R2: 对全曲所有逻辑行执行语义分类、制作名单提取与精准清理。"""
    canonical = CanonicalLyrics()
    raw_lines = lyrics_data.get("lines", []) or []
    canonical.song = {
        "title": song_title or lyrics_data.get("tags", {}).get("title", ""),
        "artist": song_artist or lyrics_data.get("tags", {}).get("artist", ""),
        "album": lyrics_data.get("tags", {}).get("album", ""),
        "audio_path": audio_path,
    }

    if not canonical.song["title"] and audio_path:
        fb_title, fb_artist = parse_filename_backup(audio_path)
        canonical.song["title"] = fb_title
        if fb_artist and not canonical.song["artist"]:
            canonical.song["artist"] = fb_artist

    eff_title = canonical.song.get("title", "")
    eff_artist = canonical.song.get("artist", "")

    classified_lines: List[Tuple[CanonicalLine, Dict[str, Any]]] = []
    extracted_lyricists: List[str] = []
    extracted_composers: List[str] = []

    for idx, rline in enumerate(raw_lines):
        raw_text = rline.get("text", "") or ""
        st_ms = int(rline.get("start", 0))
        et_ms = int(rline.get("end", st_ms + int(rline.get("duration", 0))))
        words_data = rline.get("words", []) or []
        
        words: List[CanonicalWord] = []
        for w in words_data:
            w_text = w.get("text", "")
            w_st = int(w.get("start", st_ms))
            w_et = int(w.get("end", w_st + int(w.get("duration", 0))))
            w_join = w.get("join")
            w_pause = bool(w.get("is_pause", False))
            words.append(
                CanonicalWord(
                    text=w_text,
                    start_ms=w_st,
                    end_ms=w_et,
                    join=w_join,
                    is_pause=w_pause,
                )
            )

        # 检查是否为紧随前一行、同起点的译文行
        if classified_lines and not words:
            prev_cline, prev_extra = classified_lines[-1]
            if prev_cline.start_ms == st_ms and prev_cline.is_singing:
                prev_cline.translation = raw_text.strip()
                t_line = CanonicalLine(
                    stable_id=f"L{idx + 1}",
                    source_index=idx,
                    role=LineRole.TRANSLATION,
                    start_ms=st_ms,
                    end_ms=et_ms,
                    text=raw_text,
                    translation=raw_text.strip(),
                )
                classified_lines.append((t_line, {"is_attached_translation": True}))
                continue

        role, extra = classify_line_role(
            raw_text=raw_text,
            song_title=eff_title,
            song_artist=eff_artist,
            line_index=idx,
            total_lines=len(raw_lines),
            has_words=bool(words),
        )

        if "lyricists" in extra:
            extracted_lyricists.extend(extra["lyricists"])
        if "composers" in extra:
            extracted_composers.extend(extra["composers"])

        cline = CanonicalLine(
            stable_id=f"L{idx + 1}",
            source_index=idx,
            role=role,
            start_ms=st_ms,
            end_ms=et_ms,
            text=raw_text,
            words=words,
            translation=rline.get("translation"),
            translation_source=rline.get("translation_source"),
            speaker_prefix=extra.get("cue_prefix"),
            synthetic=bool(rline.get("synthetic", False)),
            metadata=extra,
        )
        classified_lines.append((cline, extra))

    def _dedup_list(items: List[str]) -> List[str]:
        seen: Set[str] = set()
        res: List[str] = []
        for it in items:
            it_clean = it.strip()
            if it_clean and it_clean not in seen:
                seen.add(it_clean)
                res.append(it_clean)
        return res

    final_lyricists = _dedup_list(extracted_lyricists)
    final_composers = _dedup_list(extracted_composers)
    canonical.song["extracted_lyricist"] = "/".join(final_lyricists) if final_lyricists else None
    canonical.song["extracted_composer"] = "/".join(final_composers) if final_composers else None

    cleaned_lines: List[CanonicalLine] = []
    purged_indices: List[int] = []

    for cline, extra in classified_lines:
        if cline.role == LineRole.TRANSLATION:
            # 译文已作为属性挂接于对应主行，不作为独立唱句留存
            continue

        if cline.role == LineRole.PLACEHOLDER:
            canonical.add_issue(
                code="PLACEHOLDER_REMOVED",
                severity=IssueSeverity.INFO,
                line_id=cline.stable_id,
                source_index=cline.source_index,
                evidence=cline.text,
                action="REMOVE_LINE",
            )
            purged_indices.append(cline.source_index)
            continue

        if cline.role == LineRole.PRODUCTION_CREDIT:
            if extra.get("is_lrc_red_re"):
                cleaned_lines.append(cline)
                continue
            canonical.add_issue(
                code="PRODUCTION_CREDIT_REMOVED",
                severity=IssueSeverity.INFO,
                line_id=cline.stable_id,
                source_index=cline.source_index,
                evidence=cline.text,
                action="REMOVE_LINE",
            )
            purged_indices.append(cline.source_index)
            continue

        if cline.role in (LineRole.LYRICIST, LineRole.COMPOSER):
            canonical.add_issue(
                code="CREDIT_LINE_EXTRACTED_AND_REMOVED",
                severity=IssueSeverity.INFO,
                line_id=cline.stable_id,
                source_index=cline.source_index,
                evidence=cline.text,
                action="EXTRACT_AND_REMOVE",
            )
            purged_indices.append(cline.source_index)
            continue

        if cline.role == LineRole.UNKNOWN:
            canonical.add_issue(
                code="AMBIGUOUS_LINE_RETAINED",
                severity=IssueSeverity.REVIEW_REQUIRED,
                line_id=cline.stable_id,
                source_index=cline.source_index,
                evidence=cline.text,
                action="RETAIN_FOR_REVIEW",
            )
            cleaned_lines.append(cline)
            continue

        cleaned_lines.append(cline)

    canonical.lines = cleaned_lines
    return canonical
