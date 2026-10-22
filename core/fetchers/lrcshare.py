# -*- coding: utf-8 -*-
"""
LRCSHARE Synced & Multi-Language Lyric Fetcher (v1.2.3)
Fetches word-level verbatim, line-level lyrics, and structured Chinese translations
from the LRCSHARE cloud repository (api.lrcshare.com).

Key Architectural Features:
1. Session Token Lifecycle & Auto-Caching (24h TTL) via /catalog.
2. Token-level Catalog Negative Pre-filtering (0 HTTP requests when song tokens don't match).
3. Structured Multi-language Versions Parsing (kind == 'original' vs 'translation').
4. Strict Romanization Elimination (kind == 'romanization' discarded per Iron Rule).
5. Syllable-level timing extraction from inline <offset:dur> word tags.
6. Proximity duration gating and instrumental placeholder anti-tampering.
Zero external dependencies.
"""

import os
import time
import re
import urllib.parse
from typing import Dict, List, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..utils.http import http_get_json, UA_DEFAULT
from ..utils.version_guard import (
    is_live_version,
    matches_version_policy,
    strip_subtitle_brackets
)
from ..utils.text_utils import has_hanzi

BASE_URL = "https://api.lrcshare.com/v1"
# C-04: 密钥只从环境变量读取，源码里不再保留默认值。
# 未配置时该源会直接跳过（历史上它因参数顺序 bug 从未成功过，跳过不损失现有能力）。
API_KEY = os.getenv("LRCSHARE_API_KEY", "").strip()
LRCSHARE_UA = "Lyrico/1.0 (github.com/Replica0110/Lyrico)"

WORD_TAG_RE = re.compile(r"<\d{1,6}(?::\d{1,6})?>")
WORD_SPLIT_RE = re.compile(r"<(\d{1,6})(?::(\d{1,6}))?>")

# X-21: 来源/署名行识别。lrcshare 的歌词里常混入
# "LRC：X2ISPANDA[https://lrcshare.com]" 这类上传者署名，它们不是歌词，
# 一旦进入输出还会被逐字合成器按字符均分，污染整条时间轴。
SOURCE_LINE_RE = re.compile(
    r"(?i)(?:^|\b)(?:lrc|elrc)\s*[:：]"
    r"|https?://|www\."
    r"|歌词来源|来源\s*[:：]|由.{0,12}提供|powered\s+by|歌词由.{0,12}制作"
)


def is_source_or_credit_line(text: str) -> bool:
    """判断一行是否是来源标注/署名而非歌词正文。"""
    t = (text or "").strip()
    if not t:
        return True
    if SOURCE_LINE_RE.search(t):
        return True
    # 整行没有文字也没有数字（纯符号/纯标点）→ 不是歌词
    if not re.search(r"[0-9A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", t):
        return True
    return False

def strip_word_tags(text: str) -> str:
    """Removes <offset:dur> tags from text."""
    if not text:
        return ""
    return WORD_TAG_RE.sub("", text)

def parse_words_from_text(text: str, line_start: int, line_end: int) -> List[Dict[str, Any]]:
    """Converts <offset:dur> or <offset> inline tags into structured word timing."""
    tokens = WORD_SPLIT_RE.split(str(text))
    starts = []
    offset = 0
    duration = 0
    i = 0
    while i < len(tokens):
        t_text = tokens[i]
        if t_text:
            starts.append({"text": t_text, "start": line_start + offset, "dur": duration})
        if i + 1 < len(tokens):
            offset = int(tokens[i + 1])
        if i + 2 < len(tokens):
            duration = int(tokens[i + 2]) if tokens[i + 2] is not None else 0
        i += 3
        
    words = []
    for j in range(len(starts)):
        w_start = starts[j]["start"]
        w_dur = starts[j]["dur"]
        if w_dur > 0:
            w_end = w_start + w_dur
        elif j + 1 < len(starts):
            w_end = starts[j + 1]["start"]
        else:
            w_end = line_end
        words.append({
            "start": w_start,
            "end": w_end,
            "duration": max(0, w_end - w_start),
            "text": starts[j]["text"]
        })
    return words

