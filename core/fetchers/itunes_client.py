# -*- coding: utf-8 -*-
"""iTunes Search 与 Lookup 客户端：令牌桶限流、429 冷却与两区递进查询。"""

import time
import json
import urllib.parse
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple

from ..utils.cache_paths import get_cache_path
from ..utils.io_safe import atomic_write_bytes
from ..utils import http
from ..utils.http import HttpResult
from ..utils.text_match import norm_title, artist_tokens, version_tokens, nfkc

logger = logging.getLogger(__name__)

SEARCH_REGIONS = ("hk", "tw", "us", "jp")
# Lookup 第二批店面按语言分派：华语补 cn（大陆独家），外语补 jp（日韩独家常见）
LOOKUP_SECOND_STOREFRONT = {"zh": "cn", "foreign": "jp"}
LOOKUP_BATCH = 10
RATE_CAPACITY = 24.0
RATE_PER_SEC = 1.6
COOLDOWN_SEC = 90.0
CACHE_TTL_DAYS = 180
MISS_TTL_DAYS = 7
MAX_CACHE_ENTRIES = 20000
RELEASE_MARKERS = frozenset({"explicit", "clean"})


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
        self._bucket_warn_at: float = -1000.0
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

            temp_payload = json.dumps(self.cache, ensure_ascii=False).encode("utf-8")
            if not atomic_write_bytes(str(cache_file), temp_payload):
                logger.warning("保存 itunes-cache 失败: atomic_write_bytes 返回 False")
        except Exception as exc:
            logger.warning("保存 itunes-cache 失败: %s", exc)

    def _request(self, url: str) -> Optional[Dict[str, Any]]:
        """执行受限流与冷却保护的 HTTP 请求。"""
        now = time.perf_counter()
        if now < self.cooldown_until:
            logger.info("iTunes 客户端处于 429 冷却中，直接跳过")
            return None

        if not self.bucket.consume():
            # 令牌耗尽时不阻塞整条流水线，返回 None 落入后续源。
            # 同一耗尽期（20s 窗口）只可见打印一次，避免批量刷屏。
            now_pc = time.perf_counter()
            if now_pc - self._bucket_warn_at >= 20.0:
                self._bucket_warn_at = now_pc
                print("⚠️ [apple-ttml] iTunes 令牌耗尽，E1 本轮不可用 (剩余请求将降级)")
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
        hit_ttl = CACHE_TTL_DAYS * 86400
        miss_ttl = MISS_TTL_DAYS * 86400

        # 1. 查本地缓存。命中用 180 天；duration=0 真 miss 用 7 天；过期或传输失败留下的空值重查。
        for aid in apple_ids:
            s_aid = str(aid).strip()
            cache_key = f"appleid:{s_aid}"
            c_val = self.cache.get(cache_key)
            if not c_val:
                missing_ids.append(s_aid)
                continue
            age = now_ts - c_val.get("ts", 0)
            dur = c_val.get("duration_ms")
            if dur and dur > 0 and age < hit_ttl:
                result[s_aid] = dur
            elif (dur == 0 or dur is None) and age < miss_ttl:
                continue  # 真 miss，E1 本轮视为不可用
            else:
                missing_ids.append(s_aid)

        if not missing_ids:
            return result

        # 2. 第一批：向 US 店面批量查询
        _us_missing, us_fail = self._batch_lookup_storefront(missing_ids, "us", result)

        # 3. 第二批：按语言分派店面（华语 us→cn，外语 us→jp），对仍缺失的 ID 查询
        still_need = [i for i in missing_ids if i not in result]
        second_country = LOOKUP_SECOND_STOREFRONT["zh" if is_zh else "foreign"]
        second_fail: List[str] = []
        if still_need:
            _second_missing, second_fail = self._batch_lookup_storefront(still_need, second_country, result)

        us_fail_set = set(us_fail)
        second_fail_set = set(second_fail)
        # 仅两批店面都 200 且 rc=0 时写 duration=0；429/超时不得写负缓存
        for s_aid in missing_ids:
            if s_aid in result:
                continue
            if s_aid not in us_fail_set and s_aid not in second_fail_set:
                self.cache[f"appleid:{s_aid}"] = {"duration_ms": 0, "ts": int(time.time())}
        self._save_cache()

        return result

    def _batch_lookup_storefront(
        self, ids: List[str], country: str, result: Dict[str, int]
    ) -> Tuple[List[str], List[str]]:
        """返回 (本店面未找到, 传输失败)。传输失败 = _request None（429/超时/空桶）。"""
        not_found: List[str] = []
        transport_failed: List[str] = []
        for i in range(0, len(ids), LOOKUP_BATCH):
            batch = ids[i:i + LOOKUP_BATCH]
            ids_str = ",".join(batch)
            url = f"https://itunes.apple.com/lookup?id={ids_str}&country={country}"
            data = self._request(url)
            if data is None:
                transport_failed.extend(batch)
                continue
            found_ids = set()
            for item in data.get("results") or []:
                track_id = str(item.get("trackId") or "")
                dur = item.get("trackTimeMillis")
                if track_id and dur and dur > 0:
                    result[track_id] = int(dur)
                    found_ids.add(track_id)
                    # R9 顺风车：lookup 响应天然自带 artworkUrl100，缓存时长时顺手留存，取封面零额外请求
                    self.cache[f"appleid:{track_id}"] = {
                        "duration_ms": int(dur),
                        "artwork_url": str(item.get("artworkUrl100") or ""),
                        "ts": int(time.time()),
                    }
            for b_id in batch:
                if b_id not in found_ids:
                    not_found.append(b_id)
        return not_found, transport_failed

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

        cache_key = f"track:{t_strict}|{nfkc(artist)}|{sorted(q_ver)}|{int(duration_ms or 0)}"
        now_ts = int(time.time())
        cached = self.cache.get(cache_key)
        if cached:
            age = now_ts - cached.get("ts", 0)
            data = cached.get("data")
            if data is None:
                if age < MISS_TTL_DAYS * 86400:
                    return None
            elif age < CACHE_TTL_DAYS * 86400:
                cached_dur = int((data or {}).get("duration_ms") or 0)
                if duration_ms > 0 and cached_dur > 0 and abs(cached_dur - duration_ms) > 5000:
                    pass  # 时长对不上，作废重搜
                else:
                    return data

        # 检索各区
        query = f"{title} {artist}".strip()
        encoded_query = urllib.parse.quote(query)
        regions_ok = 0

        for region in SEARCH_REGIONS:
            url = f"https://itunes.apple.com/search?term={encoded_query}&country={region}&media=music&entity=song&limit=10"
            data = self._request(url)
            if data is None:
                continue
            regions_ok += 1
            if not data.get("results"):
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
                q_cmp = q_ver - RELEASE_MARKERS
                i_cmp = itunes_ver - RELEASE_MARKERS
                if (q_cmp - i_cmp) or (i_cmp - q_cmp):
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
                    "artwork_url": item.get("artworkUrl100") or "",
                    "region": region,
                }
                self.cache[cache_key] = {"data": candidate, "ts": now_ts}
                self._save_cache()
                return candidate

        # 真 miss：四个店面都 HTTP 200 且无合格曲。传输失败（429/超时）不写负缓存。
        if regions_ok == len(SEARCH_REGIONS):
            self.cache[cache_key] = {"data": None, "ts": now_ts}
            self._save_cache()
        return None


def itunes_durations(apple_ids: List[str], is_zh: bool = True) -> Dict[str, int]:
    """便捷包装函数。"""
    return ITunesClient.get_instance().lookup_durations(apple_ids, is_zh=is_zh)
