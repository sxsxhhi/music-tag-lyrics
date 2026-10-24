# -*- coding: utf-8 -*-
"""AMLL 本地索引反查：以 rawLyricFile 分组，内存建立反查表与歧义消歧。"""

import os
import json
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Set, Optional, FrozenSet, Any

from ..utils.cache_paths import get_cache_path
from ..utils import http
from ..utils.http import HttpResult
from ..utils.text_match import (
    nfkc, strip_punct, norm_title, artist_tokens, artist_token_list, primary_artist,
    version_tokens, norm_album, GENERIC_ALBUMS
)

logger = logging.getLogger(__name__)

INDEX_MIRRORS = ("gbclstudio", "gcore", "testingcf", "bikonoo", "dimeta")
INDEX_PATH = "/am-lyrics/index.jsonl"
INDEX_MIN_LINES = 2000
INDEX_SHRINK_RATIO = 0.97
INDEX_TTL_DAYS = 7
INDEX_DOWNLOAD_TIMEOUT = 12.0

MIRROR_HOSTS = {
    "gbclstudio": "https://amll-ttml-db.gbclstudio.cn",
    "gcore":      "https://gcore.jsdelivr.net/gh/amll-dev/amll-ttml-db@main",
    "testingcf":  "https://testingcf.jsdelivr.net/gh/amll-dev/amll-ttml-db@main",
    "bikonoo":    "https://amlldb.bikonoo.com",
    "dimeta":     "https://amll.mirror.dimeta.top/api/db",
}


@dataclass(frozen=True)
class Variant:
    title_strict: str
    title_loose: str
    artist_tokens: FrozenSet[str]
    primary_artist: str
    album_norm: str
    version_tokens: FrozenSet[str]


@dataclass
class Entry:
    file: str                             # rawLyricFile 唯一分组键
    ids: Tuple[str, ...]                  # 行内 id + appleMusicId，截前 4
    variants: Tuple[Variant, ...]
    n_rows: int
    ambiguous: bool
    album_norms: FrozenSet[str]           # 优化 2: 聚合组内所有行的全部专辑名
    isrcs: FrozenSet[str]


def build_ids_from_rows(rows: List[Dict[str, Any]]) -> Tuple[str, ...]:
    """严格从行内 id 与 appleMusicId 构建，绝不从 rawLyricFile 抽数字。"""
    order = []
    for r in rows:
        row_id = str(r.get("id") or "").strip()
        if row_id:
            order.append(row_id)
    for r in rows:
        am_ids = r.get("appleMusicId") or []
        if isinstance(am_ids, list):
            for aid in am_ids:
                s_aid = str(aid).strip()
                if s_aid:
                    order.append(s_aid)
        elif isinstance(am_ids, (str, int)):
            s_aid = str(am_ids).strip()
            if s_aid:
                order.append(s_aid)

    # 去重且截前 4
    seen = set()
    deduped = []
    for x in order:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return tuple(deduped[:4])


