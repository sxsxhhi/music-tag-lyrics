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
    # 整行没有任何字母/数字（含 CJK 全扩展区）才判为来源标注类噪声。
    # Python 3.10 内置 Unicode 数据库版本较旧，对 Unicode 15.1 的 CJK 扩展 I 字符
    # ch.isalnum() 会返回 False，故显式合并 is_cjk_char 判定以保证跨 Python 版本一致性。
    return not any(ch.isalnum() or is_cjk_char(ch) for ch in t)


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
_TIMING_TAG_RE = re.compile(r"[\[<]\d{1,3}:\d{1,2}(?:\.\d{1,3})?[\]>]")
_TITLE_KEY_DROP = set(
    " \t\r\n-–—－_·・,，.。、:：;；!！?？/&+|()（）[]【】\"'“”‘’"
)


def _norm_title_key(text: str) -> str:
    """标题/艺人名比对键：NFKC + 去标点空白 + 大小写折叠。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "").casefold()
    return "".join(ch for ch in s if ch not in _TITLE_KEY_DROP)


def _artist_keys(text: str) -> set:
    """提取艺人名证据集合：按 / & 、 与 feat./ft. 拆分，括号内的别名也算独立证据。"""
    import unicodedata
    s = unicodedata.normalize("NFKC", text or "")
    keys = set()
    for m in re.finditer(r"[\(（]([^\)）]*)[\)）]", s):
        k = _norm_title_key(m.group(1))
        if k:
            keys.add(k)
    base = re.sub(r"[\(（][^\)）]*[\)）]", "", s)
    for part in re.split(r"[/&;,&、|]|\s*\bfeat\.?\s+|\s*\bft\.?\s+", base, flags=re.I):
        k = _norm_title_key(part)
        if k:
            keys.add(k)
    return keys


_CTRL_WS_RE = re.compile(r"[\r\n\t\v\f]+")


def format_display_title(title: str, artist: str) -> str:
    """歌词内前奏标题行的唯一格式：「歌名 - 歌手」；无歌手时退回纯歌名。

    两个输出入口（日常 to_spl 与标准化 P_TITLE）必须共用本函数，
    避免同一首歌在不同入口得到不同标题行。换行/制表符等控制空白折叠为
    单个空格：标题行是单行结构，内嵌换行会同时破坏 ELRC 与 TTML。
    """
    t = _CTRL_WS_RE.sub(" ", title or "").strip()
    a = _CTRL_WS_RE.sub(" ", artist or "").strip()
    return f"{t} - {a}" if (t and a) else t


def match_display_title(text: str, title: str = "", artist: str = "") -> bool:
    """判定一行是否为「歌名 - 歌手」式前奏标题行（归一化匹配）。

    相对旧的三形态精确等值，容忍真实曲库常见变体：倒序、括号别名/括注、
    多名分隔符、全角连字符与多余空白。判定仍从严：必须有分隔符、至少一侧
    与 title 等价；给了 artist 时另一侧必须与之有交集。
    """
    t = _TIMING_TAG_RE.sub("", text or "").strip()
    if not t:
        return False
    # 侧长上限放宽到 300 字符：超长曲名（含 (Extended Club Mix) 等后缀）在真实
    # 曲库并不罕见，旧 80 字符上限会把它们判成非标题而重复合成标题行；误判风险
    # 由「至少一侧与 title 等价」这条硬条件兜住。
    m = re.match(r"^(.{1,300}?)\s*" + _TITLE_SEP + r"\s*(.{1,300})$", t)
    if not m:
        return False
    left, right = m.group(1).strip(), m.group(2).strip()
    if not left or not right:
        return False
    tn = _norm_title_key(title)
    if not tn:
        return True  # 无 title 时保持旧行为：有分隔符且两侧非空
    ln, rn = _norm_title_key(left), _norm_title_key(right)
    if ln == tn:
        other = _artist_keys(right)
    elif rn == tn:
        other = _artist_keys(left)
    else:
        return False
    ak = _artist_keys(artist)
    if not ak:
        return True
    return bool(other & ak)


def is_title_line(text: str, title: str = "", artist: str = "") -> bool:
    """兼容入口：实际判定统一走 match_display_title，消除双实现漂移。"""
    return match_display_title(text, title, artist)


# --------------------------------------------------------------------------- #
# 前奏显示行（标题 / 词曲）的字符级逐字：两入口单一出处
#
# 铁律 20 只保护「正文演唱字轴」（严禁对演唱内容做声学伪对齐或固定除法伪造）。
# 标题与词曲**不在演唱正文内**，属前奏显示投影，统一按窗口时长做字符级平均分配，
# 日常 to_spl 与标准化 P_TITLE 两入口共用同一算法，逐字时间戳必须一致。
# ---------------------------------------------------------------------------

def make_char_words(text: str, start_ms: int, end_ms: int):
    """字符级逐字时间轴：按窗口时长对字符平均分配（两入口同一算法）。

    - join 一律 "none"：空格本身是独立字符字块，字符间不得再触发
      铁律 14 的西文词界空格启发式（否则英文标题会变成 S o n g）。
    - 不设 100ms 下限：短窗口（如标准化 B 套）必须严格落在窗口内，
      不得溢出侵占首唱；窗口不足每字 1ms 的极端输入由调用方按
      fail-closed 处理（拒绝渲染而不是伪造）。
    """
    dur = end_ms - start_ms
    if dur <= 0 or not text:
        # 非正窗口 / 空文本一律返回空列表（fail-closed）：绝不返回 0ms 或负长度的
        # 单字块，否则调用方会把它当成合法逐字写出，违反铁律 8。调用方看到空列表
        # 应退回单词块显示行。
        return []
    n = len(text)
    step = dur / n
    return [
        {
            "text": char,
            "start": int(start_ms + i * step),
            "end": int(start_ms + (i + 1) * step),
            "join": "none",
        }
        for i, char in enumerate(text)
    ]


def collapse_duplicate_punc_tags(line: str) -> str:
    """折叠 0ms 重复标点时间戳（铁律 8），如 <01:46.180>.<01:46.180>。

    P1-5: 分钟位 1-3 位（旧版写死 \\d{2}，≥100 分钟的音频不会被折叠，与铁律 10 冲突）。
    """
    def repl(m):
        t1, punc, t2 = m.group(1), m.group(2), m.group(3)
        if t1 == t2:
            return punc + "<" + t2 + ">"
        return m.group(0)
    prev = None
    while prev != line:
        prev = line
        line = re.sub(r"<(\d{1,3}:\d{2}\.\d{1,3})>([^\w\s])<(\d{1,3}:\d{2}\.\d{1,3})>", repl, line)
    return line


def format_verbatim_words_line(words, start_ms: int, end_ms: int, precision: int = 2) -> str:
    """把词块序列格式化为标准 Enhanced LRC 逐字行（铁律 6/7 的行级形态）。

    原生执行西文词界空格（铁律 14）与 >=300ms 停顿 gap 标签（铁律 12）。
    precision=3 输出 [mm:ss.xxx] / <mm:ss.xxx>（毫秒源）；默认 2 位厘秒。
    """
    from .time_utils import format_time_tag

    if not words:
        return ""

    line_parts = []
    first_w = words[0]
    first_txt = first_w.get("text", "").replace("\xa0", " ")
    line_parts.append(f"{format_time_tag(start_ms, '[]', precision)}{format_time_tag(first_w['start'], '<>', precision)}{first_txt}")

    for i in range(1, len(words)):
        w_prev = words[i - 1]
        w_cur = words[i]
        prev_txt = w_prev.get("text", "").replace("\xa0", " ")
        cur_txt = w_cur.get("text", "").replace("\xa0", " ")
        prev_end = w_prev.get("end", w_prev.get("start", 0))

        # 词边界空格（铁律 14）：join 是解析层给出的"源里有没有空格"的权威信息
        join = w_prev.get("join")
        if prev_txt and cur_txt and not prev_txt.endswith(" ") and not cur_txt.startswith(" "):
            if join is None:
                if re.search(r"[a-zA-Z0-9,\.!\?;:'\"]$", prev_txt) and re.search(r"^[a-zA-Z0-9]", cur_txt):
                    cur_txt = " " + cur_txt
            elif join == "space":
                cur_txt = " " + cur_txt

        # 词间停顿 >=300ms 插入闭合标签（铁律 12）
        if w_cur.get("start", 0) - prev_end >= 300:
            line_parts.append(format_time_tag(prev_end, "<>", precision))
        line_parts.append(f"{format_time_tag(w_cur['start'], '<>', precision)}{cur_txt}")

    last_end = words[-1].get("end", end_ms)
    line_parts.append(format_time_tag(last_end, "<>", precision))
    return collapse_duplicate_punc_tags("".join(line_parts))
