# -*- coding: utf-8 -*-
"""歌词校验：单调性、Intro/Outro、双语行配对。外语 GPU 打轴前用 pair_plain_bilingual_lyrics 只留原文。"""

import copy
import re
from typing import Dict, Any, Tuple, List, Optional

from core.utils.text_utils import is_translation_line, LRC_META_LINE_RE
from core.utils.time_utils import strip_timing_tags

# Preamble credit keywords for whitelist protection
# v1.5.1: 正则本体上移到 core/utils/text_utils.CREDIT_KEYWORD_RE（单一出处），
# 此前 validator 与 text_utils 各持一份，语义已经在漂移。
# 语义要点（P1-8 / P1-1）：
#   · 单字 "词/曲" 必须带冒号，否则 "词不达意"、"曲终人散" 会被误判
#   · 多字关键词必须跟冒号/空白/行尾，否则 "鼓楼"、"键盘上的泪"、"作词人" 会被误判
from core.utils.text_utils import CREDIT_KEYWORD_RE as CREDIT_KEYWORD_PATTERN  # noqa: F401


def is_credit_line(line_text: str) -> bool:
    # P0-2: 时间戳剥离统一走 time_utils.strip_timing_tags（原先这里两行内联正则，
    # 与别处的实现已经漂移：只支持两位分钟，>99 分钟的歌词行会剥不掉时间戳）。
    clean = strip_timing_tags(line_text)
    return bool(CREDIT_KEYWORD_PATTERN.search(clean))

def check_acoustic_naturalness(words: List[Dict[str, Any]]) -> Tuple[bool, float]:
    """
    Checks whether word durations conform to natural acoustic human singing (>= 100ms).
    Returns: (is_healthy, tiny_ratio)

    P1-3: 阈值改为 < 80ms。旧版用 <= 80ms，而修复逻辑的下限恰好是 80ms，
    导致「修完仍然报警」的自相矛盾。

    v1.5.1-N17: 增加「贴齐豁免」。粤语 CTC 的 _MIN_CHAR_S = 60ms，快嘴行里
    大量字恰好 60ms 且与下一字首尾相接（间隙 ≤ _PACKED_GAP_MS），这是**字距
    挤出来的结构性压缩**，不是机器伪时间戳。旧口径会把每首粤语快歌都刷成
    「Natural Acoustic Alert」，变成恒噪声，反过来掩盖真正的伪时间。
    """
    if not words or len(words) < 2:
        return True, 0.0
        
    tiny_count = 0
    for k, w in enumerate(words):
        dur = w.get("duration", 0)
        if dur >= 80:  # Machine dummy compression threshold
            continue
        # 结构性压缩豁免：紧贴下一字（无可分配间隙）的短字不算伪时间
        nxt = words[k + 1].get("start") if k + 1 < len(words) else None
        if isinstance(nxt, int) and isinstance(w.get("end"), int):
            if 0 <= nxt - w["end"] <= _PACKED_GAP_MS:
                continue
        tiny_count += 1
            
    ratio = tiny_count / len(words)
    return (ratio <= 0.15), ratio

