# -*- coding: utf-8 -*-
"""单曲/批量入口：洗标签、多源抓词、无主词全球搜文本后 Demucs GPU；有行无字先问 GPU。"""

import os
import sys
import argparse
from typing import Optional, Dict, Any, List, Tuple

# Reconfigure stdout/stderr for utf-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure skill directory is in sys.path
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SKILL_DIR not in sys.path:
    sys.path.insert(0, SKILL_DIR)

from datetime import date

from scripts.tag_editor import (
    read_audio_tags, update_audio_tags, normalize_artist_string,
    clean_title_string, clean_year_string, infer_title_artist_from_basename,
    extract_year_from_album, has_embedded_cover,
)
from core.fetchers.netease import NetEaseFetcher
from core.fetchers.cover_art import CoverFetcher
from core.version import __version__
from scripts.lyric_validator import (
    validate_and_fix_lyrics, pair_plain_bilingual_lyrics, extract_lyricist_composer,
    is_credit_line,
)
from core.aggregator import LyricAggregator
from core.converter import to_spl
from core.utils.io_safe import atomic_write_many, read_text_guess_encoding
from core.utils.text_utils import is_foreign_track
from core.utils.version_guard import is_live_version, matches_version_policy

SUPPORTED_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".wav", ".dsf"}

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
    target_live = is_live_version(title)
    for c in candidates:
        if not matches_version_policy(target_live, c, ""):
            continue
        p = os.path.join(song_dir, c)
        if os.path.isfile(p):
            txt = read_text_guess_encoding(p)
            if txt:
                return txt
    return None

def _singing_line_count(text: str) -> int:
    from core.plain_lyric_search import lrc_or_plain_to_text
    body = lrc_or_plain_to_text(text or "")
    return len([l for l in body.splitlines() if l.strip()]) if body else 0


def collect_plain_lyrics(
    title: str,
    artist: str,
    filepath: str,
    explicit_text: Optional[str] = None,
    album: str = "",
    duration_ms: int = 0,
) -> Optional[str]:
    """1 --lyrics-file → 2 五源 plain → 3 GitHub/必应/DDG → 4 同目录 txt。"""
    if explicit_text and explicit_text.strip():
        return explicit_text
    print("🌐 五源无同步歌词，正在搜集权威纯文本（GitHub/raw 优先，再必应/DuckDuckGo）...")
    try:
        from core.plain_lyric_search import fetch_plain_lyric_text
        text = fetch_plain_lyric_text(title, artist, album, duration_ms)
        if text and text.strip():
            print(f"✅ 已检索到权威逐行文本（{_singing_line_count(text)} 行）")
            return text
    except Exception as exc:  # noqa: BLE001 - 搜索失败必须继续尝试本地 txt
        print(f"   ℹ️ 全球文本检索失败：{exc}")
    local = find_local_plain_lyrics(filepath, title, artist)
    if local:
        print("📖 使用同目录纯文本/LRC 作为权威歌词")
        return local
    return None


def prompt_gpu_authorization(kind: str, n_lines: int, non_interactive: bool) -> bool:
    """kind: 'line'（已有行轴）或 'plain'（无行轴、仅文本）。非交互对 line 返回 False（走 numpy）。"""
    if non_interactive:
        print("🛑 [--non-interactive] 跳过 Demucs 授权提示，不做任何重型计算。")
        return False

    print("\n" + "=" * 65)
    print(f"🔔 [Demucs v4 GPU 音频剥离与声学打轴授权提示 (v{__version__})]")
    if kind == "line":
        print(f"已有逐行权威歌词（共 {n_lines} 行），但缺少逐字时间戳。")
        print("是否授权用本地 GPU 在行时间窗内声学咬合打轴？（精度优先；拒绝则降级为按行时长合成）")
    else:
        print(f"未能从云端曲库匹配到同步歌词，但已定位到权威歌词文本（共 {n_lines} 行）。")
        print("是否授权按需使用本地 GPU 调用 Demucs v4 剥离纯人声并进行双端能量门禁逐字声学咬合？")
        print("（拒绝后无法 numpy 合成：没有行级时间窗。）")
    print("（绝对遵循权威歌词文本，零 ASR 语音听写；任务完成后自动销毁临时干声文件）")
    print("=" * 65)
    try:
        user_input = input("请输入授权决定 [Y: 授权同意运行 GPU 打轴 / N: 拒绝] (默认 N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n🛑 非交互模式或已取消，安全退出。")
        return False
    if user_input in ("y", "yes"):
        print("✅ 用户已显式授权。准备调用 AcousticAligner 执行 GPU 声学咬合...")
        return True
    print("🛑 用户拒绝授权。不进行重型计算。")
    return False


