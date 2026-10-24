# -*- coding: utf-8 -*-
"""歌词标题、艺人与版本特征的归一化与文本相似度打分。

双轨分工（勿合并）：本模块是**严格派**——NFKC 归一化键全等匹配，服务
Apple TTML/AMLL 索引/iTunes 高精链路防串歌；
模糊派见 core/utils/version_guard.py（子串/互串，服务酷狗/网易等噪声接口）。
"""

import re
import unicodedata
from typing import Set, List, Optional, FrozenSet

VERSION_KEYWORDS = {
    "live", "现场", "演唱会", "remaster", "重制", "demo", "remix", "混音",
    "acoustic", "不插电", "instrumental", "cover", "翻唱", "ver", "deluxe",
    "mono", "explicit", "clean"
}

# 版本互斥硬拒 token（A1 去重）：apple_ttml 版本门与 amll_index 硬拒 3 共用，
# 与 VERSION_KEYWORDS 是子集关系——这里只列「录音室/Live/Demo 互斥必须拒」的词。
VERSION_EXCLUSION_TOKENS = frozenset({"live", "现场", "演唱会", "remix", "混音", "demo"})

GENERIC_ARTISTS = {
    "various artists", "群星", "合辑", "未知", "unknown", "va"
}

GENERIC_ALBUMS = {
    "greatest hits", "the best of", "best of", "hits",
    "精选", "精选集", "新曲+精选", "最热门", "热门",
    "single", "singles", "ep", "soundtrack", "ost",
}

_PUNCT_RE = re.compile(r"""[《》「」""''·…、，。！？；：,.!?;:()【】\[\]\-–—_/\\]""")
_FEAT_RE = re.compile(r"""\s*(?:\(|\[|\{)?\s*(?:feat\.?|ft\.?)\s+[^)\]\}]+(?:\)|\]|\})?""", re.IGNORECASE)
_ARTIST_SPLIT_RE = re.compile(r"""[/&,、×+]|\b(?:feat\.?|ft\.?|with|vs\.?)\b""", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"""\s+""")


def nfkc(s: str) -> str:
    """NFKC 归一化，折叠全角与非常规空白，转小写。"""
    if not s:
        return ""
    norm = unicodedata.normalize("NFKC", str(s))
    norm = norm.replace("\xa0", " ").strip()
    return _WHITESPACE_RE.sub(" ", norm).lower()


def strip_punct(s: str) -> str:
    """剔除常见中英文标点符号与特殊连接符。"""
    if not s:
        return ""
    return _PUNCT_RE.sub(" ", s).strip()


def version_tokens(s: str) -> Set[str]:
    """提取字符串中包含的版本标识关键词集合。"""
    cleaned = nfkc(s)
    # 按词边界与标点切分
    parts = re.split(r"""[\s_/\-()[\]{}《》「」·,.!?;:]+""", cleaned)
    tokens = set()
    for p in parts:
        p_clean = p.strip()
        if p_clean in VERSION_KEYWORDS:
            tokens.add(p_clean)
        # 兼容例如 "live" 在长括号里，如 (Live in Tokyo 2020)
        for kw in VERSION_KEYWORDS:
            if kw in p_clean and len(kw) >= 2:
                # 英文关键词要求边界匹配，中文直接包含
                if kw.isascii():
                    if re.search(rf"\b{kw}\b", p_clean):
                        tokens.add(kw)
                else:
                    tokens.add(kw)
    return tokens


def norm_title(s: str, keep_version: bool = True) -> str:
    """标题归一化：剥离 feat. 段落；根据 keep_version 保留或剔除版本特征。"""
    if not s:
        return ""
    cur = nfkc(s)
    # 剔除 feat. 段
    cur = _FEAT_RE.sub("", cur)

    if not keep_version:
        # 剔除括号中的版本信息
        def _strip_version_bracket(m):
            content = m.group(0)
            if any(kw in content for kw in VERSION_KEYWORDS):
                return ""
            return content

        cur = re.sub(r"""\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】""", _strip_version_bracket, cur)
        # 剔除游离的版本词
        for kw in sorted(VERSION_KEYWORDS, key=len, reverse=True):
            if kw.isascii():
                cur = re.sub(rf"\b{kw}\b", "", cur)
            else:
                cur = cur.replace(kw, "")

    cur = strip_punct(cur)
    return _WHITESPACE_RE.sub(" ", cur).strip()


def artist_token_list(s: str) -> List[str]:
    """切分多艺人、去通用词并截取前 6 个有效 token，保持出现顺序。"""
    if not s:
        return []
    cleaned = nfkc(s)
    raw_tokens = _ARTIST_SPLIT_RE.split(cleaned)
    tokens: List[str] = []
    for rt in raw_tokens:
        t = strip_punct(rt)
        t = _WHITESPACE_RE.sub(" ", t).strip()
        if not t or t in GENERIC_ARTISTS:
            continue
        if t not in tokens:
            tokens.append(t)
    return tokens[:6]


def artist_tokens(s: str) -> FrozenSet[str]:
    """切分多艺人、去通用词并截取前 6 个有效 token。"""
    return frozenset(artist_token_list(s))


def primary_artist(s: str) -> str:
    """提取第一主艺人（名单出现顺序的第一位，不是 frozenset 哈希序）。"""
    toks = artist_token_list(s)
    return toks[0] if toks else ""


def norm_album(s: str) -> str:
    """专辑名归一化。"""
    if not s:
        return ""
    cur = strip_punct(nfkc(s))
    return _WHITESPACE_RE.sub(" ", cur).strip()
