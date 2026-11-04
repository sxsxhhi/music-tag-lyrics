# -*- coding: utf-8 -*-
"""QQ 音乐 QRC 抓取：搜索 + musicu 歌词接口 + 3DES 解密 + 逐字解析。

只交逐字
--------
QRC 通道如果解出来是行级（每行只有 1 个字块），直接放弃这个候选，交给下一个源。
**不返回行级候选**，也不消费接口的 `trans` / `roma` 字段（未验证，避免假译文）。

同曲判定
--------
歌名模糊匹配 + 歌手有交集 + 版本互斥 + 时长 ±5s 是硬闸；专辑只参与排序加权
（QQ 的候选常常没有专辑名，做硬闸会误杀）。

环境开关
--------
``MTL_QQ_TIER=off`` 可整档停用（用于排障或临时降级），与 Apple 的开关同构。
"""
import base64
import html
import logging
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from ..decryptors.qrc_des import decrypt_qrc
from ..parsers.qrc_parser import parse_qrc
from ..utils.http import UA_DESKTOP, http_get_json, http_post_json_body
from ..utils.text_utils import is_instrumental_placeholder
from ..utils.version_guard import (
    album_match_score,
    is_live_version,
    is_remix_version,
    is_title_fuzzy_match,
    matches_version_policy,
    strip_subtitle_brackets,
)
from .kugou import parsed_duration_reject_reason

logger = logging.getLogger(__name__)

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/search_for_qq_cp"
LYRIC_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
REFERER = "https://y.qq.com/"

# 铁律：搜歌元数据 ±5s（比酷狗的 ±10s 更紧，QQ 候选里翻唱/现场版本较多）
DURATION_WINDOW_MS = 5000
MAX_LYRIC_PROBES = 3

# 实测可用的一组 comm（轻量客户端身份）。改动前必须重跑一次真实请求确认 code == 0
_COMM = {
    "ct": "11",
    "cv": "1003006",
    "v": "1003006",
    "os_ver": "15",
    "phonetype": "24122RKC7C",
    "tmeAppID": "qqmusiclight",
    "nettype": "NETWORK_WIFI",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str) -> str:
    """搜索接口可能用 <em> 高亮命中词，统一去标签 + 解实体。"""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _b64(text: str) -> str:
    return base64.b64encode((text or "").encode("utf-8")).decode("ascii")


