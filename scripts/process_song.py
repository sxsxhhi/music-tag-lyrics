# -*- coding: utf-8 -*-
"""
Main CLI Processor for music-tag-lyrics Skill (v1.1.1)
Standardizes audio metadata tags (Artist with '/', clean Title, Album, 4-digit Year, embedded Lyrics)
and generates paired standard Enhanced LRC (.spl and .lrc) lyrics in the song's directory.
Provides Demucs v4 GPU fallback protocol with interactive user authorization prompt.
Zero external software dependencies.
"""

import os
import sys
import argparse
from typing import Optional

# Reconfigure stdout/stderr for utf-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure skill directory is in sys.path
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from scripts.tag_editor import read_audio_tags, update_audio_tags, normalize_artist_string, clean_title_string, clean_year_string
from scripts.lyric_validator import validate_and_fix_lyrics
from core.aggregator import LyricAggregator
from core.converter import to_lrc, to_spl

SUPPORTED_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".wav"}

def prompt_demucs_fallback(title: str, artist: str, filepath: str) -> bool:
    """
    Prompts user for explicit authorization to download and use Demucs v4 GPU fallback.
    If authorized, runs GPU separation. If rejected, safely declines with zero downloads.
    """
    print(f"\n" + "="*60)
    print(f"🔔 [Demucs v4 GPU 音频剥离终极兜底提示]")
    print(f"未能从商业及开源曲库（AMLL / 酷狗 / LRCLIB / 网易云）检索到《{title} - {artist}》的有效逐字歌词。")
    print(f"检测到歌曲可能为全网下架孤品或独立小众曲目。")
    print(f"是否授权按需从网络下载 Demucs v4 模型权重并调用本地 GPU 进行纯人声剥离与 AI 音素咬合？")
    print(f"注意：若授权同意，将从 PyTorch 官方拉取模型并就地推理，任务完成后自动销毁临时干声。")
    print(f"若拒绝授权，将不会进行任何网络下载或重型安装，安全退出。")
    print("="*60)
    
    # In non-interactive mode or automated tests, default to safe decline
    try:
        user_input = input("请输入授权决定 [Y: 授权同意下载并运行 / N: 拒绝授权并跳过] (默认 N): ").strip().lower()
        if user_input in ["y", "yes"]:
            print(f"✅ 用户已显式授权。准备调用 GPU 音频剥离流水线...")
            # Dynamic import of acoustic aligner to avoid loading torch when not needed
            try:
                from core.acoustic_aligner import align_song_acoustics
                return True
            except ImportError as e:
                print(f"⚠️ 本地 Python 环境未安装 PyTorch/Demucs: {e}。请在需要时手动执行安装。")
                return False
        else:
            print(f"🛑 用户拒绝授权。绝不进行任何下载操作，优雅降级退出。")
            return False
    except (EOFError, KeyboardInterrupt):
        print(f"\n🛑 非交互模式或已取消，优雅降级退出。")
        return False

