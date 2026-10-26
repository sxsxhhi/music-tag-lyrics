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

# 粤语特征口语字（用于语言检测与加分）
CANTONESE_SLANG_CHARS = frozenset([
    "嘅", "咗", "喺", "佢", "冇", "啲", "乜", "嚟", "谂", "搵",
    "唔", "睇", "瞓", "哋", "畀", "啱", "紧", "緊", "諗", "揾",
    "睇", "㗎", "啰", "囉", "嘞", "喇", "嘢", "啫", "咪", "梗",
    "掂", "掟", "撩", "揸", "掹", "氹", "拗", "嗌", "呃", "拗",
    "返", "屋", "企", "企", "点", "點", "迟", "遲", "埋", "落",
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
    norm_text = unicodedata.normalize("NFKC", text)
    i = 0
    n = len(norm_text)

    while i < n:
        ch = norm_text[i]

        # 1. 阿拉伯数字
        if ch.isdigit():
            tokens.append({
                "char": ch,
                "jyutping": CANTO_DIGITS.get(ch, "ling"),
                "type": "digit",
                "is_disambiguated": True,
            })
            i += 1
            continue

        # 2. 连续英文字母/单词 (ASCII 字母)
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            start_eng = i
            while i < n and (("a" <= norm_text[i] <= "z") or ("A" <= norm_text[i] <= "Z") or norm_text[i] == "'"):
                i += 1
            eng_word = norm_text[start_eng:i]
            tokens.append({
                "char": eng_word,
                "jyutping": "",  # 英文无直接粤拼，交由后续启发式对齐
                "type": "latin",
                "is_disambiguated": True,
            })
            continue

        # 3. 空白字符或常见标点符号
        if ch.isspace() or ch in ",.!?;:'\"-()[]{}，。！？；：“”‘’（）【】、~·":
            tokens.append({
                "char": ch,
                "jyutping": "",
                "type": "punct",
                "is_disambiguated": True,
            })
            i += 1
            continue

        # 4. 汉字：前向最长匹配词组消歧
        matched_word = False
        for wlen in range(min(_MAX_WORD_LEN, n - i), 1, -1):
            sub = norm_text[i : i + wlen]
            if sub in words_dict:
                jps = words_dict[sub]
                for k, c in enumerate(sub):
                    jp = jps[k] if k < len(jps) else ""
                    tokens.append({
                        "char": c,
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
            primary = p_list[0] if p_list else ""
            is_poly = len(p_list) > 1
            tokens.append({
                "char": ch,
                "jyutping": primary,
                "type": "hanzi",
                "is_disambiguated": not is_poly,  # 多音字单查打非消歧
            })
            i += 1
            continue

        # 6. 未知字符兜底
        tokens.append({
            "char": ch,
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
    """获取字典中所有无调粤拼音节（有序列表，共 729 类）。"""
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
