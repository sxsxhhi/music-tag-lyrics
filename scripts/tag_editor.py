# -*- coding: utf-8 -*-
"""
Audio Embedded Tag Editor (v1.1.1)
Inspects and standardizes embedded metadata tags (Title, Artist with '/', Album, Year, Lyrics)
for audio files (FLAC, WAV, MP3, M4A, OGG) using mutagen.
Zero external software dependency. Never moves or deletes audio files.
Includes Band Whitelist protection, 4-digit pure year formatting, and Trilateral embedded lyrics writing.
"""

import os
import re
from typing import Dict, Any, Optional

import mutagen
from mutagen.flac import FLAC
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TRCK, TCOM, TEXT, USLT
from mutagen.wave import WAVE
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

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
    
    # Check whitelist protection
    for band in BAND_WHITELIST:
        if clean_str.lower() == band.lower():
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
    return m.group(1) if m else s[:4]

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

def read_audio_tags(filepath: str) -> Dict[str, Any]:
    """Reads embedded metadata tags from an audio file."""
    info = {
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
        "composer": "",
        "lyricist": "",
        "lyrics": "",
        "duration": 0
    }
    if not os.path.exists(filepath):
        return info

    try:
        audio = mutagen.File(filepath)
        if audio is None:
            return info
            
        info["duration"] = int(getattr(audio.info, "length", 0) * 1000)
        
        # Format-specific tag extraction
        if isinstance(audio, FLAC):
            info["title"] = audio.get("title", [""])[0]
            info["artist"] = "/".join(audio.get("artist", [""]))
            info["album"] = audio.get("album", [""])[0]
            info["year"] = clean_year_string(audio.get("date", [""])[0])
            info["composer"] = audio.get("composer", [""])[0]
            info["lyricist"] = audio.get("lyricist", [""])[0]
            info["lyrics"] = audio.get("lyrics", [""])[0]
        elif isinstance(audio, MP4):
            info["title"] = audio.get("\xa9nam", [""])[0]
            info["artist"] = "/".join(audio.get("\xa9ART", [""]))
            info["album"] = audio.get("\xa9alb", [""])[0]
            info["year"] = clean_year_string(audio.get("\xa9day", [""])[0])
            info["composer"] = audio.get("\xa9wrt", [""])[0]
            info["lyrics"] = audio.get("\xa9lyr", [""])[0]
        elif isinstance(audio, OggVorbis):
            info["title"] = audio.get("title", [""])[0]
            info["artist"] = "/".join(audio.get("artist", [""]))
            info["album"] = audio.get("album", [""])[0]
            info["year"] = clean_year_string(audio.get("date", [""])[0])
            info["composer"] = audio.get("composer", [""])[0]
            info["lyricist"] = audio.get("lyricist", [""])[0]
            info["lyrics"] = audio.get("lyrics", [""])[0]
        elif isinstance(audio, WAVE):
            if audio.tags:
                id3 = audio.tags
                if "TIT2" in id3:
                    info["title"] = str(id3["TIT2"])
                if "TPE1" in id3:
                    info["artist"] = str(id3["TPE1"])
                if "TALB" in id3:
                    info["album"] = str(id3["TALB"])
                if "TDRC" in id3:
                    info["year"] = clean_year_string(str(id3["TDRC"]))
                if "TCOM" in id3:
                    info["composer"] = str(id3["TCOM"])
                if "TEXT" in id3:
                    info["lyricist"] = str(id3["TEXT"])
                if "TRCK" in id3:
                    info["tracknumber"] = str(id3["TRCK"])
                uslt = id3.get("USLT::eng") or id3.get("USLT::XXX") or id3.get("USLT::chi")
                if uslt:
                    info["lyrics"] = uslt.text
        else:
            # Fallback ID3 for MP3
            id3 = ID3(filepath)
            if "TIT2" in id3:
                info["title"] = str(id3["TIT2"])
            if "TPE1" in id3:
                info["artist"] = str(id3["TPE1"])
            if "TALB" in id3:
                info["album"] = str(id3["TALB"])
            if "TDRC" in id3:
                info["year"] = clean_year_string(str(id3["TDRC"]))
            if "TCOM" in id3:
                info["composer"] = str(id3["TCOM"])
            if "TEXT" in id3:
                info["lyricist"] = str(id3["TEXT"])
            if "TRCK" in id3:
                info["tracknumber"] = str(id3["TRCK"])
            uslt = id3.get("USLT::eng") or id3.get("USLT::XXX") or id3.get("USLT::chi")
            if uslt:
                info["lyrics"] = uslt.text
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
    lyrics: Optional[str] = None
) -> bool:
    """
    Updates audio file metadata tags in-place.
    Never moves or deletes the file.
    Ensures artists are joined with '/' under Band Whitelist protection.
    Formats year into 4-digit YYYY.
    Supports atomic embedded lyrics update across FLAC, WAV, and MP3.
    """
    if not os.path.exists(filepath):
        return False

    try:
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".flac":
            audio = FLAC(filepath)
            if title is not None:
                audio["title"] = [clean_title_string(title)]
            if artist is not None:
                audio["artist"] = [normalize_artist_string(artist)]
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
            audio.save()
            return True
            
        elif ext == ".mp3":
            try:
                audio = ID3(filepath)
            except Exception:
                audio = ID3()
            if title is not None:
                audio["TIT2"] = TIT2(encoding=3, text=[clean_title_string(title)])
            if artist is not None:
                audio["TPE1"] = TPE1(encoding=3, text=[normalize_artist_string(artist)])
            if album is not None:
                audio["TALB"] = TALB(encoding=3, text=[album.strip()])
            if year is not None:
                audio["TDRC"] = TDRC(encoding=3, text=[clean_year_string(year)])
            if tracknumber is not None:
                audio["TRCK"] = TRCK(encoding=3, text=[str(tracknumber)])
            if composer is not None:
                audio["TCOM"] = TCOM(encoding=3, text=[composer.strip()])
            if lyricist is not None:
                audio["TEXT"] = TEXT(encoding=3, text=[lyricist.strip()])
            if lyrics is not None:
                audio["USLT::eng"] = USLT(encoding=3, lang='eng', desc='', text=lyrics)
            audio.save(filepath)
            return True

        elif ext == ".wav":
            audio = WAVE(filepath)
            if audio.tags is None:
                audio.add_tags()
            if title is not None:
                audio.tags.add(TIT2(encoding=3, text=[clean_title_string(title)]))
            if artist is not None:
                audio.tags.add(TPE1(encoding=3, text=[normalize_artist_string(artist)]))
            if album is not None:
                audio.tags.add(TALB(encoding=3, text=[album.strip()]))
            if year is not None:
                audio.tags.add(TDRC(encoding=3, text=[clean_year_string(year)]))
            if tracknumber is not None:
                audio.tags.add(TRCK(encoding=3, text=[str(tracknumber)]))
            if composer is not None:
                audio.tags.add(TCOM(encoding=3, text=[composer.strip()]))
            if lyricist is not None:
                audio.tags.add(TEXT(encoding=3, text=[lyricist.strip()]))
            if lyrics is not None:
                audio.tags.add(USLT(encoding=3, lang='eng', desc='', text=lyrics))
            audio.save()
            return True
            
        elif ext in [".m4a", ".mp4"]:
            audio = MP4(filepath)
            if title is not None:
                audio["\xa9nam"] = [clean_title_string(title)]
            if artist is not None:
                audio["\xa9ART"] = [normalize_artist_string(artist)]
            if album is not None:
                audio["\xa9alb"] = [album.strip()]
            if year is not None:
                audio["\xa9day"] = [clean_year_string(year)]
            if composer is not None:
                audio["\xa9wrt"] = [composer.strip()]
            if lyrics is not None:
                audio["\xa9lyr"] = [lyrics]
            audio.save()
            return True
            
        elif ext in [".ogg"]:
            audio = OggVorbis(filepath)
            if title is not None:
                audio["title"] = [clean_title_string(title)]
            if artist is not None:
                audio["artist"] = [normalize_artist_string(artist)]
            if album is not None:
                audio["album"] = [album.strip()]
            if year is not None:
                audio["date"] = [clean_year_string(year)]
            if composer is not None:
                audio["composer"] = [composer.strip()]
            if lyricist is not None:
                audio["lyricist"] = [lyricist.strip()]
            if lyrics is not None:
                audio["lyrics"] = [lyrics]
            audio.save()
            return True
    except Exception as e:
        print(f"Error updating tags for {filepath}: {e}")
        return False
        
    return False
