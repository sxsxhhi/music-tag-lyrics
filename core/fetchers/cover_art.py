# -*- coding: utf-8 -*-
"""专辑封面三级梯级补全：Apple 顺风车 URL → 网易 → 酷狗。

铁律：已有内嵌封面绝不覆盖（门禁在 process_song 层，本模块只负责「无封面时找图」）。
图像必须过魔数 + 最小体积校验，任何网络/解析异常都静默降级，绝不阻断主流程。
"""

import re
import logging
from typing import Optional, Set, Tuple

from ..utils.http import UA_DESKTOP, http_get_bytes

logger = logging.getLogger(__name__)

MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
MIN_IMAGE_BYTES = 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024

_ORIG_RE = re.compile(r"/\d+x\d+bb(\.jpg)", re.IGNORECASE)


def sniff_image_mime(data: bytes) -> Optional[str]:
    """按文件头魔数判型。只认 JPEG / PNG，其余（含 WebP/HTML 壳）一律拒绝。"""
    if not data or len(data) < 16:
        return None
    if data.startswith(MAGIC_JPEG):
        return "image/jpeg"
    if data.startswith(MAGIC_PNG):
        return "image/png"
    return None


def apple_original_url(url: str) -> str:
    """把 artworkUrl100 的 100x100bb 缩略图换成 1000x1000bb 原图。

    Apple CDN 官方参数化路径（同目录尺寸替换），非缩放/压缩改写。
    不含该 token 的 URL 原样返回。
    """
    if not url:
        return ""
    return _ORIG_RE.sub(r"/1000x1000bb\1", url, count=1)


def _download_image(url: str, timeout: float = 10.0) -> Optional[bytes]:
    """下载图片二进制。非 http(s)、超时、超 15MB 一律返回 None。

    A3 去重：薄包装统一 HTTP 出口 http_get_bytes；保留本名作为测试桩挂点。
    F1：显式传桌面浏览器 UA——商用图片 CDN（mzstatic/126.net/kugou）对脚本 UA 直接 403。
    """
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    return http_get_bytes(
        url, timeout=timeout, max_bytes=MAX_IMAGE_BYTES, user_agent=UA_DESKTOP
    )


class CoverFetcher:
    """三级梯级取图，进程内同曲去重（同一 (title, artist) 只试一轮）。"""

    _instance: Optional["CoverFetcher"] = None

    def __init__(self) -> None:
        self._failed_keys: Set[Tuple[str, str]] = set()

    @classmethod
    def get_instance(cls) -> "CoverFetcher":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def fetch_cover(
        self,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        apple_artwork_url: str = "",
    ) -> Optional[Tuple[bytes, str]]:
        """返回 (img_bytes, mime) 或 None。三级全空/全失败返回 None。"""
        key = ((title or "").strip().lower(), (artist or "").strip().lower())
        if not key[0]:
            return None
        if key in self._failed_keys:
            return None
        result = self._try_ladder(title, artist, duration_ms, apple_artwork_url)
        if result is None:
            self._failed_keys.add(key)
        return result

    # ------------------------------------------------------------------
    def _try_ladder(
        self,
        title: str,
        artist: str,
        duration_ms: int,
        apple_url: str,
    ) -> Optional[Tuple[bytes, str]]:
        # T1 Apple（顺风车 URL：来自 iTunes lookup/search 缓存，零额外发现请求）
        if apple_url:
            got = self._fetch_from_url(apple_original_url(apple_url), "itunes")
            if got:
                return got
            got = self._fetch_from_url(apple_url, "itunes")  # 原图 404 时退回缩略图
            if got:
                return got
        # T2 网易
        url = self._netease_album_art(title, artist, duration_ms)
        if url:
            got = self._fetch_from_url(url, "netease")
            if got:
                return got
        # T3 酷狗
        url = self._kugou_album_art(title, artist)
        if url:
            got = self._fetch_from_url(url, "kugou")
            if got:
                return got
        return None

    def _fetch_from_url(self, url: str, source: str) -> Optional[Tuple[bytes, str]]:
        data = _download_image(url)
        if not data or len(data) < MIN_IMAGE_BYTES:
            return None
        mime = sniff_image_mime(data)
        if not mime:
            logger.info("[cover] %s 返回非 JPEG/PNG 内容，丢弃: %s", source, url[:80])
            return None
        print(f"🖼️ [cover] 命中封面（{source}，{len(data) // 1024}KB {mime}）")
        return data, mime

    # ------------------------------------------------------------------
    def _netease_album_art(self, title: str, artist: str, duration_ms: int) -> str:
        """网易检索：search_best_song 定曲 → song/detail 取 album.picUrl（实测 legacy search 只给 picId）。"""
        try:
            from .netease import NetEaseFetcher
            song = NetEaseFetcher.search_best_song(title, artist, duration_ms)
            if not song:
                return ""
            album = song.get("album") or {}
            pic = str(album.get("picUrl") or "").strip()
            if pic:
                return pic
            song_id = song.get("id")
            if song_id:
                from ..utils.http import http_get_json
                data = http_get_json(
                    f"https://music.163.com/api/song/detail?id={song_id}&ids=%5B{song_id}%5D",
                    headers={"Referer": "https://music.163.com"},
                    user_agent=UA_DESKTOP,
                    timeout=6,
                )
                for s in ((data or {}).get("songs") or []):
                    url = str(((s.get("album") or {}).get("picUrl")) or "").strip()
                    if url:
                        return url
        except Exception as exc:  # noqa: BLE001 - 封面失败绝不阻断主流程
            logger.debug("netease 封面检索失败: %s", exc)
        return ""

    def _kugou_album_art(self, title: str, artist: str) -> str:
        """酷狗兜底：search_song 命中候选的 img 直取；无 img 时按 album_id 打 album/info（字段 imgurl）。"""
        try:
            from .kugou import KugouFetcher
            from ..utils.version_guard import is_title_fuzzy_match
            for c in KugouFetcher.search_song(title, artist)[:6]:
                if not is_title_fuzzy_match(c.get("title", ""), title):
                    continue
                if c.get("img"):
                    return str(c["img"])
                album_id = c.get("album_id") or ""
                if album_id:
                    from ..utils.http import http_get_json, UA_KUGOU
                    data = http_get_json(
                        f"http://mobilecdn.kugou.com/api/v3/album/info?albumid={album_id}",
                        user_agent=UA_KUGOU,
                        timeout=6,
                    )
                    info = (data or {}).get("data") or {}
                    url = str(info.get("imgurl") or info.get("imgUrl") or info.get("img") or "").strip()
                    if url:
                        return url
                return ""  # 只用首个标题命中候选，避免连打多个专辑详情
        except Exception as exc:  # noqa: BLE001
            logger.debug("kugou 封面检索失败: %s", exc)
        return ""
