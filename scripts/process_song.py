# -*- coding: utf-8 -*-
"""
Main CLI Processor for music-tag-lyrics Skill (v1.2.4)
Standardizes audio metadata tags (Artist with '/', clean Title, Album, 4-digit Year, embedded Lyrics)
and generates paired standard Enhanced LRC (.spl and .lrc) lyrics in the song's directory.
Provides Demucs v4 GPU fallback protocol with strict acoustic alignment:
【核心红线】：绝不进行 ASR 语音听写！必须先从全网/本地检索权威歌词，在正确歌词基础上进行声学逐字打轴。
Zero external software dependencies.
"""

import os
import sys
import re
import argparse
from typing import Optional, Dict, Any

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
from core.utils.io_safe import atomic_write_many
from core.utils.text_utils import is_foreign_track

SUPPORTED_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".wav"}

def find_local_plain_lyrics(song_path: str, title: str, artist: str) -> Optional[str]:
    """
    Searches for local plain-text/LRC lyrics in the same directory:
    1. [Title - Artist.txt]
    2. [Artist - Title.txt]
    3. [base_name.txt]
    4. [Title.txt]
    5. [base_name.lrc]
    """
    song_dir = os.path.dirname(song_path)
    base_name = os.path.splitext(os.path.basename(song_path))[0]
    
    candidates = [
        f"{title} - {artist}.txt",
        f"{artist} - {title}.txt",
        f"{base_name}.txt",
        f"{title}.txt",
        f"{base_name}.lrc"
    ]
    for c in candidates:
        p = os.path.join(song_dir, c)
        if os.path.isfile(p):
            for enc in ["utf-8", "utf-8-sig", "gbk", "gb18030"]:
                try:
                    with open(p, "r", encoding=enc) as f:
                        txt = f.read().strip()
                    if txt:
                        return txt
                except Exception:
                    continue
    return None

def prompt_demucs_fallback(
    title: str, 
    artist: str, 
    filepath: str, 
    plain_text_lyrics: Optional[str] = None,
    non_interactive: bool = False
) -> Optional[Dict[str, Any]]:
    """
    Prompts user for explicit authorization to use Demucs v4 GPU fallback.
    【核心红线】：绝不进行 ASR 语音听写！若无权威逐行文本，立即阻断并退出。
    若存在权威文本并获得用户授权，调用 AcousticAligner 执行双端声学咬合打轴。
    """
    # 非交互模式：绝不阻塞等待输入（批量跑批时 input() 会永久挂死）
    if non_interactive:
        print("🛑 [--non-interactive] 跳过 Demucs 授权提示，不做任何重型计算。")
        return None

    # 1. Check if plain text lyrics are available
    if not plain_text_lyrics or not plain_text_lyrics.strip():
        plain_text_lyrics = find_local_plain_lyrics(filepath, title, artist)

    if not plain_text_lyrics or not plain_text_lyrics.strip():
        print(f"\n" + "="*65)
        print(f"🛑 [Demucs v4 绝对红线阻断 (v1.2.4)]")
        print(f"未能从商业曲库检索到《{title} - {artist}》的有效歌词，且本地未提供纯文本歌词。")
        print(f"⚠️ 核心铁律：【绝不语音听写，使用 Demucs v4 兜底时必须先从全网检索歌词，在正确歌词的基础上进行逐字打轴】！")
        print(f"👉 解决方案：请在歌曲所在目录下放置纯文本歌词文件：")
        print(f"   《{title} - {artist}.txt》或《{os.path.splitext(os.path.basename(filepath))[0]}.txt》")
        print(f"   放置完成后重新运行本程序，即可进行精准声学逐字咬合打轴。")
        print("="*65 + "\n")
        return None

    # Count singing lines (filtering out metadata)
    raw_lines = plain_text_lyrics.splitlines()
    singing_lines = [
        l.strip() for l in raw_lines 
        if l.strip() and not re.match(r"^\[(ti|ar|al|by|offset):", l.strip(), re.IGNORECASE)
    ]
    
    print(f"\n" + "="*65)
    print(f"🔔 [Demucs v4 GPU 音频剥离与声学打轴授权提示 (v1.2.4)]")
    print(f"未能从云端曲库直接匹配到现成逐字歌词，但已定位到权威歌词文本（共 {len(singing_lines)} 行）。")
    print(f"是否授权按需使用本地 GPU 调用 Demucs v4 剥离纯人声并进行双端能量门禁逐字声学咬合？")
    print(f"（注意：绝对遵循权威歌词文本，零 ASR 语音听写；任务完成后自动销毁临时干声文件）")
    print("="*65)
    
    try:
        user_input = input("请输入授权决定 [Y: 授权同意运行 GPU 打轴 / N: 拒绝授权并跳过] (默认 N): ").strip().lower()
        if user_input in ["y", "yes"]:
            print(f"✅ 用户已显式授权。准备调用 AcousticAligner 执行 GPU 声学咬合...")
            try:
                from core.acoustic_aligner import AcousticAligner
                aligned_data = AcousticAligner.align_lyrics(
                    audio_path=filepath,
                    lyrics_text=plain_text_lyrics,
                    device='cuda'
                )
                return aligned_data
            except ImportError as e:
                print(f"⚠️ 本地 Python 环境未安装 PyTorch/Demucs/Whisper: {e}")
                return None
            except Exception as e:
                print(f"❌ 声学咬合打轴失败: {e}")
                return None
        else:
            print(f"🛑 用户拒绝授权。安全退出，不进行任何重型计算。")
            return None
    except (EOFError, KeyboardInterrupt):
        print(f"\n🛑 非交互模式或已取消，安全退出。")
        return None