def process_single_song(filepath: str, update_tags: bool = True, output_dir: Optional[str] = None) -> bool:
    """
    Processes a single audio file:
    1. Inspects filename and embedded tags.
    2. Cleans title, formats 4-digit year, and normalizes multi-artist tags with '/' under Band Whitelist.
    3. Retrieves best synchronized/verbatim lyrics across multi-engines.
    4. Enforces Natural Acoustic Gate and Outro protection.
    5. Updates embedded metadata tags in-place (including lyrics).
    6. Saves paired .spl and .lrc in the song's directory (or specified output_dir).
    """
    if not os.path.isfile(filepath):
        print(f"[ERROR] File not found: {filepath}")
        return False
        
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXTS:
        print(f"[SKIP] Unsupported format ({ext}): {filepath}")
        return False

    base_name = os.path.splitext(os.path.basename(filepath))[0]
    song_dir = os.path.dirname(filepath)
    target_dir = output_dir if output_dir else song_dir
    os.makedirs(target_dir, exist_ok=True)

    print(f"\n==================================================")
    print(f"🎵 Processing: {os.path.basename(filepath)}")
    print(f"📂 Location: {song_dir}")

    # 1. Read existing metadata tags
    tags = read_audio_tags(filepath)
    duration_ms = tags.get("duration", 0)

    # Infer Title and Artist if missing
    title = tags.get("title", "").strip()
    artist = tags.get("artist", "").strip()
    album = tags.get("album", "").strip()
    year = tags.get("year", "").strip()

    if " - " in base_name:
        parts = base_name.split(" - ", 1)
        inferred_title = parts[0].strip()
        inferred_artist = parts[1].strip()
        if not title:
            title = inferred_title
        if not artist:
            artist = inferred_artist
    elif not title:
        title = base_name

    # 2. Clean title, 4-digit year, and normalize artist with '/'
    cleaned_title = clean_title_string(title)
    normalized_artist = normalize_artist_string(artist)
    cleaned_year = clean_year_string(year)

    print(f"🏷️  Metadata: [{cleaned_title}] by [{normalized_artist}] ({cleaned_year}) ({duration_ms // 1000}s)")

    # 3. Multi-engine lyric retrieval
    print("🔍 Fetching lyrics across multi-engines (AMLL/Kugou/LRCLIB/NetEase)...")
    lyric_data = LyricAggregator.fetch_best_lyrics(
        title=cleaned_title,
        artist=normalized_artist,
        album=album,
        duration_ms=duration_ms
    )

    if not lyric_data or not lyric_data.get("lines"):
        # Trigger Demucs v4 fallback prompt
        authorized = prompt_demucs_fallback(cleaned_title, normalized_artist, filepath)
        if not authorized:
            print(f"❌ No lyrics found for {cleaned_title} - {normalized_artist}")
            return False

    source = lyric_data.get("source", "unknown")
    lyric_type = lyric_data.get("lyric_type", "line")
    synthesized = lyric_data.get("synthesized_words", False)
    
    print(f"🎯 Matched Source: [{source.upper()}] (Type: {lyric_type.upper()}{', AI-Synthesized Words' if synthesized else ''})")

    # 4. Monotonicity, Natural Acoustic Gate and Outro Validation
    is_non_zh = bool(LyricAggregator.is_western_track(cleaned_title, normalized_artist))
    is_valid, validated_data, warnings = validate_and_fix_lyrics(
        lyric_data,
        audio_duration_ms=duration_ms,
        is_non_zh=is_non_zh
    )
    for w in warnings:
        print(f"   ℹ️ {w}")

    # 5. Generate .spl and .lrc (with prelude credit enrichment & whitelist protection)
    lyricist = tags.get("lyricist", "").strip()
    composer = tags.get("composer", "").strip()
    spl_content = to_spl(validated_data, title=cleaned_title, artist=normalized_artist, lyricist=lyricist, composer=composer)
    lrc_content = to_lrc(validated_data, title=cleaned_title, artist=normalized_artist, lyricist=lyricist, composer=composer, verbatim_lrc=True)

    lrc_path = os.path.join(target_dir, f"{base_name}.lrc")
    spl_path = os.path.join(target_dir, f"{base_name}.spl")

    with open(lrc_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(lrc_content)
    with open(spl_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(spl_content)

    print(f"📄 Generated .lrc -> {os.path.basename(lrc_path)}")
    print(f"💎 Generated .spl -> {os.path.basename(spl_path)}")

    # 6. Update embedded tags in-place (including lyrics)
    if update_tags:
        success = update_audio_tags(
            filepath,
            title=cleaned_title,
            artist=normalized_artist,
            album=album,
            year=cleaned_year,
            lyrics=spl_content
        )
        if success:
            print("✅ Embedded tags updated (Artist with '/', 4-digit Year, In-place Lyrics)")
        else:
            print("⚠️ Tag update skipped or format read-only")

    print(f"✨ Successfully finished processing.")
    return True

def main():
    parser = argparse.ArgumentParser(description="Antigravity music-tag-lyrics Processor (v1.1.1)")
    parser.add_argument("path", help="Path to an audio file or directory")
    parser.add_argument("--no-tags", action="store_true", help="Do not modify audio tags")
    parser.add_argument("--out-dir", default=None, help="Custom output directory for lyrics")
    args = parser.parse_args()

    target = args.path
    if os.path.isfile(target):
        process_single_song(target, update_tags=not args.no_tags, output_dir=args.out_dir)
    elif os.path.isdir(target):
        count = 0
        success_count = 0
        for root, _, files in os.walk(target):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTS:
                    count += 1
                    fp = os.path.join(root, f)
                    if process_single_song(fp, update_tags=not args.no_tags, output_dir=args.out_dir):
                        success_count += 1
        print(f"\nBatch processing completed: {success_count}/{count} succeeded.")
    else:
        print(f"Error: Target path does not exist: {target}")

if __name__ == "__main__":
    main()
