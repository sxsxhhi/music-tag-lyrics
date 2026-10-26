# -*- coding: utf-8 -*-
"""AMLL：经网易 song id 拉取 Apple Music TTML 社区库。"""

from typing import Dict, Any, Optional

from ..parsers.ttml_parser import parse_ttml
from .netease import NetEaseFetcher
from ..utils.http import http_get_text, UA_DEFAULT

import logging

logger = logging.getLogger(__name__)

BASE_URL = "https://cdn.jsdelivr.net/gh/amll-dev/amll-ttml-db@main"

class AMLLFetcher:
    """Fetcher for AMLL TTML lyrics."""

    @classmethod
    def fetch_by_ncm_id(cls, ncm_id: int) -> Optional[Dict[str, Any]]:
        url = f"{BASE_URL}/ncm-lyrics/{ncm_id}.ttml"
        content = http_get_text(url, timeout=5, user_agent=UA_DEFAULT)
        if not content:
            return None
            
        try:
            parsed = parse_ttml(content)
            if parsed and parsed.get("lines"):
                parsed["source"] = "amll"
                # P1-19: 依据行内是否含有 2 个以上有效 words 动态判定 lyric_type
                has_verbatim = sum(1 for ln in parsed["lines"] if len(ln.get("words", [])) >= 2) >= 1
                parsed["lyric_type"] = "word" if has_verbatim else "line"
                return parsed
        except Exception as exc:
            logger.debug("[amll] 解析 TTML 失败 (ncm_id=%s): %s", ncm_id, exc)
        return None

    @classmethod
    def fetch(cls, title: str, artist: str = "", duration_ms: int = 0) -> Optional[Dict[str, Any]]:
        # Use NetEaseFetcher with Version Guard and duration filtering
        ncm_id = NetEaseFetcher.search_song_id(title, artist, duration_ms)
        if ncm_id:
            res = cls.fetch_by_ncm_id(ncm_id)
            if res:
                return res
        return None