def _prepare_align_payload(
    title: str,
    artist: str,
    lyrics_text: str,
    existing_lines: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, List[Optional[str]], Optional[List[Tuple[float, str]]]]:
    """外语只对齐原文；已有行轴时带 line_timing_hints（秒）。"""
    if existing_lines:
        orig_lines = []
        trans_by_idx: List[Optional[str]] = []
        hints: List[Tuple[float, str]] = []
        for line in existing_lines:
            text = (line.get("text") or "").strip()
            if not text:
                continue
            orig_lines.append(text)
            trans_by_idx.append((line.get("translation") or "").strip() or None)
            hints.append((float(line.get("start", 0)) / 1000.0, text))
        return "\n".join(orig_lines), trans_by_idx, hints or None

    align_text = lyrics_text
    trans_by_idx = []
    if is_foreign_track(title, artist):
        orig_only, trans_by_idx = pair_plain_bilingual_lyrics(lyrics_text)
        if orig_only.strip():
            align_text = orig_only
        else:
            trans_by_idx = []
    return align_text, trans_by_idx, None


def run_acoustic_align(
    filepath: str,
    title: str,
    artist: str,
    lyrics_text: str,
    existing_lines: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    try:
        from core.acoustic_aligner import AcousticAligner, AcousticEnvironmentError
        align_text, trans_by_idx, hints = _prepare_align_payload(
            title, artist, lyrics_text, existing_lines
        )
        if not align_text.strip():
            print("❌ 权威文本剥离后为空，红线阻断，不听写。")
            return None
        aligned_data = AcousticAligner.align_lyrics(
            audio_path=filepath,
            lyrics_text=align_text,
            line_timing_hints=hints,
            device="cuda",
        )
        if aligned_data and trans_by_idx:
            for i, line in enumerate(aligned_data.get("lines") or []):
                if i < len(trans_by_idx) and trans_by_idx[i]:
                    line["translation"] = trans_by_idx[i]
        return aligned_data
    except AcousticEnvironmentError as e:
        print(f"🛑 声学环境不达标，GPU 打轴已被铁律 20 阻断（拒绝生成伪逐字）：{e}")
        return None
    except ImportError as e:
        print(f"⚠️ 本地 Python 环境未安装 PyTorch/Demucs/Whisper: {e}")
        return None
    except Exception as e:
        print(f"❌ 声学咬合打轴失败: {e}")
        return None


def apply_numpy_word_synth(validated_data: Dict[str, Any]) -> Dict[str, Any]:
    from core.acoustic_aligner import enrich_track_with_acoustic_words
    out = enrich_track_with_acoustic_words(validated_data)
    out["synthesized_words"] = True
    print("   🎼 已降级：按行时长合成逐字时间戳（numpy，非酷狗逐字、非 GPU）")
    return out


def enrich_missing_words(
    validated_data: Dict[str, Any],
    *,
    filepath: str,
    title: str,
    artist: str,
    non_interactive: bool = False,
) -> Dict[str, Any]:
    """有逐字则不动；无逐字先问 GPU（带行窗）；拒绝/非交互/失败才 numpy。"""
    lines = validated_data.get("lines") or []
    # P0-2: 旧条件是 any(len(words)>=2)。只要有一行凑够 2 个词，整首剩余
    # 没有逐字的正文行都会被跳过补齐，产出「逐字行 + 纯文本行」混排，
    # 与铁律 6「三端统一 Enhanced LRC 逐字」不一致。部分逐字的源
    # （LRCSHARE 只有几行带字、KRC 只有间奏段带字）会命中这条。
    # 改为：所有非署名、非空的正文行都已带逐字，才算「无需补齐」。
    body_lines = [
        l for l in lines
        if (l.get("text") or "").strip() and not is_credit_line(l.get("text") or "")
    ]
    if body_lines and all(l.get("words") for l in body_lines):
        return validated_data
    if validated_data.get("gpu_aligned"):
        try:
            return apply_numpy_word_synth(validated_data)
        except Exception as exc:  # noqa: BLE001
            print(f"   ℹ️ 逐字合成失败，保留行级歌词：{exc}")
            return validated_data
    n_lines = len([l for l in lines if (l.get("text") or "").strip()])
    if prompt_gpu_authorization("line", n_lines, non_interactive):
        aligned = run_acoustic_align(
            filepath, title, artist, "", existing_lines=lines
        )
        if aligned and aligned.get("lines"):
            aligned["gpu_aligned"] = True
            aligned.setdefault("source", validated_data.get("source", "acoustic"))
            aligned["lyric_type"] = "word"
            return aligned
        print("   ℹ️ GPU 打轴未成功，降级按行合成。")
    try:
        return apply_numpy_word_synth(validated_data)
    except ImportError as exc:
        print(f"   ℹ️ 无声学依赖，跳过逐字合成：{exc}")
        return validated_data
    except Exception as exc:  # noqa: BLE001 - 合成失败不能拖垮整首
        print(f"   ℹ️ 逐字合成失败，保留行级歌词：{exc}")
        return validated_data


def prompt_demucs_fallback(
    title: str,
    artist: str,
    filepath: str,
    plain_text_lyrics: Optional[str] = None,
    non_interactive: bool = False,
    album: str = "",
    duration_ms: int = 0,
) -> Optional[Dict[str, Any]]:
    """
    无同步 lines 时的 GPU 兜底。先搜集权威文本，再征求授权。
    拒绝或非交互：本首失败（没有行轴，不能 numpy）。
    """
    if not plain_text_lyrics or not plain_text_lyrics.strip():
        if non_interactive:
            print("🛑 [--non-interactive] 跳过 Demucs 授权提示，不做任何重型计算。")
            return None
        plain_text_lyrics = collect_plain_lyrics(
            title, artist, filepath, None, album=album, duration_ms=duration_ms
        )

    if not plain_text_lyrics or not plain_text_lyrics.strip():
        print("\n" + "=" * 65)
        print(f"🛑 [Demucs v4 绝对红线阻断 (v{__version__})]")
        print(f"未能从商业曲库、全球检索与本地文件得到《{title} - {artist}》的权威歌词文本。")
        print("⚠️ 核心铁律：【绝不语音听写】！必须先有正确歌词，再声学打轴。")
        print("👉 可在歌曲目录放置《标题 - 歌手.txt》，或使用 --lyrics-file。")
        print("=" * 65 + "\n")
        return None

    n_lines = _singing_line_count(plain_text_lyrics)
    if not prompt_gpu_authorization("plain", n_lines, non_interactive):
        return None
    aligned = run_acoustic_align(filepath, title, artist, plain_text_lyrics)
    if aligned:
        aligned["gpu_aligned"] = True
        aligned.setdefault("source", "acoustic")
        aligned["lyric_type"] = aligned.get("lyric_type") or "word"
    return aligned

def _resolve_apple_artwork_url(lyric_data: Optional[Dict[str, Any]]) -> str:
    """从 iTunes 缓存取该曲 artworkUrl100（顺风车：lookup 响应天然自带，零额外请求）。"""
    aid = str((lyric_data or {}).get("apple_music_id") or "").strip()
    if not aid:
        return ""
    try:
        from core.fetchers.itunes_client import ITunesClient
        entry = ITunesClient.get_instance().cache.get(f"appleid:{aid}") or {}
        return str(entry.get("artwork_url") or "")
    except Exception:
        return ""


def process_single_song(
    filepath: str,
    update_tags: bool = True,
    output_dir: Optional[str] = None,
    lyrics_only: bool = False,
    force_gpu: bool = False,
    lyrics_file: Optional[str] = None,
    non_interactive: bool = False,
    enable_apple: bool = True,
    no_cover: bool = False,
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

    inferred_title, inferred_artist = infer_title_artist_from_basename(base_name)
    if not title:
        title = inferred_title or base_name
    if not artist:
        artist = inferred_artist

    # 2. Clean title, 4-digit year, and normalize artist with '/'
    cleaned_title = clean_title_string(title)
    normalized_artist = normalize_artist_string(artist)
    cleaned_year = clean_year_string(year)
    album_year = extract_year_from_album(album)
    this_year = str(date.today().year)
    if not cleaned_year or cleaned_year == this_year:
        looked = album_year or NetEaseFetcher.lookup_year(
            cleaned_title, normalized_artist, duration_ms
        )
        if looked and looked != this_year:
            cleaned_year = looked
        elif not cleaned_year:
            cleaned_year = looked or album_year
    lyricist = tags.get("lyricist", "").strip()
    composer = tags.get("composer", "").strip()
    extracted_ly, extracted_co = extract_lyricist_composer(tags.get("lyrics") or "")
    if not lyricist:
        lyricist = extracted_ly
    if not composer:
        composer = extracted_co

    print(f"🏷️  Metadata: [{cleaned_title}] by [{normalized_artist}] ({cleaned_year}) ({duration_ms // 1000}s)")

    # Read external lyrics file if specified
    explicit_lyrics_text = None
    if lyrics_file and os.path.isfile(lyrics_file):
        explicit_lyrics_text = read_text_guess_encoding(lyrics_file)
        if explicit_lyrics_text:
            print(f"📖 Loaded external lyrics file: {lyrics_file}")

    # 3. Lyric retrieval or Demucs fallback
    lyric_data = None
    if force_gpu:
        print("⚡ Force GPU mode requested. Invoking Demucs v4 acoustic aligner directly...")
        lyric_data = prompt_demucs_fallback(
            title=cleaned_title,
            artist=normalized_artist,
            filepath=filepath,
            plain_text_lyrics=explicit_lyrics_text,
            non_interactive=non_interactive,
            album=album,
            duration_ms=duration_ms,
        )
    else:
        print("🔍 Fetching lyrics across multi-engines (AppleTTML/Kugou/AMLL/NetEase/LRCSHARE/LRCLIB)...")
        lyric_data = LyricAggregator.fetch_best_lyrics(
            title=cleaned_title,
            artist=normalized_artist,
            album=album,
            duration_ms=duration_ms,
            enable_apple=enable_apple
        )

        if not lyric_data or not lyric_data.get("lines"):
            lyric_data = prompt_demucs_fallback(
                title=cleaned_title,
                artist=normalized_artist,
                filepath=filepath,
                plain_text_lyrics=explicit_lyrics_text,
                non_interactive=non_interactive,
                album=album,
                duration_ms=duration_ms,
            )

    # 3.5 专辑封面补全（R9）：默认写标签路径生效；已有封面绝不覆盖；OGG 本轮跳过。
    # 在所有早退分支之前完成取图，抓词失败仍可只补元数据+封面的路径同样受益。
    cover_bytes: Optional[bytes] = None
    cover_mime: Optional[str] = None
    if update_tags and not lyrics_only and not no_cover:
        if ext == ".ogg":
            print("   ℹ️ [cover] OGG 封面补全暂不支持，跳过")
        elif has_embedded_cover(filepath):
            print("   🖼️ [cover] 已有内嵌封面，跳过（绝不覆盖）")
        else:
            got = CoverFetcher.get_instance().fetch_cover(
                cleaned_title, normalized_artist, duration_ms,
                apple_artwork_url=_resolve_apple_artwork_url(lyric_data),
            )
            if got:
                cover_bytes, cover_mime = got
            else:
                print("   ℹ️ [cover] 三级源均无可用封面，保持为空")

    if not lyric_data or not lyric_data.get("lines"):
        from core.fetchers.kugou import last_kugou_status
        if last_kugou_status() == "gated":
            print(f"❌ 有候选被门禁丢掉，六源未交出可用歌词：{cleaned_title} - {normalized_artist}")
            print("   （上方应有预览/冲轴/版本互斥原因；禁止说「酷狗没有」）")
        else:
            print(f"❌ 六源均无候选：{cleaned_title} - {normalized_artist}")
        if update_tags and not lyrics_only:
            _write_cleaned_metadata(
                filepath, cleaned_title, normalized_artist, album,
                cleaned_year, composer, lyricist,
                cover_data=cover_bytes, cover_mime=cover_mime,
            )
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
        if update_tags and not lyrics_only:
            _write_cleaned_metadata(
                filepath, cleaned_title, normalized_artist, album,
                cleaned_year, composer, lyricist,
                cover_data=cover_bytes, cover_mime=cover_mime,
            )
        return False

    # P0-2: 逐字必须在校验之后。先让 validator 用「没有 words」完成双语配对，
    # 再处理仍然没有逐字的行：精度优先问 GPU（带行窗），拒绝/非交互才 numpy。
    validated_data = enrich_missing_words(
        validated_data,
        filepath=filepath,
        title=cleaned_title,
        artist=normalized_artist,
        non_interactive=non_interactive,
    )

    if not lyricist or not composer:
        line_text = "\n".join((l.get("text") or "") for l in validated_data.get("lines") or [])
        extracted_ly, extracted_co = extract_lyricist_composer(line_text)
        if not lyricist:
            lyricist = extracted_ly
        if not composer:
            composer = extracted_co

    # 5. Generate .spl and .lrc (with prelude credit enrichment & whitelist protection)
    spl_content = lrc_content = to_spl(validated_data, title=cleaned_title, artist=normalized_artist, lyricist=lyricist, composer=composer)

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
                lyrics=spl_content,
                cover_data=cover_bytes,
                cover_mime=cover_mime
            )
            if success:
                print("✅ Embedded tags updated (Artist with '/', 4-digit Year, In-place Lyrics)")
            else:
                print("⚠️ Tag update skipped or format read-only")

    print(f"✨ Successfully finished processing.")
    return True


def _write_cleaned_metadata(
    filepath: str,
    title: str,
    artist: str,
    album: str,
    year: str,
    composer: str,
    lyricist: str,
    cover_data: Optional[bytes] = None,
    cover_mime: Optional[str] = None,
) -> None:
    """抓词失败时仍写回已清洗/新补的标签，不写歌词帧。封面并入同一次落盘。"""
    success = update_audio_tags(
        filepath,
        title=title,
        artist=artist,
        album=album,
        year=year,
        composer=composer,
        lyricist=lyricist,
        cover_data=cover_data,
        cover_mime=cover_mime
    )
    if success:
        print("✅ Embedded tags updated (lyrics unchanged; fetch/validate failed)")
    else:
        print("⚠️ Tag update skipped or format read-only")


def main():
    parser = argparse.ArgumentParser(description=f"music-tag-lyrics Processor (v{__version__})")
    parser.add_argument("path", help="Path to an audio file or directory")
    parser.add_argument("--no-tags", action="store_true", help="Do not modify audio tags")
    parser.add_argument("--lyrics-only", action="store_true", help="Only update embedded lyrics (USLT), leaving all other metadata tags untouched")
    parser.add_argument("--force-gpu", action="store_true", help="Force GPU acoustic alignment with Demucs v4")
    parser.add_argument("--lyrics-file", default=None, help="Explicit path to plain-text lyrics file")
    parser.add_argument("--out-dir", default=None, help="Custom output directory for lyrics")
    parser.add_argument("--non-interactive", action="store_true",
                        help="不弹 GPU 授权：有行级歌词则 numpy 合成，无行级则本首失败")
    parser.add_argument("--no-apple-ttml", action="store_true",
                        help="禁用 Apple Music TTML 引擎")
    parser.add_argument("--no-cover", action="store_true",
                        help="跳过专辑封面补全（已有封面本来也绝不覆盖）")
    args = parser.parse_args()

    # Apple 档总开关只在此处计算一次（A4 去重，原先单文件/目录两分支各算一遍）
    enable_apple = (not args.no_apple_ttml) and (os.environ.get("MTL_APPLE_TIER") != "off")

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
            non_interactive=args.non_interactive,
            enable_apple=enable_apple,
            no_cover=args.no_cover
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
                            non_interactive=args.non_interactive,
                            enable_apple=enable_apple,
                            no_cover=args.no_cover
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
