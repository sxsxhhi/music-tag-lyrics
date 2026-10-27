# -*- coding: utf-8 -*-
"""语种与文本判定：汉字/假名/谚文、外语曲、译文行、占位词。"""

import re

# ---- v1.5.2-D1: 字符判定单一真相 ----------------------------------------
# 区间表是唯一真相，正则由表派生 —— 避免「表改了、正则没改」的二次漂移。
# 每个区间都取 Unicode 已分配区块；**不要**改回 0x20000-0x323AF 这类整片宽
# 区间：它会把 0x2EE5E-0x2F7FF 等未分配码位也判成汉字（v1.5.1 的 HANZI_RE
# 就是这个毛病），也让「漏了哪个扩展」无法从代码上看出来。
HANZI_RANGES = (
    (0x3400, 0x4DBF),    # CJK 扩展 A
    (0x4E00, 0x9FFF),    # CJK 统一表意文字（基本区）
    (0xF900, 0xFAFF),    # CJK 兼容表意文字
    (0x20000, 0x2A6DF),  # CJK 扩展 B
    (0x2A700, 0x2B73F),  # CJK 扩展 C
    (0x2B740, 0x2B81F),  # CJK 扩展 D
    (0x2B820, 0x2CEAF),  # CJK 扩展 E
    (0x2CEB0, 0x2EBEF),  # CJK 扩展 F
    (0x2EBF0, 0x2EE5D),  # CJK 扩展 I（Unicode 15.1，v1.5.1 唯一漏判区）
    (0x30000, 0x3134F),  # CJK 扩展 G
    (0x31350, 0x323AF),  # CJK 扩展 H
    (0x2F800, 0x2FA1F),  # CJK 兼容表意文字补充
)
KANA_RANGES = ((0x3040, 0x309F), (0x30A0, 0x30FF))   # 平假名 / 片假名（含长音符 ー）
HANGUL_RANGES = ((0xAC00, 0xD7AF),)                  # 谚文音节
FULLWIDTH_RANGE = (0xFF00, 0xFFEF)                   # 全角标点/字母（TTML 词边界用）
FULLWIDTH_RANGES = (FULLWIDTH_RANGE,)


def _ranges_to_class(ranges) -> str:
    """把区间表拼成正则字符类内容（供 re.compile 使用）。"""
    return "".join(f"{re.escape(chr(a))}-{re.escape(chr(b))}" for a, b in ranges)


def _in_ranges(ch: str, ranges) -> bool:
    """单字符是否落在任一闭区间内。空串/多字符传参一律 False（防崩溃）。"""
    if not isinstance(ch, str) or len(ch) != 1:
        return False
    o = ord(ch)
    return any(a <= o <= b for a, b in ranges)


def is_hanzi_char(ch: str) -> bool:
    """是否为汉字（基本区 + 扩展 A~I + 兼容区）。"""
    return _in_ranges(ch, HANZI_RANGES)


def is_kana_char(ch: str) -> bool:
    """是否为日文平/片假名。"""
    return _in_ranges(ch, KANA_RANGES)


def is_hangul_char(ch: str) -> bool:
    """是否为韩文谚文音节。"""
    return _in_ranges(ch, HANGUL_RANGES)


def is_fullwidth_char(ch: str) -> bool:
    """是否为全角字符（FF00-FFEF）。"""
    return _in_ranges(ch, FULLWIDTH_RANGES)


def is_cjk_char(ch: str) -> bool:
    """是否为「词间不加空格」的 CJK 字符：汉字 | 假名 | 谚文。"""
    return is_hanzi_char(ch) or is_kana_char(ch) or is_hangul_char(ch)


CJK_RE = re.compile("[" + _ranges_to_class(HANZI_RANGES + KANA_RANGES + HANGUL_RANGES) + "]")

# LRC 元数据行（[ti:] [ar:] [al:] [by:] [offset:]）——lyric_validator 与
# plain_lyric_search 共用同一份（A2 去重，两处正则曾逐字重复）。
LRC_META_LINE_RE = re.compile(r"^\[(ti|ar|al|by|offset):", re.IGNORECASE)
# 汉字判定统一到 CJK 统一表意全集（由 HANZI_RANGES 派生，含扩展 I）
HANZI_RE = re.compile("[" + _ranges_to_class(HANZI_RANGES) + "]")
KANA_RE = re.compile("[" + _ranges_to_class(KANA_RANGES) + "]")
HANGUL_RE = re.compile("[" + _ranges_to_class(HANGUL_RANGES) + "]")

# 故意的**窄口径**：只用于「这段文本像不像歌词」的体量启发式
# （plain_lyric_search 的 ≥20 汉字判定 / krc_parser 的译文有效性），
# 不是字符契约。不要把它当成汉字判定的真相 —— 要判字符请用 is_hanzi_char。
LOOSE_BMP_HANZI_RE = re.compile(r"[\u4e00-\u9fff]")

def has_hanzi(text: str) -> bool:
    """Checks if text contains Chinese characters."""
    if not text:
        return False
    return bool(HANZI_RE.search(text))

def has_kana(text: str) -> bool:
    """Checks if text contains Japanese Hiragana or Katakana."""
    if not text:
        return False
    return bool(KANA_RE.search(text))

def has_hangul(text: str) -> bool:
    """Checks if text contains Korean Hangul characters."""
    if not text:
        return False
    return bool(HANGUL_RE.search(text))

