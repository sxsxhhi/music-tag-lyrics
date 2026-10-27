# -*- coding: utf-8 -*-
"""下架孤品纯文本搜集：五源 plain 预检后，GitHub/raw 优先，再必应/DuckDuckGo。"""

import html as htmlmod
import logging
import re
import urllib.parse
from typing import List, Optional

from .fetchers.lrclib import LRCLIBFetcher
from .fetchers.netease import NetEaseFetcher
from .utils.http import http_get_text, UA_DESKTOP
from .utils.text_utils import LRC_META_LINE_RE, LOOSE_BMP_HANZI_RE
from .utils.time_utils import strip_timing_tags
from .utils.version_guard import is_title_fuzzy_match, is_live_version, matches_version_policy

logger = logging.getLogger(__name__)

# 元数据行正则统一走 text_utils.LRC_META_LINE_RE（A2 去重，与 lyric_validator 共用）
_HREF_RE = re.compile(r"""href=["'](https?://[^"'#\s]+)["']""", re.I)
_GITHUB_BLOB_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/blob/([^/]+)/(.+)",
    re.I,
)
_SKIP_HOST = (
    "taobao.com", "jd.com", "tmall.com", "amazon.", "youtube.com", "youtu.be",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "tiktok.com",
    "bilibili.com", "douyin.com", "weixin.qq.com",
)
_TEXT_EXT = (".txt", ".lrc", ".md", ".lyric", ".lyrics")
_MAX_FETCH_URLS = 24


def lrc_or_plain_to_text(raw: str) -> str:
    """剥元数据与时间戳，留下可对齐的逐行原文。"""
    out = []
    for line in (raw or "").splitlines():
        s = line.strip()
        if not s or LRC_META_LINE_RE.match(s):
            continue
        s = strip_timing_tags(s).strip()
        if s:
            out.append(s)
    return "\n".join(out)


def looks_like_lyrics(text: str, title: str = "") -> bool:
    if not text or not text.strip():
        return False
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    joined = "".join(lines)
    # 故意用窄口径（基本区）做体量启发式，不是字符契约；见 text_utils 注释
    cjk = len(LOOSE_BMP_HANZI_RE.findall(joined))
    latin = len(re.findall(r"[A-Za-z]", joined))
    if cjk < 20 and latin < 40:
        return False
    if title:
        blob = text[:800]
        titled = is_title_fuzzy_match(blob, title) or title.lower() in text.lower()
        if not titled and len(lines) < 6:
            return False
    return True


def github_blob_to_raw(url: str) -> Optional[str]:
    """github.com/.../blob/ref/path → raw.githubusercontent.com。已是 raw 则原样返回。"""
    if not url:
        return None
    bare = url.split("?")[0]
    if "raw.githubusercontent.com" in bare:
        return bare
    m = _GITHUB_BLOB_RE.match(bare)
    if not m:
        return None
    owner, repo, ref, path = m.group(1), m.group(2), m.group(3), m.group(4)
    path = urllib.parse.unquote(path)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def _from_plain_sources(title: str, artist: str, album: str, duration_ms: int) -> Optional[str]:
    try:
        raw = NetEaseFetcher.fetch_raw_lyrics(title, artist, duration_ms)
        if raw:
            text = lrc_or_plain_to_text(raw.get("lrc") or "")
            if looks_like_lyrics(text, title):
                logger.info("plain lyrics from netease unsynced/lrc")
                return text
    except Exception as exc:
        logger.warning("netease plain 失败: %s", exc)

    try:
        for item in LRCLIBFetcher.iter_api_items(title, artist, album, duration_ms):
            plain = (item.get("plainLyrics") or "").strip()
            if looks_like_lyrics(plain, title):
                logger.info("plain lyrics from lrclib plainLyrics")
                return plain
            synced = item.get("syncedLyrics") or ""
            text = lrc_or_plain_to_text(synced)
            if looks_like_lyrics(text, title):
                logger.info("plain lyrics from lrclib synced-as-plain")
                return text
    except Exception as exc:
        logger.warning("lrclib plain 失败: %s", exc)
    return None