def process_single_song(
    filepath: str, 
    update_tags: bool = True, 
    output_dir: Optional[str] = None,
    lyrics_only: bool = False,
    force_gpu: bool = False,
    lyrics_file: Optional[str] = None,
    non_interactive: bool = False
) -> bool:
    """
    Processes a single audio file:
    1. Inspects filename and embedded tags.
    2. Cleans title, formats 4-digit year, and normalizes multi-artist tags with '/' under Band Whitelist.
    3. Retrieves best synchronized/verbatim lyrics across multi-engines (or Demucs v4 fallback).
    4. Enforces Natural Acoustic Gate, Pause Gap Tags, and Outro protection.
    5. Updates embedded metadata tags in-place (supports lyrics-only protection).
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
    # duration 为 None 表示"未知"（不是 0 秒）；归一成 0 交给门禁按"未知"处理
    duration_ms = tags.get("duration") or 0
    if not duration_ms:
        # P1-2: 不能静默降级 —— 时长未知时所有时长门禁都会失效，必须让用户知道
        print("   ℹ️ 音频时长未知（标签/容器解析失败），本轮跳过所有时长门禁，可能选中错版本")

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

    # Read external lyrics file if specified
    explicit_lyrics_text = None
    if lyrics_file and os.path.isfile(lyrics_file):
        for enc in ["utf-8", "utf-8-sig", "gbk", "gb18030"]:
            try:
                with open(lyrics_file, "r", encoding=enc) as f:
                    explicit_lyrics_text = f.read().strip()
                if explicit_lyrics_text:
                    print(f"📖 Loaded external lyrics file: {lyrics_file}")
                    break
            except Exception:
                continue

    # 3. Lyric retrieval or Demucs fallback
    lyric_data = None
    if force_gpu:
        print("⚡ Force GPU mode requested. Invoking Demucs v4 acoustic aligner directly...")
        lyric_data = prompt_demucs_fallback(
            title=cleaned_title,
            artist=normalized_artist,
            filepath=filepath,
            plain_text_lyrics=explicit_lyrics_text,
            non_interactive=non_interactive
        )
    else:
        print("🔍 Fetching lyrics across multi-engines (Kugou/AMLL/LRCSHARE/NetEase/LRCLIB)...")
        lyric_data = LyricAggregator.fetch_best_lyrics(
            title=cleaned_title,
            artist=normalized_artist,
            album=album,
            duration_ms=duration_ms
        )

        if not lyric_data or not lyric_data.get("lines"):
            # Trigger Demucs v4 fallback prompt
            lyric_data = prompt_demucs_fallback(
                title=cleaned_title, 
                artist=normalized_artist, 
                filepath=filepath,
                plain_text_lyrics=explicit_lyrics_text,
                non_interactive=non_interactive
            )

    if not lyric_data or not lyric_data.get("lines"):
        print(f"❌ No lyrics found or authorized for {cleaned_title} - {normalized_artist}")
        return False

    source = lyric_data.get("source", "unknown")
    lyric_type = lyric_data.get("lyric_type", "line")

    print(f"🎯 Matched Source: [{source.upper()}] (Type: {lyric_type.upper()})")

    # 4. Monotonicity, Natural Acoustic Gate and Outro Validation
    # P1-3: 语种判定统一走 is_foreign_track。旧实现用 is_western_track（日韩被判为"非外语"），
    # 与 aggregator 里的 is_foreign_track 口径冲突，导致日/韩曲的双语译文行不会被剥离。
    is_non_zh = bool(is_foreign_track(cleaned_title, normalized_artist))
    is_valid, validated_data, warnings = validate_and_fix_lyrics(
        lyric_data,
        audio_duration_ms=duration_ms,
        is_non_zh=is_non_zh
    )
    for w in warnings:
        print(f"   ℹ️ {w}")

    # P0-3: 校验失败必须终止。旧实现解出 is_valid 后直接丢弃，纯译文曲被剥成空行
    # 依然写盘、依然 return True，产物只剩标题行却报告成功。
    if not is_valid:
        print(f"❌ 校验未通过（有效歌词行为 0），不写盘：{cleaned_title} - {normalized_artist}")
        return False

    # P0-2: 逐字合成必须在校验之后。先让 validator 用"没有 words"的干净数据
    # 完成双语配对/剥离，再给仍然没有逐字的行补合成时间戳；顺序颠倒会让
    # 每一行都先带上 words，译文行就再也剥不掉了。
    has_words = any(len(l.get("words") or []) >= 2 for l in validated_data.get("lines", []))
    if not has_words:
        try:
            from core.acoustic_aligner import enrich_track_with_acoustic_words
            validated_data = enrich_track_with_acoustic_words(validated_data)
            validated_data["synthesized_words"] = True
            # P1-9: 这行提示必须放在合成之后 —— 旧实现读标记在读之前，永远显示不出来
            print("   🎼 无逐字来源，已按行时长合成逐字时间戳（AI-Synthesized Words）")
        except ImportError as exc:
            print(f"   ℹ️ 无声学依赖，跳过逐字合成：{exc}")
        except Exception as exc:  # noqa: BLE001 - 合成失败不能拖垮整首
            print(f"   ℹ️ 逐字合成失败，保留行级歌词：{exc}")

    # 5. Generate .spl and .lrc (with prelude credit enrichment & whitelist protection)
    lyricist = tags.get("lyricist", "").strip()
    composer = tags.get("composer", "").strip()
    spl_content = to_spl(validated_data, title=cleaned_title, artist=normalized_artist, lyricist=lyricist, composer=composer)
    lrc_content = to_lrc(validated_data, title=cleaned_title, artist=normalized_artist, lyricist=lyricist, composer=composer)

    lrc_path = os.path.join(target_dir, f"{base_name}.lrc")
    spl_path = os.path.join(target_dir, f"{base_name}.spl")

    # D-06 + P1-9: 两份产物一次性原子写入 —— 先都写临时文件，全部成功才替换，
    # 避免"lrc 写成功、spl 失败"留下半对产物。
    atomic_write_many({lrc_path: lrc_content, spl_path: spl_content})

    print(f"📄 Generated .lrc -> {os.path.basename(lrc_path)}")
    print(f"💎 Generated .spl -> {os.path.basename(spl_path)}")

    # 6. Update embedded tags in-place (including lyrics)
    if update_tags:
        if lyrics_only:
            # Strictly update USLT/lyrics frame, keeping all other metadata untouched
            success = update_audio_tags(
                filepath,
                lyrics=spl_content
            )
            if success:
                print("✅ Embedded lyrics updated (User-defined metadata tags 100% untouched)")
            else:
                print("⚠️ Tag update skipped or format read-only")
        else:
            success = update_audio_tags(
                filepath,
                title=cleaned_title,
                artist=normalized_artist,
                album=album,
                year=cleaned_year,
                composer=composer,
                lyricist=lyricist,
                lyrics=spl_content
            )
            if success:
                print("✅ Embedded tags updated (Artist with '/', 4-digit Year, In-place Lyrics)")
            else:
                print("⚠️ Tag update skipped or format read-only")

    print(f"✨ Successfully finished processing.")
    return True

def main():
    parser = argparse.ArgumentParser(description="music-tag-lyrics Processor (v1.2.4)")
    parser.add_argument("path", help="Path to an audio file or directory")
    parser.add_argument("--no-tags", action="store_true", help="Do not modify audio tags")
    parser.add_argument("--lyrics-only", action="store_true", help="Only update embedded lyrics (USLT), leaving all other metadata tags untouched")
    parser.add_argument("--force-gpu", action="store_true", help="Force GPU acoustic alignment with Demucs v4")
    parser.add_argument("--lyrics-file", default=None, help="Explicit path to plain-text lyrics file")
    parser.add_argument("--out-dir", default=None, help="Custom output directory for lyrics")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never block on stdin: skip the Demucs authorization prompt (batch/CI use)")
    args = parser.parse_args()

    target = args.path
    if os.path.isfile(target):
        # P1-10: 单文件模式也返回真实退出码，脚本化调用才能判成败
        ok = process_single_song(
            target, 
            update_tags=not args.no_tags, 
            output_dir=args.out_dir,
            lyrics_only=args.lyrics_only,
            force_gpu=args.force_gpu,
            lyrics_file=args.lyrics_file,
            non_interactive=args.non_interactive
        )
        return 0 if ok else 1
    elif os.path.isdir(target):
        count = 0
        success_count = 0
        for root, _, files in os.walk(target):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in SUPPORTED_EXTS:
                    count += 1
                    fp = os.path.join(root, f)
                    # P1-10: 单曲异常不能中断整批
                    try:
                        ok = process_single_song(
                            fp, 
                            update_tags=not args.no_tags, 
                            output_dir=args.out_dir,
                            lyrics_only=args.lyrics_only,
                            force_gpu=args.force_gpu,
                            lyrics_file=args.lyrics_file,
                            non_interactive=args.non_interactive
                        )
                    except Exception as exc:  # noqa: BLE001 - 单曲失败不能拖垮整批
                        print(f"❌ 处理失败，已跳过继续：{fp}\n   {type(exc).__name__}: {exc}")
                        ok = False
                    if ok:
                        success_count += 1
        print(f"\nBatch processing completed: {success_count}/{count} succeeded.")
        return 0 if success_count == count else 1
    else:
        print(f"Error: Target path does not exist: {target}")
        return 2

if __name__ == "__main__":
    sys.exit(main())