def is_foreign_track(title: str, artist: str = "") -> bool:
    """
    Checks if a track is foreign (requires Chinese translation).
    A track is foreign if it contains Hangul (Korean), Kana (Japanese), or lacks Hanzi (Western).
    """
    combined = f"{title} {artist}".strip()
    if has_hangul(combined) or has_kana(combined):
        return True
    return not has_hanzi(combined)

def is_translation_line(text: str, has_words: bool = False) -> bool:
    """
    Checks if a lyric line in a non-Chinese song is an interleaved Chinese translation.
    Criteria: Contains Hanzi, lacks Kana, and has no word-level timestamps.
    """
    if has_words or not text:
        return False
    return has_hanzi(text) and not has_kana(text)

# ---- G5/G7: 跨模块共用的文本判定（原先散落在 lrcshare / kugou / aggregator / acoustic 各写一份）----

SOURCE_LINE_RE = re.compile(
    r"(?i)(?:^|\b)(?:lrc|elrc)\s*[:：]"
    r"|https?://|www\."
    r"|歌词来源|来源\s*[:：]|由.{0,12}提供|powered\s+by|歌词由.{0,12}制作"
)

# v1.5.2-D1: 旧实现用窄正则 `[0-9A-Za-z\u4e00-\u9fff...]`，整行只有扩展 A/B
# 生僻字（如全部用 𠀀 区书写）时会被判成「无字母 → 署名行」。改用
# str.isalnum()（按 Unicode L*/N* 判定），天然覆盖扩展 A~I 与所有文字系统。

# v1.5.1: 署名行判定的单一出处（原先 lyric_validator 里有一份等价实现，两边漂移）。
# 语义与 validator 完全一致：多字关键词必须跟冒号/空白/行尾（否则「作词人」「鼓楼」
# 「键盘上的泪」会被误杀）；单字「词/曲」必须带冒号（否则「词不达意」「曲终人散」）。
CREDIT_KEYWORD_RE = re.compile(
    r"^(?:作词|作曲|编曲|制作|监制|录音|混音|吉他|贝斯|鼓|键盘|母带|弦乐|出品|"
    r"作詞|編曲|OP|SP|Lyrics|Composed|Arranged|Produced|Written|Mixed|Mastered|"
    r"작사|작곡|편곡)"
    r"(?:\s*[:：]|\s|$)"
    r"|^(?:词|曲)\s*[:：]",
    re.IGNORECASE,
)

INSTRUMENTAL_KEYWORDS = ("纯音乐", "请欣赏", "伴奏", "Instrumental", "No Lyrics")


def is_credit_line(text: str) -> bool:
    """是否为作词/作曲/编曲等署名行（内部先剥时间戳，可直接传带轴行）。"""
    from core.utils.time_utils import strip_timing_tags

    t = (text or "").strip()
    if not t:
        return False
    clean = strip_timing_tags(t)
    return bool(CREDIT_KEYWORD_RE.search(clean))


def is_source_or_credit_line(text: str) -> bool:
    """判断一行是来源标注/署名而非歌词正文（X-21）。

    v1.5.1: 旧实现只认「LRC: / URL / 歌词来源」这类**来源标注**，完全不含
    「作词 : 某某」这类**署名行**，与函数名不符。后果是
    `acoustic_aligner.enrich_track_with_acoustic_words` 的注释写着
    「来源/署名行不参与合成」，实际上署名行照样被合成出一条逐字时间轴，
    并且会进 CTC 的 target 序列（作/词/姬/赓 四个音节）干扰对齐。
    现在把署名行一并纳入。
    """
    t = (text or "").strip()
    if not t:
        return True
    if SOURCE_LINE_RE.search(t):
        return True
    if is_credit_line(t):
        return True
    # 整行没有任何字母/数字（含 CJK 全扩展区）才判为来源标注类噪声
    return not any(ch.isalnum() for ch in t)


def is_instrumental_placeholder(lines, target_duration_ms: int = 0, max_lines: int = 6) -> bool:
    """
    判断候选是不是"纯音乐/伴奏"占位（G7：原先 4 份实现、3 份关键词表）。

    只在时长已知且足够长时才判，避免把短视频/铃声误杀。
    """
    if not lines or not target_duration_ms or target_duration_ms <= 60000:
        return False
    if len(lines) > max_lines:
        return False
    joined = " ".join(str(l.get("text", "")) for l in lines)
    low = joined.lower()
    return any(kw.lower() in low for kw in INSTRUMENTAL_KEYWORDS)

_TITLE_SEP = r"[-–—－]"


def is_title_line(text: str, title: str = "", artist: str = "") -> bool:
    """是否为「歌名 - 歌手」这类标题行（v1.6.0）。

    现状缺口：`is_source_or_credit_line` 认「作词 : 某某」，**不认**「歌名 - 歌手」
    （实测返回 False），于是片头标题行会进对齐目标序列，把若干合成音节摊进前奏。
    本函数只做「标题行」这一件事，判定从严：必须带分隔符且（给了 title 时）以它开头。
    """
    from core.utils.time_utils import strip_timing_tags

    t = strip_timing_tags(text or "").strip()
    if not t:
        return False
    m = re.match(r"^(.{1,40}?)\s*" + _TITLE_SEP + r"\s*(.{1,40})$", t)
    if not m:
        return False
    left, right = m.group(1).strip(), m.group(2).strip()
    if not left or not right:
        return False
    if title:
        return left == title.strip()
    return True
