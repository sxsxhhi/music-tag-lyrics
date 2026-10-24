# -*- coding: utf-8 -*-
"""iTunes Search 与 Lookup 客户端：令牌桶限流、429 冷却与两区递进查询。"""

import time
import json
import urllib.parse
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from ..utils.cache_paths import get_cache_path
from ..utils import http
from ..utils.http import HttpResult
from ..utils.text_match import norm_title, artist_tokens, version_tokens, nfkc

logger = logging.getLogger(__name__)

SEARCH_REGIONS = ("hk", "tw", "us", "jp")
LOOKUP_REGIONS = ("us", "cn")
LOOKUP_BATCH = 10
RATE_CAPACITY = 8.0
RATE_PER_SEC = 0.5
COOLDOWN_SEC = 90.0
CACHE_TTL_DAYS = 180
MAX_CACHE_ENTRIES = 20000


class TokenBucket:
    def __init__(self, capacity: float = RATE_CAPACITY, rate: float = RATE_PER_SEC):
        self.capacity = capacity
        self.rate = rate
        self.tokens = capacity
        self.last_time = time.perf_counter()

    def consume(self) -> bool:
        now = time.perf_counter()
        elapsed = now - self.last_time
        self.last_time = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class ITunesClient:
    _instance: Optional["ITunesClient"] = None

    def __init__(self):
        self.bucket = TokenBucket()
        self.cooldown_until: float = 0.0
        self.cache: Dict[str, Any] = {}
        self._load_cache()

    @classmethod
    def get_instance(cls) -> "ITunesClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _load_cache(self) -> None:
        cache_file = get_cache_path("itunes-cache.json")
        if cache_file.is_file():
            try:
                self.cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                self.cache = {}

    def _save_cache(self) -> None:
        cache_file = get_cache_path("itunes-cache.json")
        try:
            if len(self.cache) > MAX_CACHE_ENTRIES:
                # 剔除最旧的 10%
                sorted_items = sorted(
                    self.cache.items(),
                    key=lambda x: x[1].get("ts", 0) if isinstance(x[1], dict) else 0
                )
                to_keep = dict(sorted_items[int(len(sorted_items) * 0.1):])
                self.cache = to_keep

            temp_f = cache_file.with_suffix(".tmp")
            temp_f.write_text(json.dumps(self.cache, ensure_ascii=False), encoding="utf-8")
            temp_f.replace(cache_file)
        except Exception as exc:
            logger.warning("保存 itunes-cache 失败: %s", exc)

    def _request(self, url: str) -> Optional[Dict[str, Any]]:
        """执行受限流与冷却保护的 HTTP 请求。"""
        now = time.perf_counter()
        if now < self.cooldown_until:
            logger.info("iTunes 客户端处于 429 冷却中，直接跳过")
            return None

        if not self.bucket.consume():
            # 令牌耗尽时不阻塞整条流水线，返回 None 落入后续源
            logger.debug("iTunes 请求超限流速，本次放弃")
            return None

        res = http.http_get_text_ex(url, timeout=3.0)
        if res.status == 429:
            self.cooldown_until = time.perf_counter() + COOLDOWN_SEC
            logger.warning("iTunes 返回 429，激活 90s 冷却，后续请求直接跳过")
            return None

        if res.status != 200 or not res.text:
            return None

        try:
            return json.loads(res.text)
        except Exception:
            return None

    def lookup_durations(self, apple_ids: List[str], is_zh: bool = True) -> Dict[str, int]:
        """批量反查 Apple ID 的 trackTimeMillis 时长字典。实现 us -> cn 局部两区递进。"""
        result: Dict[str, int] = {}
        missing_ids = []

        now_ts = int(time.time())
        ttl_sec = CACHE_TTL_DAYS * 86400

        # 1. 查本地缓存
        for aid in apple_ids:
            s_aid = str(aid).strip()
            cache_key = f"appleid:{s_aid}"
            c_val = self.cache.get(cache_key)
            if c_val and (now_ts - c_val.get("ts", 0) < ttl_sec):
                dur = c_val.get("duration_ms")
                if dur is not None and dur > 0:
                    result[s_aid] = dur
            else:
                missing_ids.append(s_aid)

        if not missing_ids:
            return result

        # 2. 第一批：向 US 店面批量查询
        still_missing = self._batch_lookup_storefront(missing_ids, "us", result)

        # 3. 第二批：对仍缺失且为华语的 ID 向 CN 店面查询（局部两区递进，优化 3）
        if still_missing and is_zh:
            self._batch_lookup_storefront(still_missing, "cn", result)

        # 记录空结果防止重复查询
        for aid in apple_ids:
            s_aid = str(aid).strip()
            if s_aid not in result:
                self.cache[f"appleid:{s_aid}"] = {"duration_ms": 0, "ts": int(time.time())}
        self._save_cache()

        return result

    def _batch_lookup_storefront(self, ids: List[str], country: str, result: Dict[str, int]) -> List[str]:
        missing = []
        for i in range(0, len(ids), LOOKUP_BATCH):
            batch = ids[i:i + LOOKUP_BATCH]
            ids_str = ",".join(batch)
            url = f"https://itunes.apple.com/lookup?id={ids_str}&country={country}"
            data = self._request(url)
            found_ids = set()
            if data and "results" in data:
                for item in data["results"]:
                    track_id = str(item.get("trackId") or "")
                    dur = item.get("trackTimeMillis")
                    if track_id and dur and dur > 0:
                        result[track_id] = int(dur)
                        found_ids.add(track_id)
                        self.cache[f"appleid:{track_id}"] = {"duration_ms": int(dur), "ts": int(time.time())}
            for b_id in batch:
                if b_id not in found_ids:
                    missing.append(b_id)
        return missing

    def search_track(
        self,
        title: str,
        artist: str = "",
        duration_ms: int = 0
    ) -> Optional[Dict[str, Any]]:
        """按 search 区域顺序检索 iTunes，命中首个合格候选即停止（永不触碰 cn）。"""
        t_strict = norm_title(title, keep_version=True)
        t_loose = norm_title(title, keep_version=False)
        q_ver = version_tokens(title)
        q_art = artist_tokens(artist)

        cache_key = f"track:{t_strict}|{nfkc(artist)}|{sorted(q_ver)}"
        now_ts = int(time.time())
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            if now_ts - entry.get("ts", 0) < CACHE_TTL_DAYS * 86400:
                return entry.get("data")

        # 检索各区
        query = f"{title} {artist}".strip()
        encoded_query = urllib.parse.quote(query)

        for region in SEARCH_REGIONS:
            url = f"https://itunes.apple.com/search?term={encoded_query}&country={region}&media=music&entity=song&limit=10"
            data = self._request(url)
            if not data or not data.get("results"):
                continue

            for item in data["results"]:
                track_id = str(item.get("trackId") or "")
                track_title = item.get("trackName") or ""
                track_artist = item.get("artistName") or ""
                track_dur = item.get("trackTimeMillis") or 0

                # 预筛选门禁
                itunes_t_loose = norm_title(track_title, keep_version=False)
                if itunes_t_loose != t_loose:
                    continue

                itunes_ver = version_tokens(track_title)
                if (q_ver - itunes_ver) or (itunes_ver - q_ver):
                    continue

                if q_art:
                    itunes_art = artist_tokens(track_artist)
                    if itunes_art and not (q_art & itunes_art):
                        continue

                if duration_ms > 0 and track_dur > 0:
                    if abs(track_dur - duration_ms) > 5000:
                        continue

                # 命中合格候选
                candidate = {
                    "apple_id": track_id,
                    "title": track_title,
                    "artist": track_artist,
                    "album": item.get("collectionName") or "",
                    "duration_ms": track_dur,
                    "region": region,
                }
                self.cache[cache_key] = {"data": candidate, "ts": now_ts}
                self._save_cache()
                return candidate

        self.cache[cache_key] = {"data": None, "ts": now_ts}
        self._save_cache()
        return None


def itunes_durations(apple_ids: List[str], is_zh: bool = True) -> Dict[str, int]:
    """便捷包装函数。"""
    return ITunesClient.get_instance().lookup_durations(apple_ids, is_zh=is_zh)
