# -*- coding: utf-8 -*-
"""容器标签读取、安全写入与磁盘回读门禁适配器 (v1.8.2 R4-C)。

满足《歌曲检测与修补标准》§6 全部要求及方案铁律：
1. FLAC 映射 lyrics (ELRC) + ttmllyric (TTML) + lyricist/composer；
2. MP3/WAV 映射单帧 USLT::eng (ELRC) + TXXX:TTMLLYRIC (TTML) + TEXT/TCOM；
3. 严格保护现有封面、ReplayGain、TPE2/albumartist、TCON/genre、TRCK、ISRC 等未授权字段；
4. 写入前在独立工作副本上操作，禁止原地修改；
5. 写入后立即从磁盘物理回读，执行二次 R1 审计与字段保真校验；
6. 任何校验失败立即销毁副本并回滚，绝不交付损坏文件。
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, Optional

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3,
    TCOM,
    TEXT,
    TXXX,
    USLT,
)
from mutagen.wave import WAVE

from ..auditors.elrc_ttml_comparator import compare_elrc_ttml


def read_container_tags(filepath: str) -> Dict[str, Any]:
    """只读提取 FLAC / MP3 / WAV 的全量元数据与双格式歌词。"""
    info: Dict[str, Any] = {
        "title": "",
        "artist": "",
        "album": "",
        "year": "",
        "composer": "",
        "lyricist": "",
        "lyrics": "",
        "ttml": "",
        "albumartist": "",
        "tracknumber": "",
        "genre": "",
        "isrc": "",
        "has_cover": False,
        "replaygain": {},
    }

    if not os.path.exists(filepath):
        return info

    try:
        audio = mutagen.File(filepath)
    except Exception:
        audio = None

    if audio is None:
        try:
            id3 = ID3(filepath)
            _extract_id3_all(id3, info)
            info["has_cover"] = any(k.startswith("APIC") for k in id3.keys())
        except Exception:
            pass
        return info

    if isinstance(audio, FLAC):
        info["title"] = audio.get("title", [""])[0]
        info["artist"] = "/".join(audio.get("artist", [""]))
        info["album"] = audio.get("album", [""])[0]
        info["year"] = audio.get("date", [""])[0]
        info["composer"] = audio.get("composer", [""])[0]
        info["lyricist"] = audio.get("lyricist", [""])[0]
        info["tracknumber"] = audio.get("tracknumber", [""])[0]
        info["albumartist"] = "/".join(audio.get("albumartist", [""]))
        info["genre"] = audio.get("genre", [""])[0]
        info["isrc"] = (audio.get("isrc") or audio.get("ISRC") or [""])[0]
        info["lyrics"] = audio.get("lyrics", [""])[0]
        info["ttml"] = (audio.get("ttmllyric") or audio.get("TTMLLYRIC") or [""])[0]
        info["has_cover"] = bool(audio.pictures)
        for k in audio.keys():
            if k.lower().startswith("replaygain_"):
                info["replaygain"][k.lower()] = audio.get(k)

    elif isinstance(audio, WAVE):
        if getattr(audio, "tags", None):
            _extract_id3_all(audio.tags, info)
            info["has_cover"] = any(k.startswith("APIC") for k in audio.tags.keys())

    else:
        # MP3 及通用 ID3 容器
        tags = getattr(audio, "tags", None)
        if tags is None:
            try:
                tags = ID3(filepath)
            except Exception:
                tags = None
        if tags is not None:
            _extract_id3_all(tags, info)
            info["has_cover"] = any(k.startswith("APIC") for k in tags.keys())

    return info


def _extract_id3_all(id3: Any, info: Dict[str, Any]) -> None:
    """从 ID3 结构中提取完整字段（含 TXXX:TTMLLYRIC 与 ReplayGain）。"""
    def _get(key: str) -> str:
        try:
            return str(id3.get(key, ""))
        except Exception:
            return ""

    info["title"] = _get("TIT2")
    info["artist"] = _get("TPE1")
    info["albumartist"] = _get("TPE2")
    info["album"] = _get("TALB")
    info["year"] = _get("TDRC") or _get("TYER")
    info["composer"] = _get("TCOM")
    info["lyricist"] = _get("TEXT")
    info["tracknumber"] = _get("TRCK")
    info["genre"] = _get("TCON")
    info["isrc"] = _get("TSRC")

    # 兼容读取 TXXX:LYRICIST 与 TXXX:COMPOSER
    for k, frame in id3.items():
        if isinstance(frame, TXXX):
            desc = (frame.desc or "").upper()
            if desc == "TTMLLYRIC":
                info["ttml"] = frame.text[0] if frame.text else ""
            elif desc == "LYRICIST" and not info["lyricist"]:
                info["lyricist"] = frame.text[0] if frame.text else ""
            elif desc == "COMPOSER" and not info["composer"]:
                info["composer"] = frame.text[0] if frame.text else ""
            elif desc.startswith("REPLAYGAIN_"):
                info["replaygain"][desc] = frame.text[0] if frame.text else ""

    # USLT 主歌词
    for k, frame in id3.items():
        if isinstance(frame, USLT):
            info["lyrics"] = frame.text
            break


def write_verified_standardized_container(
    source_audio_path: str,
    target_work_path: str,
    elrc_text: str,
    ttml_text: str,
    lyricist: Optional[str] = None,
    composer: Optional[str] = None,
) -> Dict[str, Any]:
    """在独立工作副本中写入双格式歌词与词曲标签，并执行磁盘回读硬校验。
    
    返回回读审计摘要 dict。若回读比对失败，立即抛出 RuntimeError 并清理副本。
    """
    if not os.path.exists(source_audio_path):
        raise FileNotFoundError(f"Source audio file not found: {source_audio_path}")

    # 1. 保存原文件标签快照（用于写后比对受保护字段）
    original_tags = read_container_tags(source_audio_path)

    # 2. 复制到受控工作副本目录
    os.makedirs(os.path.dirname(os.path.abspath(target_work_path)), exist_ok=True)
    shutil.copyfile(source_audio_path, target_work_path)

    try:
        audio = mutagen.File(target_work_path)
        if audio is None:
            audio = ID3(target_work_path)

        if isinstance(audio, FLAC):
            audio["lyrics"] = [elrc_text]
            audio["ttmllyric"] = [ttml_text]
            if lyricist:
                audio["lyricist"] = [lyricist.strip()]
            if composer:
                audio["composer"] = [composer.strip()]
            audio.save()

        elif isinstance(audio, WAVE):
            if audio.tags is None:
                audio.add_tags()
            id3 = audio.tags
            _apply_id3_standard_lyrics(id3, elrc_text, ttml_text, lyricist, composer)
            audio.save()

        else:
            # MP3 及普通 ID3
            id3 = getattr(audio, "tags", None)
            if id3 is None:
                try:
                    id3 = ID3(target_work_path)
                except Exception:
                    id3 = ID3()
            _apply_id3_standard_lyrics(id3, elrc_text, ttml_text, lyricist, composer)
            if hasattr(audio, "save"):
                audio.save()
            else:
                id3.save(target_work_path)

        # 3. 磁盘物理回读门禁
        readback_tags = read_container_tags(target_work_path)

        # 验证歌词写入保真度
        if readback_tags.get("lyrics") != elrc_text:
            raise RuntimeError("Disk readback verification failed: ELRC lyrics content mismatch.")
        if readback_tags.get("ttml") != ttml_text:
            raise RuntimeError("Disk readback verification failed: TTML content mismatch.")

        # 验证词曲写入
        if lyricist and readback_tags.get("lyricist") != lyricist.strip():
            raise RuntimeError(f"Disk readback verification failed: Lyricist '{readback_tags.get('lyricist')}' != '{lyricist}'.")
        if composer and readback_tags.get("composer") != composer.strip():
            raise RuntimeError(f"Disk readback verification failed: Composer '{readback_tags.get('composer')}' != '{composer}'.")

        # 验证受保护字段未被破坏
        if original_tags["has_cover"] and not readback_tags["has_cover"]:
            raise RuntimeError("CRITICAL: Embedded cover was corrupted or removed during tag write!")
        for protected_field in ("title", "artist", "album", "year", "tracknumber", "genre", "albumartist", "isrc"):
            orig_v = original_tags.get(protected_field, "")
            read_v = readback_tags.get(protected_field, "")
            if orig_v and read_v != orig_v:
                raise RuntimeError(f"CRITICAL: Protected metadata '{protected_field}' was corrupted! ('{read_v}' != '{orig_v}')")

        # 4. 回读内容重新执行 R1 双向比对
        r1_result = compare_elrc_ttml(readback_tags["lyrics"], readback_tags["ttml"])
        if not r1_result.passed:
            raise RuntimeError(f"Disk readback R1 comparison failed: {r1_result.issues}")

        return {
            "status": "PASS",
            "target_path": target_work_path,
            "readback_tags": readback_tags,
            "r1_max_delta_ms": r1_result.max_time_delta_ms,
        }

    except Exception as exc:
        # 写入或校验失败，强制清理损坏的副本
        if os.path.exists(target_work_path):
            try:
                os.remove(target_work_path)
            except Exception:
                pass
        raise exc


def _apply_id3_standard_lyrics(
    id3: Any,
    elrc_text: str,
    ttml_text: str,
    lyricist: Optional[str] = None,
    composer: Optional[str] = None,
) -> None:
    """安全向 ID3 帧集合写入标准歌词与词曲。"""
    # 清除旧的 USLT / SYLT 避免多重主歌词
    for k in list(id3.keys()):
        if str(k).startswith("USLT") or str(k).startswith("SYLT"):
            del id3[k]

    # 写入单帧 USLT::eng
    id3["USLT::eng"] = USLT(encoding=3, lang="eng", desc="", text=elrc_text)

    # 清除旧的 TXXX:TTMLLYRIC
    for k in list(id3.keys()):
        if str(k).startswith("TXXX") and getattr(id3[k], "desc", "").upper() == "TTMLLYRIC":
            del id3[k]

    # 写入 TXXX:TTMLLYRIC
    id3["TXXX:TTMLLYRIC"] = TXXX(encoding=3, desc="TTMLLYRIC", text=[ttml_text])

    # 词曲
    if lyricist:
        id3["TEXT"] = TEXT(encoding=3, text=[lyricist.strip()])
    if composer:
        id3["TCOM"] = TCOM(encoding=3, text=[composer.strip()])
