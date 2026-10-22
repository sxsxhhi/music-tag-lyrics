# -*- coding: utf-8 -*-
"""
Audio Embedded Tag Editor (v1.2.4)
Inspects and standardizes embedded metadata tags (Title, Artist with '/', Album, Year, Lyrics)
for audio files (FLAC, WAV, MP3, M4A, OGG) using mutagen.
Zero external software dependency. Never moves or deletes audio files.
Includes Band Whitelist protection, 4-digit pure year formatting, and Trilateral embedded lyrics writing.
Refactored to eliminate ID3 and Vorbis duplicate code (DRY architecture).
"""

import os
import re
from typing import Dict, Any, Optional

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TIT2, TPE1, TPE2, TALB, TDRC, TRCK, TCOM, TEXT, USLT
from mutagen.wave import WAVE
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
try:
    from mutagen.dsf import DSF
except ImportError:
    DSF = None

try:
    from core.utils.io_safe import safe_save_audio_tags
except ImportError:  # 允许以脚本方式直接运行本文件
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.utils.io_safe import safe_save_audio_tags

# Whitelist of bands, groups, and brand names that contain '&' or other separators but must NOT be split
BAND_WHITELIST = {
    "伍佰 & China Blue",
    "伍佰&China Blue",
    "China Blue",
    "Phoenix & The Turtles",
    "B.B.クイーンズ",
    "Fun.",
    "Simon & Garfunkel",
    "Earth, Wind & Fire",
    "Kool & The Gang",
    "Brooks & Dunn",
    "Daryl Hall & John Oates",
    "Hall & Oates",
    "Tom Petty and the Heartbreakers",
    "Bob Marley & The Wailers",
    "KC & The Sunshine Band",
    "Florence + The Machine",
    "Marina and the Diamonds",
    "Mumford & Sons",
    "Dan + Shay",
    "Aly & AJ",
    "Twenty One Pilots",
    "Air Supply",
    "Beyond",
    "Mayday",
    "S.H.E",
    "Sweety",
    "Twins",
    "F.I.R.",
    "飞儿乐团",
    "苏打绿",
    "草蜢",
    "黑豹乐队",
    "唐朝乐队",
    "痛仰乐队",
    "新裤子",
    "五条人",
    "重塑雕像的权利",
    "落日飞车",
    "回春丹",
    "告五人",
    "南拳妈妈",
    "花儿乐队"
}

# Regex pattern to identify multi-artist separators
ARTIST_SPLIT_RE = re.compile(r"\s*(?:&|/|、|，|,|\sft\.?\s|\sfeat\.?\s)\s*", re.IGNORECASE)

def normalize_artist_string(artist_str: str) -> str:
    """
    Standardizes multiple artists using '/' as the universal separator.
    Strictly protects bands and groups in BAND_WHITELIST.
    e.g. '徐良 & 小凌' -> '徐良/小凌'
         '伍佰 & China Blue' -> '伍佰 & China Blue' (Protected!)
    """
    if not artist_str:
        return ""
    
    clean_str = artist_str.strip()
    
    # 白名单保护：只保护"名字里本来就含分隔符"的乐队（含 & , / + feat. 等），
    # 因为只有这类名字会被切分误伤。"Beyond the Black" 这种不该被当成 "Beyond" 保护。
    low = clean_str.lower()
    for band in sorted(BAND_WHITELIST, key=len, reverse=True):
        b = band.lower()
        if not re.search(r"[&/,，、+]|feat|ft\.", b):
            continue
        if low == b or low.startswith(b + " ") or low.startswith(b + "(") or low.startswith(b + "["):
            return band
            
    # Split by any known separator
    parts = [p.strip() for p in ARTIST_SPLIT_RE.split(clean_str) if p.strip()]
    return "/".join(parts) if parts else clean_str

def clean_year_string(year_val: Any) -> str:
    """
    Extracts a clean 4-digit Gregorian year (1900-2099).
    e.g. '2016-08-15' -> '2016'
         '2016/10/01 00:00:00' -> '2016'
    """
    if not year_val:
        return ""
    s = str(year_val).strip()
    m = re.search(r"\b(19\d\d|20\d\d)\b", s)
    # 匹配不到返回空串：以前返回 s[:4] 会把 "unknown" 写成 "unkn" 塞进 TDRC
    return m.group(1) if m else ""