def _host_ok(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return not any(bad in host for bad in _SKIP_HOST)


def _unwrap_search_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("uddg", "url", "u"):
        vals = qs.get(key) or []
        if not vals:
            continue
        raw = vals[0]
        if raw.startswith("http"):
            return raw
        if key == "u":
            try:
                import base64
                pad = "=" * ((4 - len(raw) % 4) % 4)
                dec = base64.urlsafe_b64decode(raw + pad).decode("utf-8", "ignore")
                if dec.startswith("http"):
                    return dec
            except Exception:
                pass
    return url


def _extract_hrefs(page: str) -> List[str]:
    urls = []
    for m in _HREF_RE.finditer(page or ""):
        u = _unwrap_search_url(htmlmod.unescape(m.group(1)))
        if u.startswith("http") and _host_ok(u):
            urls.append(u)
    return urls


def _html_to_candidate_text(page: str) -> str:
    if not page:
        return ""
    page = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", page)
    page = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", page)
    pres = re.findall(r"(?is)<pre[^>]*>(.*?)</pre>", page)
    blobs = [htmlmod.unescape(re.sub(r"<[^>]+>", "", p)) for p in pres]
    blobs.append(htmlmod.unescape(re.sub(r"<[^>]+>", "\n", page)))
    best = ""
    for blob in blobs:
        text = lrc_or_plain_to_text(blob)
        if looks_like_lyrics(text) and len(text) > len(best):
            best = text
    return best


def _search_pages(query: str) -> List[str]:
    q = urllib.parse.quote_plus(query)
    pages = []
    for url in (
        f"https://www.bing.com/search?q={q}",
        f"https://html.duckduckgo.com/html/?q={q}",
        f"https://github.com/search?q={q}&type=code",
    ):
        html = http_get_text(url, user_agent=UA_DESKTOP, timeout=6)
        if html:
            pages.append(html)
    return pages


def _web_search_plain(title: str, artist: str) -> Optional[str]:
    target_live = is_live_version(title)
    base_queries = [
        f"site:github.com {title} {artist} 歌词",
        f"site:github.com {title} 歌词",
        f"{title} {artist} 歌词",
        f"{title} 歌词",
        f"{title} {artist} lyrics",
    ]
    if target_live:
        queries = [f"{q} Live 现场" for q in base_queries] + base_queries
    else:
        queries = base_queries
    seen = set()
    for query in queries:
        for page in _search_pages(query):
            for href in _extract_hrefs(page):
                raw = github_blob_to_raw(href) or href
                if raw in seen:
                    continue
                seen.add(raw)
                if len(seen) > _MAX_FETCH_URLS:
                    return None
                if not matches_version_policy(target_live, raw, href):
                    continue
                path = urllib.parse.urlparse(raw).path.lower()
                is_raw = "raw.githubusercontent.com" in raw or path.endswith(_TEXT_EXT)
                if not is_raw and "github.com" in raw and "/blob/" not in raw:
                    continue
                body = http_get_text(raw, user_agent=UA_DESKTOP, timeout=6)
                if not body:
                    continue
                text = lrc_or_plain_to_text(body) if is_raw else _html_to_candidate_text(body)
                if looks_like_lyrics(text, title) and matches_version_policy(target_live, text[:800], raw):
                    logger.info("plain lyrics from web: %s", raw[:120])
                    return text
    return None


def fetch_plain_lyric_text(
    title: str,
    artist: str = "",
    album: str = "",
    duration_ms: int = 0,
) -> Optional[str]:
    """
    为 GPU 打轴搜集权威逐行文本。下架曲五源常空，主路径是全球搜索（GitHub 优先）。
    """
    text = _from_plain_sources(title, artist, album, duration_ms)
    if text:
        return text
    return _web_search_plain(title, artist)