class LrcShareFetcher:
    """Fetcher for api.lrcshare.com lyrics and multi-language translations."""

    _cached_session_token: Optional[str] = None
    _session_expires_at: float = 0.0
    _cached_catalog_text: Optional[str] = None
    _catalog_expires_at: float = 0.0

    @classmethod
    def _get_headers(cls, require_session: bool = True) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "X-API-Key": API_KEY,
            "User-Agent": LRCSHARE_UA
        }
        if require_session:
            token = cls._get_session_token()
            if token:
                headers["X-LrcShare-Session"] = token
        return headers

    @classmethod
    def _get_session_token(cls) -> Optional[str]:
        now = time.time()
        if cls._cached_session_token and cls._session_expires_at > now:
            return cls._cached_session_token

        try:
            url = f"{BASE_URL}/catalog"
            headers = {
                "Accept": "application/json",
                "X-API-Key": API_KEY,
                "User-Agent": LRCSHARE_UA
            }
            res = http_get_json(url, headers=headers, timeout=8, user_agent=LRCSHARE_UA)
            if res and isinstance(res, dict) and res.get("code") == 200:
                data = res.get("data", {})
                token = data.get("session_token")
                if token:
                    cls._cached_session_token = token
                    cls._session_expires_at = now + 24 * 3600 - 300  # 24h minus 5m buffer
                    cat_text = data.get("text")
                    if cat_text:
                        cls._cached_catalog_text = cat_text.lower()
                        cls._catalog_expires_at = now + 24 * 3600
                    return token
        except Exception:
            pass
        return cls._cached_session_token

    @classmethod
    def catalog_allows(cls, keyword: str = "", title: str = "", artist: str = "") -> bool:
        """
        Token-level catalog negative pre-filtering:
        If catalog snapshot is available, returns False if none of the query tokens exist in catalog.
        Saves unnecessary network searches for items known not to be in the database.
        """
        cls._get_session_token()  # Warm cache
        if not cls._cached_catalog_text:
            return True  # Fallback: proceed if catalog not available

        cat_text = cls._cached_catalog_text

        def check_tokens(query: str) -> bool:
            if not query:
                return False
            q_clean = query.strip().lower()
            if q_clean in cat_text:
                return True
            tokens = [t for t in re.split(r"\s+", q_clean) if len(t) > 1]
            for t in tokens:
                if t in cat_text:
                    return True
            return False

        if title or artist:
            if check_tokens(title) or check_tokens(artist):
                return True
            return False

        if keyword:
            return check_tokens(keyword)

        return True

    @classmethod
    def search_songs(
        cls,
        title: str,
        artist: str = "",
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Searches songs with structured title/artist query and combined fallback."""
        if not cls.catalog_allows(title=title, artist=artist):
            return []

        headers = cls._get_headers(require_session=True)

        # 1. Structured query
        if title:
            params = {
                "type": "song",
                "title": title,
                "limit": limit
            }
            if artist:
                params["artist"] = artist
            url = f"{BASE_URL}/search?" + urllib.parse.urlencode(params)
            res = http_get_json(url, headers=headers, timeout=8, user_agent=LRCSHARE_UA)
            if res and isinstance(res, dict) and res.get("code") == 200:
                items = res.get("data", {}).get("items", [])
                if items:
                    return items

        # 2. Combined fallback
        keyword = f"{title} {artist}".strip()
        params = {
            "type": "song",
            "keyword": keyword,
            "limit": limit
        }
        url = f"{BASE_URL}/search?" + urllib.parse.urlencode(params)
        res = http_get_json(url, headers=headers, timeout=8, user_agent=LRCSHARE_UA)
        if res and isinstance(res, dict) and res.get("code") == 200:
            return res.get("data", {}).get("items", [])

        return []

    @classmethod
    def fetch_song_detail(cls, song_id: str) -> Optional[Dict[str, Any]]:
        """Fetches full song detail including lyric_lines and ttml versions."""
        if not song_id:
            return None
        headers = cls._get_headers(require_session=True)
        url = f"{BASE_URL}/song/{urllib.parse.quote(song_id)}?lyric_lines=1"
        res = http_get_json(url, headers=headers, timeout=10, user_agent=LRCSHARE_UA)
        if res and isinstance(res, dict) and res.get("code") == 200:
            return res.get("data")
        return None

    @classmethod
    def parse_lyric_lines(cls, lyric_lines_data: Dict[str, Any], default_lrc: str = "") -> Dict[str, Any]:
        """
        Parses lyric_lines versions into structured original and translated lines.
        Strictly excludes romanization (kind == 'romanization').
        Extracts syllable-level timing from <offset:dur> tags if present.
        """
        versions = lyric_lines_data.get("versions", [])
        original_ver = None
        translation_rows = []

        for v in versions:
            kind = v.get("kind")
            if kind == "original" and not original_ver:
                original_ver = v
            elif kind == "translation":
                # Prefer Chinese translation (zh-Hans, zh-Hant, zh, or containing Hanzi)
                rows = v.get("rows", [])
                sample_text = "".join(r.get("text", "") for r in rows[:10])
                if has_hanzi(sample_text) or str(v.get("lang", "")).lower().startswith("zh"):
                    translation_rows.extend(rows)

        # Fallback to default raw LRC if original_ver is empty
        if not original_ver or not original_ver.get("rows"):
            if default_lrc:
                parsed_raw = parse_lrc(default_lrc)
                return {
                    "lines": parsed_raw.get("lines", []),
                    "has_words": False,
                    "has_translation": False
                }
            return {"lines": [], "has_words": False, "has_translation": False}

        raw_orig_rows = original_ver.get("rows", [])
        # Sort by time_ms
        sorted_orig = sorted(
            [r for r in raw_orig_rows if r.get("time_ms") is not None],
            key=lambda x: x["time_ms"]
        )

        lines = []
        has_any_words = False

        for i, row in enumerate(sorted_orig):
            start = int(row.get("time_ms", 0))
            end = int(row.get("end_ms", 0)) if row.get("end_ms") else None
            if not end or end <= start:
                if i + 1 < len(sorted_orig):
                    end = int(sorted_orig[i + 1]["time_ms"])
                else:
                    end = start + 3000

            text_raw = str(row.get("text", "")).strip()
            clean_text = strip_word_tags(text_raw)

            words = []
            if WORD_TAG_RE.search(text_raw):
                words = parse_words_from_text(text_raw, start, end)
                if words:
                    has_any_words = True

            lines.append({
                "start": start,
                "end": end,
                "duration": end - start,
                "text": clean_text,
                "translation": "",
                "words": words
            })

        # X-21: 剔除来源/署名行，避免它们被当作歌词逐字输出
        if lines:
            kept = [l for l in lines if not is_source_or_credit_line(l.get("text", ""))]
            if len(kept) != len(lines):
                has_any_words = any(len(l.get("words") or []) >= 2 for l in kept)
            lines = kept

        # Attach translations by nearest timestamp matching
        if translation_rows and lines:
            sorted_trans = sorted(
                [r for r in translation_rows if r.get("time_ms") is not None],
                key=lambda x: x["time_ms"]
            )
            used_trans = set()
            for line in lines:
                l_start = line["start"]
                best_diff = 2000  # 2.0s window
                best_idx = -1
                best_text = ""
                for t_idx, tr in enumerate(sorted_trans):
                    if t_idx in used_trans:
                        continue
                    t_time = int(tr.get("time_ms", 0))
                    diff = abs(t_time - l_start)
                    if diff < best_diff:
                        best_diff = diff
                        best_idx = t_idx
                        best_text = strip_word_tags(str(tr.get("text", "")).strip())
                if best_idx >= 0 and has_hanzi(best_text):
                    line["translation"] = best_text
                    used_trans.add(best_idx)

        has_trans = any(bool(l.get("translation")) for l in lines)
        return {
            "lines": lines,
            "has_words": has_any_words,
            "has_translation": has_trans
        }

    @classmethod
    def fetch(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Public entry point to search and fetch lyrics from LRCSHARE.
        Applies duration proximity gating (±5.0s) and instrumental placeholder gating.
        """
        if not API_KEY:
            # 未配置 LRCSHARE_API_KEY：静默跳过该源（不报错、不阻塞其他源）
            return None
        candidates = cls.search_songs(title, artist, limit=5)
        if not candidates:
            return None

        target_is_live = is_live_version(title)

        for cand in candidates:
            cand_title = cand.get("title", "") or ""
            # album 可能是 {"id":..,"name":..} 也可能是纯字符串
            cand_album_raw = cand.get("album", "")
            if isinstance(cand_album_raw, dict):
                cand_album = cand_album_raw.get("name", "") or ""
            else:
                cand_album = str(cand_album_raw) if cand_album_raw else ""
            # F-01: 参数顺序必须是 (target_is_live, cand_title, cand_album)。
            # 旧代码传的是 (cand_title, target_is_live)，导致录音室目标恒被丢弃、Live 目标直接抛异常。
            if not matches_version_policy(target_is_live, cand_title, cand_album):
                continue

            song_id = cand.get("id")
            if not song_id:
                continue

            detail = cls.fetch_song_detail(song_id)
            if not detail:
                continue

            lyric_lines_data = detail.get("lyric_lines") or {}
            raw_lrc = detail.get("lrc") or ""

            parsed = cls.parse_lyric_lines(lyric_lines_data, default_lrc=raw_lrc)
            lines = parsed.get("lines", [])
            if not lines:
                continue

            # Anti-tampering gate: Reject collapsed instrumental placeholders
            if duration_ms > 60000 and len(lines) <= 6:
                sample_text = "".join(l.get("text", "") for l in lines)
                if any(w in sample_text for w in ["纯音乐", "请欣赏", "伴奏", "Instrumental"]):
                    continue

            # Duration Proximity Gate (Iron Rule 17: ±5.0s)
            last_end = lines[-1]["end"]
            if duration_ms > 0:
                diff_ms = abs(last_end - duration_ms)
                if diff_ms > 5000:
                    continue

            # Extract tags
            album_name = detail.get("album", {}).get("name", "") if isinstance(detail.get("album"), dict) else ""
            artist_names = [a.get("name") for a in detail.get("artists", []) if isinstance(a, dict) and a.get("name")]
            ar_str = " / ".join(artist_names) if artist_names else artist

            return {
                "lines": lines,
                "tags": {
                    "ti": detail.get("title", title),
                    "ar": ar_str,
                    "al": album_name
                },
                "source": "lrcshare",
                "lyric_type": "word" if parsed.get("has_words") else "line",
                "has_translation": parsed.get("has_translation", False)
            }

        return None
