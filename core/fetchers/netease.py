# -*- coding: utf-8 -*-
"""网易云歌词：检索、Live 互斥、时长门禁、YRC 逐字、官方译文。

v1.6.1：接口追加 `yv=1&rv=1` 后返回 `yrc`（逐字）与 `ytlrc`（与逐字同轴的译文）。
`fetch` 优先走 YRC；YRC 缺失、解析失败或尾部名单截断后不可用时，**原样回落**
行级 LRC 路径，保证没有 YRC 的曲目行为与升级前逐字节一致。
"""

import logging
import time
from typing import Dict, Any, Optional

from ..parsers.lrc_parser import parse_lrc
from ..parsers.yrc_parser import parse_yrc
from ..utils.http import http_post_json, http_get_json, UA_DESKTOP
from ..utils.version_guard import (
    is_live_version,
    is_remix_version,
    matches_version_policy,
    is_duration_acceptable,
    is_title_fuzzy_match,
    album_match_score,
)

logger = logging.getLogger(__name__)


def _lyric_field(data: Dict[str, Any], key: str) -> str:
    """安全取 `{key: {lyric: ...}}`。接口异常时该节点可能是 None / 字符串。"""
    node = data.get(key)
    if not isinstance(node, dict):
        return ""
    return node.get("lyric") or ""


class NetEaseFetcher:
    """Fetcher for NetEase Cloud Music lyrics with Version Guard."""

    @classmethod
    def search_best_song(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = ""
    ) -> Optional[Dict[str, Any]]:
        """检索网易曲目，返回最匹配的一首（含 album.publishTime）。

        v1.3.5 O1：与酷狗对称——Remix 双向互斥 + 同名专辑加权优先于时长抖动。
        """
        keyword = f"{title} {artist}".strip()
        search_url = "https://music.163.com/api/search/get/web?csrf_token="
        post_data = {
            "s": keyword,
            "type": 1,
            "offset": 0,
            "total": "true",
            "limit": 6
        }
        headers = {"Referer": "https://music.163.com"}
        data = http_post_json(search_url, data=post_data, headers=headers, user_agent=UA_DESKTOP, timeout=6)
        if not data or not isinstance(data, dict):
            return None

        songs = data.get("result", {}).get("songs", [])
        if not songs:
            return None

        target_is_live = is_live_version(title)
        target_is_remix = is_remix_version(title) or is_remix_version(album)
        valid_candidates = []
        for s in songs:
            s_name = s.get("name", "")
            s_album = s.get("album", {}).get("name", "")
            s_dur = s.get("duration", 0)

            if not is_title_fuzzy_match(s_name, title):
                continue
            if not matches_version_policy(target_is_live, s_name, s_album, target_is_remix=target_is_remix):
                continue
            if not is_duration_acceptable(s_dur, duration_ms, tolerance_sec=35):
                continue
            d_diff = abs(s_dur - duration_ms) if duration_ms > 0 else 0
            valid_candidates.append((d_diff, s))

        if not valid_candidates:
            return None
        valid_candidates.sort(
            key=lambda x: (-album_match_score(album, x[1].get("album", {}).get("name", "")), x[0])
        )
        return valid_candidates[0][1]

    @classmethod
    def search_song_id(cls, title: str = "", artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[int]:
        """
        Searches NetEase song catalog and returns the best matching song ID.
        Applies version mutual exclusion (Live vs Studio) and duration filtering.
        """
        song = cls.search_best_song(title, artist, duration_ms, album=album)
        if not song:
            return None
        return song.get("id")

    @classmethod
    def lookup_year(cls, title: str, artist: str = "", duration_ms: int = 0) -> str:
        """从网易专辑 publishTime 取四位年份。"""
        song = cls.search_best_song(title, artist, duration_ms)
        if not song:
            return ""
        ts = (song.get("album") or {}).get("publishTime") or 0
        try:
            ts_i = int(ts)
        except (TypeError, ValueError):
            return ""
        if ts_i <= 0:
            return ""
        if ts_i > 10_000_000_000:
            ts_i //= 1000
        year = time.gmtime(ts_i).tm_year
        if 1900 <= year <= 2099:
            return str(year)
        return ""

    @classmethod
    def fetch_raw_lyrics(cls, title: str, artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[Dict[str, str]]:
        """同一首歌的 lrc / tlyric / yrc / ytlrc 原文。供同步解析与 GPU 纯文本共用。

        `yrc` 必须显式带 `yv=1` 才会返回；`ytlrc` 是逐字轨配套的行级译文。
        """
        song_id = cls.search_song_id(title, artist, duration_ms, album=album)
        if not song_id:
            return None

        lyric_url = (
            f"https://music.163.com/api/song/lyric"
            f"?id={song_id}&lv=1&kv=1&tv=-1&yv=1&rv=1"
        )
        headers = {"Referer": "https://music.163.com"}
        data = http_get_json(lyric_url, headers=headers, user_agent=UA_DESKTOP, timeout=6)
        if not data or not isinstance(data, dict):
            return None
        lrc_text = _lyric_field(data, "lrc")
        tlyric_text = _lyric_field(data, "tlyric")
        yrc_text = _lyric_field(data, "yrc")
        ytlrc_text = _lyric_field(data, "ytlrc")
        if not any((lrc_text, tlyric_text, yrc_text, ytlrc_text)):
            return None
        return {
            "lrc": lrc_text,
            "tlyric": tlyric_text,
            "yrc": yrc_text,
            "ytlrc": ytlrc_text,
        }

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0, album: str = "") -> Optional[Dict[str, Any]]:
        """Retrieves and parses lyrics. YRC 逐字优先，缺失/失败回落行级 LRC。"""
        raw = cls.fetch_raw_lyrics(title, artist, duration_ms, album=album)
        if not raw:
            return None

        # v1.6.1 YRC 通道：解析异常、尾部名单截断失败（返回空 lines）都走回落分支，
        # 绝不因为新增通道而丢掉原本能拿到的 lrc。
        if raw.get("yrc"):
            try:
                word_data = parse_yrc(raw["yrc"], duration_ms=duration_ms)
            except Exception as exc:  # noqa: BLE001 - 任何解析异常都不许吞掉 lrc 兜底
                logger.warning("[netease] YRC 解析失败，回落 lrc: %s", exc)
                word_data = {"lines": []}
            if word_data.get("lines"):
                word_data["source"] = "netease"
                word_data["lyric_type"] = "word"
                word_data.setdefault("tags", {})["ti"] = title
                word_data.setdefault("tags", {})["ar"] = artist
                # ytlrc 与 yrc 同轴（实测最近邻 0ms），优先于 lrc 轴的 tlyric。
                # 挂载统一由 aggregator.attach_same_source_translation 负责，此处只传键。
                ytlrc_text = raw.get("ytlrc") or ""
                trans_text = ytlrc_text if ytlrc_text.strip() else (raw.get("tlyric") or "")
                if trans_text:
                    word_data["tlyric"] = trans_text
                return word_data

        if not raw.get("lrc"):
            return None

        parsed = parse_lrc(raw["lrc"])
        parsed["source"] = "netease"
        parsed["lyric_type"] = "line"
        parsed.setdefault("tags", {})["ti"] = title
        parsed.setdefault("tags", {})["ar"] = artist

        tlyric_text = raw.get("tlyric") or ""
        if tlyric_text:
            parsed["tlyric"] = tlyric_text

        return parsed