class AmllIndex:
    _instance: Optional["AmllIndex"] = None

    def __init__(self, entries: List[Entry]):
        self.entries = entries
        self.by_key_strict: Dict[Tuple[str, str], List[int]] = {}
        self.by_key_loose: Dict[Tuple[str, str], List[int]] = {}
        self.by_title_strict: Dict[str, List[int]] = {}
        self.by_title_loose: Dict[str, List[int]] = {}
        self.by_isrc: Dict[str, int] = {}
        self._build_tables()

    @classmethod
    def get_instance(cls) -> Optional["AmllIndex"]:
        if cls._instance is None:
            cls._instance = cls.load_or_fetch()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _build_tables(self) -> None:
        for idx, entry in enumerate(self.entries):
            # 记录 ISRC
            for isrc in entry.isrcs:
                if isrc and isrc not in self.by_isrc:
                    self.by_isrc[isrc] = idx

            # 遍历 Entry 下的各 Variant 建立反查键
            seen_strict_pairs = set()
            seen_loose_pairs = set()
            seen_strict_titles = set()
            seen_loose_titles = set()

            for v in entry.variants:
                if v.title_strict and v.title_strict not in seen_strict_titles:
                    seen_strict_titles.add(v.title_strict)
                    self.by_title_strict.setdefault(v.title_strict, []).append(idx)

                if v.title_loose and v.title_loose not in seen_loose_titles:
                    seen_loose_titles.add(v.title_loose)
                    self.by_title_loose.setdefault(v.title_loose, []).append(idx)

                for a_tok in v.artist_tokens:
                    pair_s = (v.title_strict, a_tok)
                    if pair_s not in seen_strict_pairs:
                        seen_strict_pairs.add(pair_s)
                        self.by_key_strict.setdefault(pair_s, []).append(idx)

                    pair_l = (v.title_loose, a_tok)
                    if pair_l not in seen_loose_pairs:
                        seen_loose_pairs.add(pair_l)
                        self.by_key_loose.setdefault(pair_l, []).append(idx)

    @classmethod
    def parse_jsonl_content(cls, lines: List[str]) -> "AmllIndex":
        """解析 jsonl 文本行并按 rawLyricFile 进行聚合。"""
        # 1. 行级解析
        groups: Dict[str, List[Dict[str, Any]]] = {}
        key_to_files: Dict[Tuple[str, str], Set[str]] = {}

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                logger.warning("amll-index 跳过坏 JSON 行")
                continue
            raw_file = (row.get("rawLyricFile") or "").strip()
            if not raw_file:
                continue

            # metadata 是列表套列表: [["key", [vals]], ...]
            meta_dict: Dict[str, List[str]] = {}
            for item in row.get("metadata", []):
                if isinstance(item, list) and len(item) >= 2:
                    k, v = item[0], item[1]
                    vals = v if isinstance(v, list) else [v]
                    meta_dict[k] = [str(x) for x in vals]

            name = meta_dict.get("musicName", [""])[0]
            artists = "/".join(meta_dict.get("artists", []))
            albums = meta_dict.get("album", [])
            isrcs = meta_dict.get("isrc", [])
            am_ids = meta_dict.get("appleMusicId", [])

            row_data = {
                "id": str(row.get("id") or "").strip(),
                "musicName": name,
                "artists": artists,
                "albums": albums,
                "isrcs": isrcs,
                "appleMusicId": am_ids,
            }
            groups.setdefault(raw_file, []).append(row_data)

            # 统计 (musicName, artists) 歧义键
            t_key = norm_title(name, keep_version=True)
            a_key = primary_artist(artists)
            if t_key:
                key_to_files.setdefault((t_key, a_key), set()).add(raw_file)

        # 统计歧义键 (>1 个 file)
        ambiguous_files = set()
        ambiguous_key_count = 0
        for pair_key, flist in key_to_files.items():
            if len(flist) > 1:
                ambiguous_key_count += 1
                ambiguous_files.update(flist)

        # 2. 构建 Entry 集合
        entries: List[Entry] = []
        for raw_file, rows in groups.items():
            ids = build_ids_from_rows(rows)
            var_set: Set[Variant] = set()
            all_albums: Set[str] = set()
            all_isrcs: Set[str] = set()

            for r in rows:
                t_s = norm_title(r["musicName"], keep_version=True)
                t_l = norm_title(r["musicName"], keep_version=False)
                a_tokens = artist_tokens(r["artists"])
                p_artist = primary_artist(r["artists"])
                v_tokens = frozenset(version_tokens(r["musicName"]))
                for alb in r["albums"]:
                    al_n = norm_album(alb)
                    if al_n:
                        all_albums.add(al_n)
                primary_al_n = norm_album(r["albums"][0]) if r["albums"] else ""

                for isrc_val in r["isrcs"]:
                    s_isrc = isrc_val.strip()
                    if s_isrc:
                        all_isrcs.add(s_isrc)

                var_set.add(Variant(
                    title_strict=t_s,
                    title_loose=t_l,
                    artist_tokens=a_tokens,
                    primary_artist=p_artist,
                    album_norm=primary_al_n,
                    version_tokens=v_tokens
                ))

            entry = Entry(
                file=raw_file,
                ids=ids,
                variants=tuple(sorted(var_set, key=lambda v: (v.title_strict, v.primary_artist))[:4]),
                n_rows=len(rows),
                ambiguous=(raw_file in ambiguous_files),
                album_norms=frozenset(all_albums),
                isrcs=frozenset(all_isrcs)
            )
            entries.append(entry)

        logger.info(
            "amll-index 加载完成: %d 行 -> %d 组; %d 个歧义键",
            len(lines), len(entries), ambiguous_key_count
        )
        return cls(entries)

    @classmethod
    def load_or_fetch(cls) -> Optional["AmllIndex"]:
        """读取本地缓存索引；若不存在或已过期则尝试多镜像下载，具备 stale-while-error 保护。"""
        cache_path = get_cache_path("amll-index.jsonl")
        meta_path = get_cache_path("amll-index.meta.json")

        # 检查本地缓存
        cached_lines = None
        cached_meta = {}
        if cache_path.is_file():
            try:
                if meta_path.is_file():
                    cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                age_days = (time.time() - cached_meta.get("updated_at", 0)) / 86400
                if age_days < INDEX_TTL_DAYS:
                    text = cache_path.read_text(encoding="utf-8")
                    lines = [ln for ln in text.splitlines() if ln.strip()]
                    if len(lines) >= INDEX_MIN_LINES:
                        return cls.parse_jsonl_content(lines)
            except Exception as exc:
                logger.warning("读取本地索引缓存失败: %s", exc)

        # 尝试下载
        new_lines = cls._download_index(prev_line_count=cached_meta.get("lines_count", 0))
        if new_lines:
            try:
                # 原子写盘
                temp_file = cache_path.with_suffix(".tmp")
                temp_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                temp_file.replace(cache_path)
                meta_path.write_text(json.dumps({
                    "updated_at": int(time.time()),
                    "lines_count": len(new_lines)
                }), encoding="utf-8")
                return cls.parse_jsonl_content(new_lines)
            except Exception as exc:
                logger.warning("写盘索引失败: %s", exc)

        # stale-while-error: 尝试读取旧文件
        if cache_path.is_file():
            try:
                text = cache_path.read_text(encoding="utf-8")
                lines = [ln for ln in text.splitlines() if ln.strip()]
                if len(lines) >= INDEX_MIN_LINES:
                    logger.info("下载失败但启用旧索引 (stale-while-error)")
                    return cls.parse_jsonl_content(lines)
            except Exception:
                pass

        logger.info("本地索引不可用（12s 超时），将仅使用 iTunes 实时检索")
        return None

    @classmethod
    def _download_index(cls, prev_line_count: int = 0) -> Optional[List[str]]:
        """按镜像优先级下载完整 index.jsonl 并执行 4 道完整性校验。"""
        t0 = time.perf_counter()
        for mirror_name in INDEX_MIRRORS:
            rem = INDEX_DOWNLOAD_TIMEOUT - (time.perf_counter() - t0)
            if rem <= 1.0:
                break
            base = MIRROR_HOSTS.get(mirror_name)
            if not base:
                continue
            url = f"{base}{INDEX_PATH}"
            res = http.http_get_text_ex(url, timeout=rem)
            if res.status != 200 or not res.text:
                continue

            lines = [ln for ln in res.text.splitlines() if ln.strip()]
            # 完整性四条检验
            if len(lines) < INDEX_MIN_LINES:
                continue
            if prev_line_count > 0 and len(lines) < int(prev_line_count * INDEX_SHRINK_RATIO):
                continue
            if not lines[-1].strip().endswith("}"):
                continue
            if not any("appleMusicId" in ln for ln in lines):
                continue

            return lines
        return None

    def lookup(
        self,
        title: str,
        artist: str = "",
        album: str = "",
        isrc: str = ""
    ) -> List[Entry]:
        """反查前 2 名候选 Entry。"""
        # ISRC 命中直接满分晋级
        if isrc and isrc in self.by_isrc:
            return [self.entries[self.by_isrc[isrc]]]

        t_strict = norm_title(title, keep_version=True)
        t_loose = norm_title(title, keep_version=False)
        a_tokens = artist_token_list(artist)[:3]

        pool_indices: List[int] = []

        # L1: 严格标题 + 艺人 token
        for a in a_tokens:
            pool_indices.extend(self.by_key_strict.get((t_strict, a), []))

        # L2: 宽松标题 + 艺人 token
        if not pool_indices:
            for a in a_tokens:
                pool_indices.extend(self.by_key_loose.get((t_loose, a), []))

        # L3: 纯标题
        if not pool_indices:
            pool_indices = self.by_title_strict.get(t_strict) or self.by_title_loose.get(t_loose) or []

        if not pool_indices:
            return []

        # 去重
        seen_files = set()
        unique_entries = []
        for idx in pool_indices:
            e = self.entries[idx]
            if e.file not in seen_files:
                seen_files.add(e.file)
                unique_entries.append(e)

        # 打分与硬拒过滤
        scored: List[Tuple[int, Entry]] = []
        q_version_tokens = version_tokens(title)
        q_artist_tokens = artist_tokens(artist)
        q_primary_artist = primary_artist(artist)
        al_norm = norm_album(album)

        for e in unique_entries:
            # 硬拒判定
            # 1. 标题宽松都不等
            if not any(v.title_loose == t_loose for v in e.variants):
                continue

            # 2. 双方都有艺人，但交集为空 -> 硬拒
            entry_all_artists = set()
            for v in e.variants:
                entry_all_artists.update(v.artist_tokens)
            if q_artist_tokens and entry_all_artists and not (q_artist_tokens & entry_all_artists):
                continue

            # 3. 版本互斥硬拒
            # 例如查询是录音室版本（无 live 词），而条目明确包含 live 词
            has_version_conflict = False
            for v in e.variants:
                # 若查询无版本特征，而候选带版本特征 -> 冲突
                diff = v.version_tokens - q_version_tokens
                if diff and any(k in diff for k in ("live", "现场", "演唱会", "remix", "混音", "demo")):
                    has_version_conflict = True
                    break
            if has_version_conflict:
                continue

            # 打分加总 (满分 90, 达标 >= 60)
            score = 0
            # 标题维度 (+30 / +20)
            if any(v.title_strict == t_strict for v in e.variants):
                score += 30
            else:
                score += 20

            # 艺人维度 (最高一档，互斥)
            if any(v.artist_tokens == q_artist_tokens or (v.primary_artist and v.primary_artist == q_primary_artist) for v in e.variants):
                score += 40
            elif q_artist_tokens and entry_all_artists and q_artist_tokens.issubset(entry_all_artists):
                score += 30
            else:
                jaccard = len(q_artist_tokens & entry_all_artists) / max(1, len(q_artist_tokens | entry_all_artists))
                if jaccard >= 0.5:
                    score += 25
                elif q_artist_tokens & entry_all_artists:
                    score += 15

            # 版本一致加分 (+15)
            if any(v.version_tokens == q_version_tokens for v in e.variants):
                score += 15

            # 专辑一致加分 (+5)
            if al_norm and al_norm not in GENERIC_ALBUMS and al_norm in e.album_norms:
                score += 5

            if score >= 60:
                scored.append((score, e))

        scored.sort(key=lambda x: -x[0])
        return [item[1] for item in scored[:2]]
