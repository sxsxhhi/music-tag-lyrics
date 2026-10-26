# -*- coding: utf-8 -*-
"""粤拼转换器 (Jyutping Converter) —— 基于 Rime 精炼词库与 OpenCC 简繁对齐。

功能：
1. 字符级/词组级粤语注音（无调粤拼，适应声学对齐空间）；
2. 基于最长匹配词组消歧多音字（如「重新」cung vs「重要」zung）；
3. 阿拉伯数字规范化（0-9 -> 粤语音节）；
4. 英文/非汉字片段保留与类型标注，保障 Enhanced LRC 逐字对齐不丢字。
"""

import os
import re
import unicodedata
from typing import Dict, List, Tuple, Optional, Any

# 数字 0-9 规范粤拼读音（无调）
CANTO_DIGITS: Dict[str, str] = {
    "0": "ling",
    "1": "jat",
    "2": "ji",
    "3": "saam",
    "4": "sei",
    "5": "ng",
    "6": "luk",
    "7": "cat",
    "8": "baat",
    "9": "gau",
}

# 17 词 18 形核心语法虚词表（P0-2：包含繁简「諗/谂」两形，纳入高频否定词「唔」）
CANTONESE_CORE_GRAMMAR = frozenset([
    "嘅", "咗", "喺", "佢", "冇", "啲", "乜", "嚟",
    "睇", "諗", "谂", "瞓", "哋", "畀", "㗎", "嘢", "啫", "唔"
])

# 粤语特征口语字（P0-2：已剔除落/点/紧/返/企/屋/埋/迟等22个国语常用字）
CANTONESE_SLANG_CHARS = frozenset([
    "嘅", "咗", "喺", "佢", "冇", "啲", "乜", "嚟", "谂", "搵",
    "唔", "睇", "瞓", "哋", "畀", "啱", "諗", "揾",
    "㗎", "嘢", "啫", "掟",
])

# 粤歌书面意象 N-Gram 高频词（用于书面语粤歌路由加分）
CANTONESE_IMAGERY_WORDS = frozenset([
    "海阔天空", "海闊天空", "月半小夜曲", "富士山下", "一生何求",
    "刻不容缓", "刻不容緩", "半斤八两", "半斤八兩", "漫步人生路",
    "偏偏喜欢你", "偏偏喜歡你", "难得有情人", "難得有情人", "红日", "紅日",
    "心窝", "心窩", "冷风", "冷風", "这一刹", "這一剎", "这一生", "這一生",
    "偏偏", "倦了", "唏嘘", "唏噓", "依依不舍", "依依不捨", "街灯", "街燈",
    "红红", "紅紅", "留恋", "留戀", "茫茫", "漫漫", "消逝", "刹那", "剎那",
    "凄美", "淒美", "离别", "離別", "相聚", "分秒", "千般", "万缕", "萬縷",
    "共聚", "重聚", "难分", "難分", "情义", "情義", "痴心", "癡心", "冷雨",
    "痛快", "旧梦", "舊夢", "情深", "可笑", "怨恨", "爱意", "愛意", "情话", "情話",
])

_CHARS_DICT: Optional[Dict[str, List[str]]] = None
_WORDS_DICT: Optional[Dict[str, List[str]]] = None
_MAX_WORD_LEN: int = 4


# P1-10: 常用二字词消歧表（过渡版，覆盖「还会/说话/还是/发财」等高频词）
COMMON_DISAMBIGUATION_WORDS = {
    "还会": ["waan", "wui"],
    "還會": ["waan", "wui"],
    "还是": ["waan", "si"],
    "還是": ["waan", "si"],
    "还要": ["waan", "jiu"],
    "還要": ["waan", "jiu"],
    "说话": ["syut", "waa"],
    "說話": ["syut", "waa"],
    "发财": ["faat", "coi"],
    "發財": ["faat", "coi"],
    "开会": ["hoi", "wui"],
    "開會": ["hoi", "wui"],
    "不会": ["bat", "wui"],
    "不會": ["bat", "wui"],
    "唔会": ["m", "wui"],
    "唔會": ["m", "wui"],
}