def strip_translation_lines_from_parsed(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pairs interleaved translation lines from legacy dual-track LRCs into modern ELRC format,
    attaching the translation to the preceding singing line without causing screen jumping.
    """
    lines = data.get("lines", [])
    if not lines:
        return data
        
    cleaned_lines = []
    for line in lines:
        text = line.get("text", "").strip()
        words = line.get("words", [])
        
        # Keep credit lines
        if is_credit_line(text):
            cleaned_lines.append(line)
            continue
            
        # If line has words -> keep
        if words:
            cleaned_lines.append(line)
            continue
            
        # Check translation line using shared text_utils
        if is_translation_line(text, has_words=bool(words)):
            # Attach to previous singing line if available
            if cleaned_lines and not cleaned_lines[-1].get("translation"):
                cleaned_lines[-1]["translation"] = text
            continue
        else:
            cleaned_lines.append(line)
            
    return {**data, "lines": cleaned_lines}


# 元数据行正则统一走 text_utils.LRC_META_LINE_RE（A2 去重，与 plain_lyric_search 共用）
# P0-4: 分段式切分。旧版 ^…(.+)$ 的 (.+) 贪婪吃到行尾，导致
#   "作词 : 张三 作曲 : 李四" -> ('张三 作曲 : 李四', '')、作曲整体丢失，
#   垃圾串还会写进 TCOM 标签。改为先定位每个「关键词:」标记，再取到下一个标记之前。
_CREDIT_TOKEN_RE = re.compile(
    r"(作词作曲|作詞作曲|词曲|作词|作詞|作曲|Lyrics|Written|Composed)"
    r"(?:\s+by)?\s*[:：]"
    r"|(词|曲)\s*[:：]",
    re.IGNORECASE,
)
_CREDIT_BOTH = {"作词作曲", "作詞作曲", "词曲"}
_CREDIT_LYRICIST = {"作词", "作詞", "词", "lyrics", "written"}
_CREDIT_COMPOSER = {"作曲", "曲", "composed"}

# v1.5.1-A2: 行间/行长的三个下限。铁律 19「行间零重叠」绝对优先，
# 铁律 4 的 500ms 行长下限降级为 best-effort——两者冲突时宁可缩短前一行。
_MIN_LINE_MS = 500        # 行长舒适下限（best-effort）
_MIN_LINE_HARD_MS = 50    # 无解时的最小行时长
_LINE_GAP_MS = 50         # 行间最小间隙（与 acoustic_quality.LINE_GAP_MS 同值）
_MIN_WORD_MS = 100        # R2：单字下限，行尾被压时末字回移保住这个宽度
# N17: 与下一字间隙 <= 此值的短字视为「字距挤出来的结构性压缩」，不计入伪时间
_PACKED_GAP_MS = 10


def split_credit_pairs(clean: str) -> List[Tuple[str, str]]:
    """把一行里所有「关键词: 值」切成 [(kind, value), …]。

    值取到下一个关键词标记之前，并剥掉尾随的 / 、 , ， | 与空白。
    "作词 : 张三 作曲 : 李四" -> [("作词", "张三"), ("作曲", "李四")]
    """
    tokens = list(_CREDIT_TOKEN_RE.finditer(clean or ""))
    pairs: List[Tuple[str, str]] = []
    for i, m in enumerate(tokens):
        # 双组正则：group(1) 是多字关键词，group(2) 是单字 词/曲
        kind = (m.group(1) or m.group(2) or "").strip()
        start = m.end()
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(clean)
        value = clean[start:end].strip().strip("/、,，|").strip()
        if kind and value:
            pairs.append((kind, value))
    return pairs


def extract_lyricist_composer(text: str) -> Tuple[str, str]:
    """从歌词前若干行提取作词/作曲。单字词/曲必须带冒号。人名分隔交给 normalize_artist_string。"""
    from scripts.tag_editor import normalize_artist_string

    lyricists: List[str] = []
    composers: List[str] = []
    seen = 0
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or LRC_META_LINE_RE.match(line):
            continue
        clean = strip_timing_tags(line).strip()
        if not clean:
            continue
        seen += 1
        if seen > 12:
            break
        for kind, value in split_credit_pairs(clean):
            names = normalize_artist_string(value)
            if not names:
                continue
            key = kind.lower()
            if kind in _CREDIT_BOTH:
                lyricists.append(names)
                composers.append(names)
            elif key in _CREDIT_LYRICIST:
                lyricists.append(names)
            elif key in _CREDIT_COMPOSER:
                composers.append(names)

    def _uniq(items: List[str]) -> str:
        out: List[str] = []
        for item in items:
            if item not in out:
                out.append(item)
        return "/".join(out)

    return _uniq(lyricists), _uniq(composers)


def pair_plain_bilingual_lyrics(text: str) -> Tuple[str, List[Optional[str]]]:
    """
    把夹杂中文译文的纯文本拆成「原文行」+ 平行译文列表。
    GPU 只应对原文 align；译文挂回 translation 字段，保持行级时间轴。
    """
    originals: List[str] = []
    translations: List[Optional[str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or LRC_META_LINE_RE.match(line):
            continue
        clean = strip_timing_tags(line).strip() or line
        if is_credit_line(clean):
            originals.append(clean)
            translations.append(None)
            continue
        if is_translation_line(clean, has_words=False):
            if originals and translations[-1] is None:
                translations[-1] = clean
            continue
        originals.append(clean)
        translations.append(None)
    return "\n".join(originals), translations


def _clamp_line_end(line: Dict[str, Any], new_end: int) -> None:
    """把一行的 end 压到 new_end，并同步压缩词条、维持 line.end == words[-1].end。

    v1.5.1-A2: 只在真正的行间重叠时调用（罕见路径），优先正确性而非简洁。
    """
    line_start = int(line.get("start", 0))
    new_end = max(line_start + 1, int(new_end))
    words = line.get("words") or []
    if words:
        last_end = words[-1].get("end")
        last_end = int(last_end) if isinstance(last_end, int) else new_end
        span = max(1, last_end - line_start)
        target = max(1, new_end - line_start)
        if target < span:
            for w in words:
                for key in ("start", "end"):
                    v = w.get(key)
                    if isinstance(v, int):
                        rel = max(0, min(span, v - line_start))
                        w[key] = line_start + rel * target // span
            # 正向单调兜底：压缩取整可能让相邻字重叠
            prev_end = line_start
            for w in words:
                if not isinstance(w.get("start"), int) or not isinstance(w.get("end"), int):
                    continue
                w["start"] = min(max(w["start"], prev_end), max(line_start, new_end - 1))
                if w["end"] <= w["start"]:
                    w["end"] = min(new_end, w["start"] + 1)
                w["duration"] = max(1, w["end"] - w["start"])
                prev_end = w["end"]
        # 恒等式：行 end ≡ 末字 end；行尾被压时末字起点回移保住最小字宽
        words[-1]["end"] = new_end
        if isinstance(words[-1].get("start"), int) and words[-1]["start"] >= new_end:
            words[-1]["start"] = max(line_start, new_end - _MIN_WORD_MS)
            if words[-1]["start"] >= new_end:
                words[-1]["start"] = max(line_start, new_end - 1)
        if isinstance(words[-1].get("start"), int):
            words[-1]["duration"] = max(1, new_end - words[-1]["start"])
    line["end"] = new_end
    line["duration"] = max(1, new_end - line_start)


def _enforce_zero_overlap(lines: List[Dict[str, Any]], warnings: List[str]) -> None:
    """终局零重叠钳制（铁律 19 绝对优先，500ms 行下限让位）。

    v1.5.1-A2: 幂等 —— 对已无重叠的输入再跑一次不产生任何改动、不产生 warning
    （process_song 会对同一份数据做二次校验，非幂等会二次偏移）。
    """
    for i in range(len(lines) - 1):
        cur, nxt = lines[i], lines[i + 1]
        cur_start = int(cur.get("start", 0))
        nxt_start = int(nxt.get("start", 0))

        # 起点已碰撞（两行起点相距 ≤ 行间间隙）：唯一允许改后行起点的情形
        if nxt_start - _LINE_GAP_MS <= cur_start:
            new_start = cur_start + _LINE_GAP_MS + _MIN_LINE_HARD_MS
            delta = new_start - nxt_start
            nxt["start"] = new_start
            # 整行平移：words 与 end 必须同步，否则 line.end != words[-1].end
            if isinstance(nxt.get("end"), int):
                nxt["end"] += delta
            for w in nxt.get("words") or []:
                for key in ("start", "end"):
                    if isinstance(w.get(key), int):
                        w[key] += delta
            nxt_start = new_start
            warnings.append(
                f"Line {i + 2} start pushed to {new_start}ms (zero-overlap priority)"
            )

        cap = nxt_start - _LINE_GAP_MS
        if int(cur.get("end", 0)) > cap:
            old_end = int(cur.get("end", 0))
            short = cap - cur_start
            warnings.append(
                f"Line {i + 1} shortened {old_end}ms -> {cap}ms "
                f"(zero-overlap priority, {short}ms < {_MIN_LINE_MS}ms floor)"
            )
            _clamp_line_end(cur, cap)


def validate_and_fix_lyrics(data: Dict[str, Any], audio_duration_ms: int = 0, is_non_zh: bool = False) -> Tuple[bool, Dict[str, Any], List[str]]:
    """
    Validates and auto-repairs minor monotonicity deviations in parsed lyrics.
    Enforces Natural Acoustic Gate, Intro Gate, and Outro protection.
    Returns: (is_valid, repaired_data, warnings_list)
    """
    if is_non_zh:
        data = strip_translation_lines_from_parsed(data)
        
    # P1-3: 深拷贝，避免修时间轴时把调用方手里的 data 一起改脏
    # （旧版 list() 只是浅拷贝，line/word dict 仍与调用方共享）。
    lines = copy.deepcopy(data.get("lines", []))
    warnings = []
    
    if not lines:
        return False, data, ["Empty lyrics track"]

    last_line_start = -1
    last_line_end = -1
    first_sing_start = None

    for i, line in enumerate(lines):
        line_start = line.get("start", 0)
        line_text = line.get("text", "").strip()

        # Track first sung lyric start (ignoring credit preamble)
        if first_sing_start is None and not is_credit_line(line_text):
            first_sing_start = line_start

        # 0. 行间重叠处理已移到主循环之后的「终局零重叠钳制」统一执行。
        # v1.5.1-A2: 旧版在这里用 max(last_line_start + 500, line_start - 50) 收拢前一行，
        # 当两行间隔不足 550ms 时该 max 会反超 line_start - 50，**重新制造**
        # 行间重叠（实测两行 0/200ms、字 60ms 时会残留 350ms 重叠）。
        # 现在主循环只负责单调性与时长归一，零重叠由 _enforce_zero_overlap 终局保证。

        # 1. Check line monotonic progression
        if line_start < last_line_start:
            warnings.append(f"Line {i} timestamp inversion: {line_start}ms < previous {last_line_start}ms. Auto-correcting.")
            line["start"] = last_line_start + 100
            line_start = line["start"]
            
        last_line_start = line_start
        
        # 2. Check word monotonic progression & acoustic naturalness
        words = line.get("words", [])
        last_word_start = line_start
        for k, w in enumerate(words):
            w_start = w.get("start", 0)
            if w_start < last_word_start:
                warnings.append(f"Line {i} Word {k} timestamp inversion: {w_start}ms < {last_word_start}ms. Auto-correcting.")
                w["start"] = last_word_start
                w_start = last_word_start

            # P1-3: 计算时长时优先沿用源里真实的 end（只给 start/end 的候选
            # 旧版会被 w.get("duration", 150) 改成 150ms，3000ms 的词直接丢精度）；
            # 兜底下限抬到 100ms，与 check_acoustic_naturalness 的 <80ms 判定错开，
            # 避免「修完恰好等于 80 仍在报警」的自相矛盾。
            raw_end = w.get("end")
            if isinstance(raw_end, int) and raw_end > w_start:
                w_dur = raw_end - w_start
            elif w.get("duration"):
                w_dur = int(w["duration"])
            else:
                w_dur = 100
            w_dur = max(100, w_dur)

            # P1-3: 与下一个词的起点钳制，避免拉长后压到下一词（旧版 40ms 词拉到 80ms
            # 会越过下一词 start，产生时间轴重叠）。
            w_end = w_start + w_dur
            if k + 1 < len(words):
                nxt_start = words[k + 1].get("start")
                if isinstance(nxt_start, int) and nxt_start > w_start:
                    w_end = min(w_end, nxt_start)

            w["start"] = w_start
            w["duration"] = w_end - w_start
            w["end"] = w_end
            last_word_start = w_start

        # Natural Acoustic Gate
        if words:
            healthy, ratio = check_acoustic_naturalness(words)
            if not healthy:
                warnings.append(f"Line {i} Natural Acoustic Alert: {ratio*100:.1f}% words < 80ms.")

        if words:
            # v1.5.1-A2: 500ms 行长下限体现在**末字尾巴**上，而不是只抬高 line.end。
            # 旧版只写 line["end"]，导致 line.end != words[-1].end，而 to_spl 的
            # <tend> 取末字 end（converter.py:169），两者不一致时播放器流光会提前收束。
            # 同时把「下一行起点 - 行间间隙」作为硬上界前置进来（铁律 19 优先），
            # 这样首轮即收敛，终局钳制不会再有活儿干，二次校验不产生重复告警。
            prev_end = line.get("end") if isinstance(line.get("end"), int) else None
            next_start = lines[i + 1].get("start") if i + 1 < len(lines) else None
            cap = int(next_start) - _LINE_GAP_MS if isinstance(next_start, int) else None
            # cap 太小说明两行起点本身就撞了（如都为 0），此时不该把本行压成 1ms
            # 的闪帧；放弃 cap，交给终局钳制去推后行起点，本行保留最小行时长。
            if cap is not None and cap < line_start + _MIN_LINE_HARD_MS:
                cap = None

            floor_end = line_start + _MIN_LINE_MS
            last_w = words[-1]
            tail = max(floor_end, last_w["end"])
            if cap is not None:
                tail = min(tail, cap)
            last_w["end"] = tail
            if last_w["start"] >= tail:
                # R2：行尾被压时末字起点一并回移，保住最小字宽（旧版行为，必须保留）
                last_w["start"] = max(line_start, tail - _MIN_WORD_MS)
                if last_w["start"] >= tail:
                    last_w["start"] = max(line_start, tail - 1)
            last_w["duration"] = max(1, tail - last_w["start"])
            line["end"] = tail
            line["duration"] = max(1, tail - line_start)
            if prev_end is not None and prev_end != line["end"]:
                short = line["end"] - line_start
                if cap is not None and prev_end > cap:
                    warnings.append(
                        f"Line {i + 1} overlap ({prev_end - cap}ms). Auto-trimming previous "
                        f"line {prev_end}ms -> {line['end']}ms "
                        f"(zero-overlap priority, {short}ms < {_MIN_LINE_MS}ms floor)"
                    )
                elif short < _MIN_LINE_MS:
                    warnings.append(
                        f"Line {i + 1} shortened {prev_end}ms -> {line['end']}ms "
                        f"(zero-overlap priority, {short}ms < {_MIN_LINE_MS}ms floor)"
                    )
        else:
            line["end"] = line_start + line.get("duration", 3000)
        last_line_end = line["end"]

    # 2.9 终局零重叠钳制（铁律 19 绝对优先，幂等；主循环已前置上界，此处是安全网）
    _enforce_zero_overlap(lines, warnings)

    # 3. Intro Gate: Check unusually delayed singing start
    if audio_duration_ms > 90000 and first_sing_start is not None:
        if first_sing_start > 32000:
            warnings.append(f"Intro Gate Alert: First lyric starts unusually late ({first_sing_start / 1000.0:.2f}s). Please verify if intro stage banter or cut exists.")

    # 4. Outro check: Musical instrument solo differentiation
    if audio_duration_ms > 0 and lines:
        last_lyric_ms = lines[-1].get("end", lines[-1].get("start", 0))
        tail_gap = audio_duration_ms - last_lyric_ms
        if tail_gap > 35000:
            warnings.append(f"Instrumental outro detected ({tail_gap / 1000.0:.1f}s solo). Preserving natural musical silence.")

    return True, {**data, "lines": lines}, warnings
