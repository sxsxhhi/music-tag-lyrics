# -*- coding: utf-8 -*-
"""Apple Music TTML 独立转换与单曲诊断 CLI。百行轻量管道，直接对接 to_spl 与 atomic_write_many。"""

import os
import re
import sys
import json
import argparse
import tempfile
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.parsers.ttml_parser import parse_ttml
from core.version import __version__
from core.fetchers.apple_ttml import AppleTTMLFetcher
from core.converter import to_spl
from core.utils.io_safe import atomic_write_many
from scripts.tag_editor import read_audio_tags
from scripts.process_song import SUPPORTED_EXTS

_APPLE_I_RE = re.compile(r"[?&]i=(\d+)")
_APPLE_SONG_RE = re.compile(r"/song/[^/]+/(\d+)")
_APPLE_ID_RE = re.compile(r"(?:[?&]id=|/id)(\d+)")
_APPLE_DIGITS_RE = re.compile(r"^\d+$")


def extract_apple_id(value: str) -> Optional[str]:
    """从纯数字、music.apple.com 链接或 id= 参数抽出 Apple 曲目 ID。抽不到返回 None。"""
    if not value:
        return None
    s = value.strip()
    if _APPLE_DIGITS_RE.match(s):
        return s
    m = _APPLE_I_RE.search(s)
    if m:
        return m.group(1)
    m = _APPLE_SONG_RE.search(s)
    if m:
        return m.group(1)
    m = _APPLE_ID_RE.search(s)
    if m:
        return m.group(1)
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Apple Music TTML to Enhanced LRC / SPL Converter (v{__version__})")
    parser.add_argument("input", nargs="?", default=None, help="Audio file or .ttml file")
    parser.add_argument("--apple-id", default=None, help="Apple Music ID or music.apple.com URL")
    parser.add_argument("--title", default="", help="Song title")
    parser.add_argument("--artist", default="", help="Artist name")
    parser.add_argument("--album", default="", help="Album name")
    parser.add_argument("--out-dir", default=None, help="Output directory")
    parser.add_argument("--stdout", action="store_true", help="Print LRC to stdout")
    parser.add_argument("--json", action="store_true", help="Print JSON metadata to stdout")
    parser.add_argument("--no-cache", action="store_true", help="Bypass local cache")

    args = parser.parse_args(argv)

    if args.no_cache:
        from core.fetchers.amll_index import AmllIndex
        from core.fetchers.itunes_client import ITunesClient
        from core.utils.cache_paths import set_cache_dir_override
        tmp = Path(tempfile.mkdtemp(prefix="mtl-nocache-"))
        os.environ["MTL_CACHE_DIR"] = str(tmp)
        set_cache_dir_override(tmp)
        AmllIndex.reset_instance()
        ITunesClient.reset_instance()

    inp = args.input
    apple_id = args.apple_id
    title = args.title
    artist = args.artist
    album = args.album
    duration_ms = 0
    stem = "lyrics"
    target_dir = Path(args.out_dir) if args.out_dir else Path.cwd()

    data = None

    # 1. 输入判定
    if apple_id:
        extracted = extract_apple_id(apple_id)
        looks_like_url = "music.apple.com" in apple_id or apple_id.startswith("http")
        if looks_like_url and not extracted:
            print(f"❌ [apple-ttml] 无法从链接提取 Apple ID: {apple_id}")
            return 2
        if extracted:
            apple_id = extracted
        elif not _APPLE_DIGITS_RE.match(str(apple_id).strip()):
            print(f"❌ [apple-ttml] 无效的 Apple ID: {apple_id}")
            return 2
        data, verdict = AppleTTMLFetcher.fetch_by_apple_id_ex(apple_id)
        if not data:
            # 退出码语义：1=AMLL 未收录；3=镜像不可达（与未收录严格区分）；4=内容不合格
            if verdict in ("UNREACHABLE", "SUSPECT_MISSING"):
                code, msg = 3, f"镜像不可达 (verdict={verdict})"
            elif verdict == "NOT_FOUND":
                code, msg = 1, "AMLL 未收录"
            else:
                code, msg = 4, f"抓取失败 (verdict={verdict})"
            if not args.stdout and not args.json:
                print(f"❌ [apple-ttml] {msg}: {apple_id}")
            return code
        stem = f"apple_{apple_id}"
        title = data.get("tags", {}).get("ti") or title
        artist = data.get("tags", {}).get("ar") or artist

    elif inp and inp.lower().endswith(".ttml") and os.path.isfile(inp):
        try:
            raw_text = Path(inp).read_text(encoding="utf-8")
            data = parse_ttml(raw_text)
            data["source"] = "apple-ttml"
            data["lyric_type"] = "word"
            data["time_precision"] = 3
            stem = Path(inp).stem
            title = data.get("tags", {}).get("ti") or title
            artist = data.get("tags", {}).get("ar") or artist
            if not args.out_dir:
                target_dir = Path(inp).parent
        except Exception as exc:
            print(f"❌ [apple-ttml] 解析 TTML 文件失败: {exc}")
            return 4

    elif inp and os.path.isfile(inp):
        ext = os.path.splitext(inp)[1].lower()
        if ext in SUPPORTED_EXTS:
            tags = read_audio_tags(inp)
            title = tags.get("title") or Path(inp).stem
            artist = tags.get("artist") or ""
            album = tags.get("album") or ""
            duration_ms = tags.get("duration") or 0
            stem = Path(inp).stem
            if not args.out_dir:
                target_dir = Path(inp).parent
            data = AppleTTMLFetcher.fetch(title, artist=artist, duration_ms=duration_ms, album=album)
            if not data:
                print(f"❌ [apple-ttml] 未匹配到可用歌词或门禁未过: {title} - {artist}")
                return 1
        else:
            print(f"❌ [apple-ttml] 不支持的文件格式: {ext}")
            return 2

    elif title:
        data = AppleTTMLFetcher.fetch(title, artist=artist, duration_ms=duration_ms, album=album)
        if not data:
            print(f"❌ [apple-ttml] 未匹配到歌词: {title} - {artist}")
            return 1
        stem = f"{title} - {artist}".strip(" -")

    else:
        parser.print_help()
        return 2

    if not data or not data.get("lines"):
        return 1

    # 2. 导出 Enhanced LRC
    try:
        elrc_text = to_spl(data, title=title, artist=artist)
    except Exception as exc:
        print(f"❌ [apple-ttml] 转写 Enhanced LRC 失败: {exc}")
        return 4

    # 3. 输出交付
    if args.json:
        out_dict = {
            "ok": True,
            "title": title,
            "artist": artist,
            "lines_count": len(data["lines"]),
            "match": data.get("match", {}),
            "text": elrc_text
        }
        print(json.dumps(out_dict, ensure_ascii=False, indent=2))
        return 0

    if args.stdout:
        print(elrc_text)
        return 0

    # 双写 .lrc 与 .spl
    target_dir.mkdir(parents=True, exist_ok=True)
    lrc_path = target_dir / f"{stem}.lrc"
    spl_path = target_dir / f"{stem}.spl"

    content_bytes = elrc_text.encode("utf-8")
    ok = atomic_write_many([
        (str(lrc_path), content_bytes),
        (str(spl_path), content_bytes),
    ])

    if ok:
        print(f"✅ [apple-ttml] 成功交付 3 位毫秒歌词:")
        print(f"   -> {lrc_path}")
        print(f"   -> {spl_path}")
        return 0
    else:
        print(f"❌ [apple-ttml] 歌词写盘失败")
        return 5


if __name__ == "__main__":
    sys.exit(main())