def _tier_enabled() -> bool:
    raw = (os.environ.get("MTL_QQ_TIER") or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


class QQFetcher:
    """QQ 音乐逐字歌词抓取器。"""

    @classmethod
    def search_songs(cls, title: str, artist: str = "") -> List[Dict[str, Any]]:
        """按「歌名 + 歌手」检索，标题带副标题时追加一次清洗后的检索。"""
        queries: List[str] = []
        base = f"{title} {artist}".strip()
        if base:
            queries.append(base)
        cleaned = strip_subtitle_brackets(title)
        if cleaned and cleaned != title:
            alt = f"{cleaned} {artist}".strip()
            if alt and alt not in queries:
                queries.append(alt)

        found: List[Dict[str, Any]] = []
        seen = set()
        for query in queries:
            params = {"w": query, "format": "json", "p": 1, "n": 10}
            url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
            data = http_get_json(
                url, headers={"Referer": REFERER}, user_agent=UA_DESKTOP, timeout=6
            )
            if not data or not isinstance(data, dict):
                continue
            songs = (((data.get("data") or {}).get("song") or {}).get("list") or [])
            for item in songs:
                song_id = item.get("songid")
                if not song_id:
                    # 极少数响应只有 songmid，没有数字 songid 时无法请求歌词，跳过
                    continue
                try:
                    song_id = int(song_id)
                except (TypeError, ValueError):
                    continue
                key = item.get("songmid") or song_id
                if key in seen:
                    continue
                seen.add(key)
                singers = [
                    _clean(s.get("name", ""))
                    for s in (item.get("singer") or [])
                    if _clean(s.get("name", ""))
                ]
                try:
                    interval = int(item.get("interval") or 0)
                except (TypeError, ValueError):
                    interval = 0
                found.append({
                    "song_id": song_id,
                    "songmid": item.get("songmid") or "",
                    "title": _clean(item.get("songname", "")),
                    "singers": singers,
                    "album": _clean(item.get("albumname", "")),
                    "interval": interval,
                })
            if found:
                break

        return found

    @classmethod
    def _rank_candidates(
        cls,
        songs: List[Dict[str, Any]],
        title: str,
        artist: str,
        album: str,
        duration_ms: int,
    ) -> List[Dict[str, Any]]:
        """四项硬闸 + 专辑加权排序。"""
        target_is_live = is_live_version(title)
        target_is_remix = is_remix_version(title)
        target_artist = (artist or "").strip().lower()

        scored: List[tuple] = []
        for idx, song in enumerate(songs):
            cand_title = song.get("title") or ""
            cand_album = song.get("album") or ""
            if not is_title_fuzzy_match(cand_title, title):
                continue
            if not matches_version_policy(
                target_is_live, cand_title, cand_album, target_is_remix=target_is_remix
            ):
                continue
            if target_artist:
                joined = "/".join(song.get("singers") or []).lower()
                if joined and target_artist not in joined and joined not in target_artist:
                    continue
            cand_ms = int(song.get("interval") or 0) * 1000
            if duration_ms > 0 and cand_ms > 0:
                if abs(cand_ms - duration_ms) > DURATION_WINDOW_MS:
                    continue
            dur_diff = abs(cand_ms - duration_ms) if duration_ms > 0 else 0
            alb_score = album_match_score(album, cand_album)
            scored.append(((-alb_score, dur_diff, idx), song))

        scored.sort(key=lambda x: x[0])
        return [song for _, song in scored]

    @classmethod
    def _request_qrc(cls, cand: Dict[str, Any]) -> str:
        """调 musicu 拿 hex 密文。失败返回空串。"""
        payload = {
            "comm": dict(_COMM),
            "req_0": {
                "method": "GetPlayLyricInfo",
                "module": "music.musichallSong.PlayLyricInfo",
                "param": {
                    "songID": int(cand["song_id"]),
                    "songName": _b64(cand.get("title") or ""),
                    "albumName": _b64(cand.get("album") or ""),
                    "singerName": _b64("/".join(cand.get("singers") or [])),
                    "crypt": 1,
                    "qrc": 1,
                    "trans": 1,
                    "roma": 1,
                    "cv": 2111,
                    "ct": 19,
                    "lrc_t": 0,
                    "qrc_t": 0,
                    "roma_t": 0,
                    "trans_t": 0,
                    "type": 0,
                    "interval": int(cand.get("interval") or 0),
                },
            },
        }
        data = http_post_json_body(
            LYRIC_URL, payload, headers={"Referer": REFERER}, user_agent=UA_DESKTOP, timeout=8
        )
        if not data or not isinstance(data, dict):
            return ""
        node = data.get("req_0") or {}
        code = node.get("code")
        if code not in (0, "0"):
            logger.debug("[qq] 歌词接口返回 code=%s", code)
            return ""
        return ((node.get("data") or {}).get("lyric") or "")

    @classmethod
    def fetch(
        cls,
        title: str,
        artist: str = "",
        duration_ms: int = 0,
        album: str = "",
    ) -> Optional[Dict[str, Any]]:
        """抓取 QQ 音乐逐字歌词。任何环节失败都返回 None，绝不返回行级或半截数据。"""
        if not _tier_enabled():
            return None

        songs = cls.search_songs(title, artist)
        if not songs:
            return None
        candidates = cls._rank_candidates(songs, title, artist, album, duration_ms)
        if not candidates:
            return None

        for cand in candidates[:MAX_LYRIC_PROBES]:
            try:
                hex_text = cls._request_qrc(cand)
                if not hex_text:
                    continue
                plain = decrypt_qrc(hex_text)
                if not plain:
                    continue
                parsed = parse_qrc(plain, duration_ms=duration_ms)
                lines = parsed.get("lines") or []
                if not lines:
                    continue
                # 只收逐字：每行至少 2 个字块，否则视为行级，交给下一个源
                if not any(len(l.get("words") or []) >= 2 for l in lines):
                    logger.debug("[qq] 候选 %s 只有行级，跳过", cand.get("song_id"))
                    continue
                if is_instrumental_placeholder(lines, duration_ms):
                    continue
                last = lines[-1]
                last_end = int(last.get("end") or last.get("start") or 0)
                reason = parsed_duration_reject_reason(last_end, duration_ms)
                if reason:
                    label = "预览截断" if reason == "preview" else "冲轴"
                    print(
                        f"   ⚠️ QQ 丢弃候选（{label}）：末行 {last_end}ms / 曲长 {duration_ms}ms"
                    )
                    continue

                parsed["source"] = "qq"
                parsed["lyric_type"] = "word"
                tags = parsed.setdefault("tags", {})
                tags.setdefault("ti", title)
                tags.setdefault("ar", artist)
                return parsed
            except Exception as exc:  # noqa: BLE001 - 单候选异常不许打断整条链路
                logger.debug("[qq] 候选 %s 处理失败: %s", cand.get("song_id"), exc)
                continue
        return None


__all__ = ["QQFetcher", "DURATION_WINDOW_MS"]