def clean_title_string(title_str: str) -> str:
    """
    Cleans unwanted marketing and encoding suffixes from song title.
    Preserves genuine subtitle parentheses.
    """
    if not title_str:
        return ""
    cleaned = re.sub(r"\[(FLAC|无损|Hi-Res|320K|MP3|HQ|简明宣发版)[^\]]*\]", "", title_str, flags=re.IGNORECASE)
    cleaned = re.sub(r"【(FLAC|无损|Hi-Res|320K|MP3|HQ)[^】]*】", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def _txt(value: Optional[str]) -> Optional[str]:
    """空串/纯空白视为「未提供」，返回 None —— 避免把原有标签清空。"""
    if value is None:
        return None
    v = str(value).strip()
    return v or None


def _yr(value: Optional[str]) -> Optional[str]:
    """年份归一化：解析不出 4 位年份就视为「未提供」，绝不写入残缺值。"""
    if value is None:
        return None
    return clean_year_string(value) or None


def _extract_id3_dict(id3: Any, info: Dict[str, Any]) -> None:
    """Helper: Extracts metadata fields from an ID3 tags structure into info dictionary."""
    if not id3:
        return
    if "TIT2" in id3:
        info["title"] = str(id3["TIT2"])
    if "TPE1" in id3:
        info["artist"] = str(id3["TPE1"])
    if "TPE2" in id3:
        info["albumartist"] = str(id3["TPE2"])
    if "TALB" in id3:
        info["album"] = str(id3["TALB"])
    if "TDRC" in id3:
        info["year"] = clean_year_string(str(id3["TDRC"]))
    elif "TYER" in id3:
        # ID3v2.3 只有 TYER；不回退的话会读出空年份，随后空值覆盖掉原年份
        info["year"] = clean_year_string(str(id3["TYER"]))
    if "TCOM" in id3:
        info["composer"] = str(id3["TCOM"])
    if "TEXT" in id3:
        info["lyricist"] = str(id3["TEXT"])
    if "TRCK" in id3:
        info["tracknumber"] = str(id3["TRCK"])
    # P1-11: 不再只认 eng/XXX/chi 三个固定键。优先 eng，否则取任意一帧
    # （jpn/kor 或多 desc 的歌词帧以前一律读不到，表现为"明明有内嵌歌词却显示空"）
    uslt = None
    for key in sorted(k for k in id3.keys() if k.startswith("USLT")):
        uslt = id3[key]
        if "eng" in key.lower():
            break
    if uslt:
        info["lyrics"] = uslt.text

def _apply_id3_tags(
    id3: Any,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    year: Optional[str] = None,
    tracknumber: Optional[str] = None,
    composer: Optional[str] = None,
    lyricist: Optional[str] = None,
    lyrics: Optional[str] = None,
    albumartist: Optional[str] = None
) -> None:
    """Helper: Applies standardized metadata frames onto an ID3 structure."""
    title, album, composer, lyricist = _txt(title), _txt(album), _txt(composer), _txt(lyricist)
    year = _yr(year)
    if title is not None:
        id3["TIT2"] = TIT2(encoding=3, text=[clean_title_string(title)])
    if artist is not None:
        id3["TPE1"] = TPE1(encoding=3, text=[normalize_artist_string(artist)])
    if albumartist is not None:
        if albumartist.strip() == "":
            if "TPE2" in id3:
                del id3["TPE2"]
        else:
            id3["TPE2"] = TPE2(encoding=3, text=[normalize_artist_string(albumartist)])
    if album is not None:
        id3["TALB"] = TALB(encoding=3, text=[album.strip()])
    if year is not None:
        id3["TDRC"] = TDRC(encoding=3, text=[clean_year_string(year)])
    if tracknumber is not None:
        id3["TRCK"] = TRCK(encoding=3, text=[str(tracknumber)])
    if composer is not None:
        id3["TCOM"] = TCOM(encoding=3, text=[composer.strip()])
    if lyricist is not None:
        id3["TEXT"] = TEXT(encoding=3, text=[lyricist.strip()])
    if lyrics is not None:
        # 只覆盖自己写的那一帧（lang=eng, desc=""），其它语言/描述的 USLT 一律保留。
        # 旧实现按 startswith("USLT") 全删，会把多语歌词与翻译帧一并抹掉。
        id3["USLT::eng"] = USLT(encoding=3, lang='eng', desc='', text=lyrics)

def _apply_vorbis_tags(
    audio: Any,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    year: Optional[str] = None,
    tracknumber: Optional[str] = None,
    composer: Optional[str] = None,
    lyricist: Optional[str] = None,
    lyrics: Optional[str] = None,
    albumartist: Optional[str] = None
) -> None:
    """Helper: Applies standardized metadata keys onto Vorbis Comment structure (FLAC/OGG)."""
    title, album, composer, lyricist = _txt(title), _txt(album), _txt(composer), _txt(lyricist)
    year = _yr(year)
    if title is not None:
        audio["title"] = [clean_title_string(title)]
    if artist is not None:
        audio["artist"] = [normalize_artist_string(artist)]
    if albumartist is not None:
        if albumartist.strip() == "":
            if "albumartist" in audio:
                del audio["albumartist"]
        else:
            audio["albumartist"] = [normalize_artist_string(albumartist)]
    if album is not None:
        audio["album"] = [album.strip()]
    if year is not None:
        audio["date"] = [clean_year_string(year)]
    if tracknumber is not None:
        audio["tracknumber"] = [str(tracknumber)]
    if composer is not None:
        audio["composer"] = [composer.strip()]
    if lyricist is not None:
        audio["lyricist"] = [lyricist.strip()]
    if lyrics is not None:
        audio["lyrics"] = [lyrics]

def read_audio_tags(filepath: str) -> Dict[str, Any]:
    """
    Reads embedded metadata tags from an audio file.

    每个字段独立 try —— 单个字段解析失败不会把整份信息清零（旧实现外层一个
    ``except: pass``，任一字段抛错就返回全空，随后空值还会覆盖掉原标签）。
    ``duration`` 为 ``None`` 表示**未知**（文件缺失/解析失败），调用方不得当作 0 秒。
    """
    info: Dict[str, Any] = {
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
        "composer": "",
        "lyricist": "",
        "lyrics": "",
        "duration": None
    }
    if not os.path.exists(filepath):
        return info

    try:
        audio = mutagen.File(filepath)
    except Exception:
        audio = None
    if audio is None:
        return info

    try:
        length = getattr(getattr(audio, "info", None), "length", None)
        info["duration"] = int(length * 1000) if length else None
    except Exception:
        info["duration"] = None

    def _take(key: str, getter) -> None:
        try:
            info[key] = getter() or ""
        except Exception:
            pass

    if isinstance(audio, (FLAC, OggVorbis)):
        _take("title", lambda: audio.get("title", [""])[0])
        _take("artist", lambda: "/".join(audio.get("artist", [""])))
        _take("album", lambda: audio.get("album", [""])[0])
        _take("year", lambda: clean_year_string(audio.get("date", [""])[0]))
        _take("composer", lambda: audio.get("composer", [""])[0])
        _take("lyricist", lambda: audio.get("lyricist", [""])[0])
        _take("lyrics", lambda: audio.get("lyrics", [""])[0])
    elif isinstance(audio, MP4):
        _take("title", lambda: audio.get("\xa9nam", [""])[0])
        _take("artist", lambda: "/".join(audio.get("\xa9ART", [""])))
        _take("album", lambda: audio.get("\xa9alb", [""])[0])
        _take("year", lambda: clean_year_string(audio.get("\xa9day", [""])[0]))
        _take("composer", lambda: audio.get("\xa9wrt", [""])[0])
        _take("lyrics", lambda: audio.get("\xa9lyr", [""])[0])
    elif isinstance(audio, WAVE):
        if getattr(audio, "tags", None):
            try:
                _extract_id3_dict(audio.tags, info)
            except Exception:
                pass
    elif DSF is not None and isinstance(audio, DSF):
        if getattr(audio, "tags", None):
            try:
                _extract_id3_dict(audio.tags, info)
            except Exception:
                pass
    else:
        # 兜底：MP3 及 mutagen 无法归类的 ID3 容器
        try:
            _extract_id3_dict(ID3(filepath), info)
        except Exception:
            pass

    return info

def update_audio_tags(
    filepath: str,
    title: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    year: Optional[str] = None,
    tracknumber: Optional[str] = None,
    composer: Optional[str] = None,
    lyricist: Optional[str] = None,
    lyrics: Optional[str] = None,
    albumartist: Optional[str] = None
) -> bool:
    """
    Updates audio file metadata tags in-place.

    所有格式统一走「副本改写 -> 原子替换」（core.utils.io_safe.safe_save_audio_tags）。
    mutagen 的 save() 是截断后重写，直接作用在原文件上时进程中断就会留下损坏音频；
    现在只改副本，写成功且副本非空才 os.replace 回原路径。

    Never moves or deletes the file.
    """
    if not os.path.exists(filepath):
        return False

    ext = os.path.splitext(filepath)[1].lower()

    def _write_vorbis(tmp: str) -> bool:
        audio = FLAC(tmp) if ext == ".flac" else OggVorbis(tmp)
        _apply_vorbis_tags(audio, title, artist, album, year, tracknumber, composer, lyricist, lyrics, albumartist)
        audio.save()
        return True

    def _write_mp3(tmp: str) -> bool:
        # P0-4: 解析失败时**绝不**新建空 ID3() 再保存 —— 那会把 APIC/TXXX/USLT 等原有帧
        # 全部抹掉，随后 os.replace 覆盖原文件，等于静默销毁用户的标签。
        # 这里故意不加 try：让 safe_save_audio_tags 捕获异常后放弃写入，原文件保持不变。
        id3 = ID3(tmp)
        orig_v2 = getattr(id3, "version", (2, 4, 0))[1]
        _apply_id3_tags(id3, title, artist, album, year, tracknumber, composer, lyricist, lyrics, albumartist)
        # 保留原 ID3 版本，避免 v2.3 被静默升级成 v2.4
        id3.save(tmp, v2_version=orig_v2)
        return True

    def _write_wave(tmp: str) -> bool:
        audio = WAVE(tmp)
        if audio.tags is None:
            audio.add_tags()
        _apply_id3_tags(audio.tags, title, artist, album, year, tracknumber, composer, lyricist, lyrics, albumartist)
        audio.save()
        return True

    def _write_dsf(tmp: str) -> bool:
        audio = DSF(tmp)
        if audio.tags is None:
            audio.add_tags()
        _apply_id3_tags(audio.tags, title, artist, album, year, tracknumber, composer, lyricist, lyrics, albumartist)
        audio.save()
        return True

    def _write_mp4(tmp: str) -> bool:
        from mutagen.mp4 import MP4FreeForm
        nonlocal title, album, composer, lyricist, year
        title, album, composer, lyricist = _txt(title), _txt(album), _txt(composer), _txt(lyricist)
        year = _yr(year)
        audio = MP4(tmp)
        if title is not None:
            audio["\xa9nam"] = [clean_title_string(title)]
        if artist is not None:
            audio["\xa9ART"] = [normalize_artist_string(artist)]
        if albumartist is not None:
            if albumartist.strip() == "":
                if "aART" in audio:
                    del audio["aART"]
            else:
                audio["aART"] = [normalize_artist_string(albumartist)]
        if album is not None:
            audio["\xa9alb"] = [album.strip()]
        if year is not None:
            audio["\xa9day"] = [clean_year_string(year)]
        if tracknumber is not None:
            try:
                audio["trkn"] = [(int(str(tracknumber).split("/")[0]), 0)]
            except (ValueError, TypeError):
                pass
        if composer is not None:
            audio["\xa9wrt"] = [composer.strip()]
        if lyricist is not None:
            audio["----:com.apple.iTunes:LYRICIST"] = [MP4FreeForm(lyricist.strip().encode("utf-8"))]
        if lyrics is not None:
            audio["\xa9lyr"] = [lyrics]
        audio.save()
        return True

    try:
        if ext in (".flac", ".ogg"):
            return safe_save_audio_tags(filepath, _write_vorbis)
        if ext == ".mp3":
            return safe_save_audio_tags(filepath, _write_mp3)
        if ext == ".wav":
            return safe_save_audio_tags(filepath, _write_wave)
        if ext == ".dsf":
            if DSF is None:
                raise ImportError("mutagen.dsf is not available")
            return safe_save_audio_tags(filepath, _write_dsf)
        if ext in (".m4a", ".mp4"):
            return safe_save_audio_tags(filepath, _write_mp4)
    except Exception as e:
        print(f"Error updating tags for {filepath}: {e}")
        return False

    return False
