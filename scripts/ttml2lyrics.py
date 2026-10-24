# -*- coding: utf-8 -*-
"""Apple Music TTML 独立转换与单曲诊断 CLI。百行轻量管道，直接对接 to_spl 与 atomic_write_many。"""

import os
import sys
import json
import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.parsers.ttml_parser import parse_ttml
from core.fetchers.apple_ttml import AppleTTMLFetcher, fetch_ttml_raw
from core.converter import to_spl
from core.utils.io_safe import atomic_write_many
from scripts.tag_editor import read_audio_tags
from scripts.process_song import SUPPORTED_EXTS


def main(argv=None):
    parser = argparse.ArgumentParser(description="Apple Music TTML to Enhanced LRC / SPL Converter (v1.3.0)")
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
        # 支持从 URL 提取 ID
        if "music.apple.com" in apple_id:
            import re
            m = re.search(r"id(?:=|\/)(\d+)", apple_id)
            if m:
                apple_id = m.group(1)
        data = AppleTTMLFetcher.fetch_by_apple_id(apple_id)
        if not data:
            if not args.stdout and not args.json:
                print(f"❌ [apple-ttml] 未收录或网络不可达: {apple_id}")
            return 1
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