# P1-10: 单字常用读音优先级调整（字典单字第一项调整为通用音）
COMMON_SINGLE_CHAR_PRIORITY = {
    "会": "wui",
    "會": "wui",
    "说": "syut",
    "說": "syut",
    "还": "waan",
    "還": "waan",
    # v1.5.1-A4: 繁体字条读音正确、简体字条首项被词频带偏，逐字校准。
    # 证据：词典 種→['zung'] 而 种→['cung','zung']；各種→['gok','zung']。
    "种": "zung",
    # 台→['ji','toi']：歌词里的「台」几乎全是 tái（舞台/电台/平台）= toi4
    "台": "toi",
    "臺": "toi",
    "檯": "toi",
    # 著→['zoek','zyu'] 而 着→['ziu','zoek']：着凉→['zoek','loeng'] 可佐证
    "着": "zoek",
    # 行→['haang','hang','hong']：书面语「行走/进行」读 hang4，行街 才是 haang4
    "行": "hang",
}

# v1.5.1-A4: 读音覆盖表。有些简体字在词典里**根本没有正确读音**
# （叶→['hip'] 而非 jip6），改优先级救不了，只能直接指定。
SINGLE_CHAR_READING_OVERRIDE = {
    "叶": "jip",   # 葉→['jip','sip']；叶子→['jip','zi'] 可佐证
}

# v1.5.1-A3: 标点字符集（NFKC 展开后若全在此集内，仍按一个标点 token 处理）
_PUNCT_CHARS = frozenset(
    ",.!?;:'\"-()[]{}，。！？；：“”‘’（）【】、~·…—–《》〈〉「」『』"
)


def _load_dictionary_if_needed() -> None:
    """按需加载纯文本粤拼词库 (core/data/jyutping_dict.tsv)。"""
    global _CHARS_DICT, _WORDS_DICT, _MAX_WORD_LEN
    if _CHARS_DICT is not None and _WORDS_DICT is not None:
        return

    chars: Dict[str, List[str]] = {}
    words: Dict[str, List[str]] = {}
    max_len = 1

    # 定位数据文件：优先同模块 data 目录，其次查找相对路径
    cur_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(cur_dir, "..", "data", "jyutping_dict.tsv"),
        os.path.join(cur_dir, "data", "jyutping_dict.tsv"),
        os.path.abspath(os.path.join(cur_dir, "..", "..", "core", "data", "jyutping_dict.tsv")),
    ]
    dict_path = None
    for c in candidates:
        if os.path.isfile(c):
            dict_path = c
            break

    if not dict_path:
        # 兜底：如果数据文件尚未生成，初始化为空
        _CHARS_DICT = {}
        _WORDS_DICT = {}
        _MAX_WORD_LEN = 1
        return

    with open(dict_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            tag, key, val = parts[0], parts[1], parts[2]
            if tag == "C":
                chars[key] = val.split(" ")
            elif tag == "W":
                jps = val.split(" ")
                words[key] = jps
                if len(key) > max_len:
                    max_len = len(key)

    # P1-10: 注入常用二字词消歧表
    words.update(COMMON_DISAMBIGUATION_WORDS)
    for k in COMMON_DISAMBIGUATION_WORDS:
        if len(k) > max_len:
            max_len = len(k)

    # P1-10: 调整单字常用音到第一位
    for ch, prio in COMMON_SINGLE_CHAR_PRIORITY.items():
        if ch in chars and prio in chars[ch]:
            cur_list = [p for p in chars[ch] if p != prio]
            chars[ch] = [prio] + cur_list

    _CHARS_DICT = chars
    _WORDS_DICT = words
    _MAX_WORD_LEN = max(max_len, 4)


def is_cantonese_slang(char_or_word: str) -> bool:
    """是否包含典型口语粤字。"""
    return any(c in CANTONESE_SLANG_CHARS for c in char_or_word)


def to_jyutping(text: str) -> List[Dict[str, Any]]:
    """将文本切分为 token 列表，并转换为无调粤拼。
    
    返回字典结构：
    [
        {
            "char": "海",
            "jyutping": "hoi",
            "type": "hanzi",
            "is_disambiguated": True,
        },
        ...
    ]
    """
    _load_dictionary_if_needed()
    chars_dict = _CHARS_DICT or {}
    words_dict = _WORDS_DICT or {}

    tokens: List[Dict[str, Any]] = []
    i = 0
    n = len(text)

    # v1.5.1-A3: NFKC 只用于**查表/判定**，绝不用于输出。
    # 旧版把整段文本 NFKC 后同时用作输出，于是 `…`→`...`（1 字变 3 字）、
    # 全角字母→半角，token["char"] 与原文不再一致，converter 的一致性校验
    # （converter.py:239-243）判为「逐字文本 != 行文本」后**整行降级为行级**，
    # 用户侧只看到一行 warning，逐字全丢。这里改成逐字符按需归一。
    def _nfkc(s: str) -> str:
        return unicodedata.normalize("NFKC", s)

    def _is_ascii_word_char(c: str) -> bool:
        return len(c) == 1 and (("a" <= c <= "z") or ("A" <= c <= "Z") or c == "'")

    while i < n:
        raw_ch = text[i]
        ch = _nfkc(raw_ch)          # 仅供判定与查表

        # 1. 阿拉伯数字（含全角 １）
        if len(ch) == 1 and ch.isdigit():
            tokens.append({
                "char": raw_ch,
                "jyutping": CANTO_DIGITS.get(ch, "ling"),
                "type": "digit",
                "is_disambiguated": True,
            })
            i += 1
            continue

        # 2. 连续英文字母/单词（含全角字母 Ａ → A）
        if len(ch) == 1 and (("a" <= ch <= "z") or ("A" <= ch <= "Z")):
            start_eng = i
            while i < n and _is_ascii_word_char(_nfkc(text[i])):
                i += 1
            tokens.append({
                "char": text[start_eng:i],   # 保留原始字形（可能是全角）
                "jyutping": "",  # 英文无直接粤拼，交由后续启发式对齐
                "type": "latin",
                "is_disambiguated": True,
            })
            continue

        # 3. 空白字符或常见标点符号。
        # `…`、`——`、`《》` 这类经 NFKC 会展开成多个 ASCII 标点的字符，
        # 只要展开后全是标点，也按「一个标点 token」处理，保持原文长度。
        if raw_ch.isspace() or ch in _PUNCT_CHARS or (
            len(ch) > 1 and all(c in _PUNCT_CHARS for c in ch)
        ):
            tokens.append({
                "char": raw_ch,
                "jyutping": "",
                "type": "punct",
                "is_disambiguated": True,
            })
            i += 1
            continue

        # 4. 汉字：前向最长匹配词组消歧。
        # 归一化若改变了长度（如 `㍿`→`株式会社`），无法按原文切片对齐，跳过该窗口。
        matched_word = False
        for wlen in range(min(_MAX_WORD_LEN, n - i), 1, -1):
            sub_raw = text[i : i + wlen]
            sub = _nfkc(sub_raw)
            if len(sub) != wlen:
                continue
            if sub in words_dict:
                jps = words_dict[sub]
                for k, c in enumerate(sub):
                    jp = jps[k] if k < len(jps) else ""
                    tokens.append({
                        "char": sub_raw[k],
                        "jyutping": jp,
                        "type": "hanzi",
                        "is_disambiguated": True,
                    })
                i += wlen
                matched_word = True
                break

        if matched_word:
            continue

        # 5. 单汉字查表
        if ch in chars_dict:
            p_list = chars_dict[ch]
            # v1.5.1-A4: 优先查人工校准表（含「字典里根本没有该读音」的字，如 叶→jip）
            primary = SINGLE_CHAR_READING_OVERRIDE.get(ch)
            if primary is None:
                primary = p_list[0] if p_list else ""
                primary = COMMON_SINGLE_CHAR_PRIORITY.get(ch, primary)
            is_poly = len(p_list) > 1
            tokens.append({
                "char": raw_ch,
                "jyutping": primary,
                "type": "hanzi",
                "is_disambiguated": not is_poly,  # 多音字单查打非消歧
            })
            i += 1
            continue

        # 6. 未知字符兜底（保留原始字形，不能写 NFKC 结果，否则破坏文本保真）
        tokens.append({
            "char": raw_ch,
            "jyutping": "",
            "type": "unknown",
            "is_disambiguated": False,
        })
        i += 1

    return tokens


def text_to_jyutping_tokens(text: str) -> List[str]:
    """提取纯声学 target 序列（仅保留含非空粤拼的音节，如汉字与数字）。"""
    parsed = to_jyutping(text)
    return [t["jyutping"] for t in parsed if t.get("jyutping")]


def get_all_syllables() -> List[str]:
    """获取字典中所有无调粤拼音节（有序列表，实测 703 类）。"""
    _load_dictionary_if_needed()
    syllables = set()
    for jps in (_CHARS_DICT or {}).values():
        for jp in jps:
            if jp:
                syllables.add(jp)
    return sorted(list(syllables))


def get_token_disambiguation_ratio(tokens: List[Dict[str, Any]]) -> float:
    """计算一行内汉字/数字多音字消歧置信度比率。"""
    hanzi_tokens = [t for t in tokens if t.get("type") in ("hanzi", "digit")]
    if not hanzi_tokens:
        return 1.0
    disambiguated = sum(1 for t in hanzi_tokens if t.get("is_disambiguated"))
    return disambiguated / len(hanzi_tokens)
