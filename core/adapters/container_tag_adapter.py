# -*- coding: utf-8 -*-
"""容器标签读取、安全写入与磁盘回读门禁适配器 (v1.8.3 R4-C)。

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
import tempfile
import hashlib
from typing import Any, Dict, Optional, Tuple

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TCOM,
    TEXT,
    TXXX,
    USLT,
)
from mutagen.wave import WAVE

from ..auditors.elrc_ttml_comparator import compare_elrc_ttml
from ..utils.io_safe import sha256_file


def _id3_cover_digest(id3) -> str:
    entries = sorted((str(x.desc), str(x.mime), int(x.type), bytes(x.data))
                     for x in id3.values() if getattr(x, 'FrameID', '') == 'APIC')
    h = hashlib.sha256()
    for desc, mime, typ, data in entries:
        for part in (desc.encode('utf-8'), mime.encode('utf-8'), typ.to_bytes(4, 'big'), data):
            h.update(len(part).to_bytes(8, 'big'))
            h.update(part)
    return h.hexdigest() if entries else ""


def _sha_file(path: str) -> str:
    return sha256_file(path)


def _mpeg_layer3_frame_length(header: bytes) -> Optional[int]:
    """Validate a fixed-bitrate MPEG Layer III frame header and its byte length.

    This is a conservative *boundary* check, not an audio decoder or CRC check.
    Free-format and reserved variants require dedicated validation and are
    deliberately returned as unverified.
    """
    if len(header) != 4:
        return None
    bits = int.from_bytes(header, "big")
    if ((bits >> 21) & 0x7ff) != 0x7ff:
        return None
    version_id = (bits >> 19) & 3
    layer_id = (bits >> 17) & 3
    bitrate_idx = (bits >> 12) & 15
    sample_idx = (bits >> 10) & 3
    if version_id == 1 or layer_id != 1 or bitrate_idx in (0, 15) or sample_idx == 3:
        return None
    bitrate_mpeg1 = (0,32,40,48,56,64,80,96,112,128,160,192,224,256,320,0)
    bitrate_other = (0,8,16,24,32,40,48,56,64,80,96,112,128,144,160,0)
    sample_rates = {3: (44100,48000,32000), 2: (22050,24000,16000), 0: (11025,12000,8000)}
    bitrate = (bitrate_mpeg1 if version_id == 3 else bitrate_other)[bitrate_idx]
    sample_rate = sample_rates[version_id][sample_idx]
    padding = (bits >> 9) & 1
    size = ((144000 if version_id == 3 else 72000) * bitrate) // sample_rate + padding
    return size if size >= 4 else None


def compute_flac_audio_stream_digest(path: str) -> Tuple[str, str]:
    """Hash a bounded FLAC audio frame region, never a missing/truncated one.

    Validate the mandatory STREAMINFO block, every metadata extent, and the
    first FLAC sync prefix. The digest establishes unchanged frame *bytes*,
    not full FLAC decodability or correct audio checksums.
    """
    unverified = ("AUDIO_STREAM_NOT_VERIFIED", "")
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size
            if file_size < 4 + 4 + 34 + 2 or f.read(4) != b"fLaC":
                return unverified
            count = 0
            while True:
                if f.tell() + 4 > file_size:
                    return unverified
                header = f.read(4)
                last = bool(header[0] & 0x80)
                kind = header[0] & 0x7f
                size = int.from_bytes(header[1:], "big")
                if count == 0 and (kind != 0 or size != 34):
                    return unverified
                if kind == 127 or size > file_size - f.tell():
                    return unverified
                if count == 0:
                    streaminfo = f.read(34)
                    if len(streaminfo) != 34:
                        return unverified
                    # STREAMINFO sample rate (20 bits), channels and bit depth.
                    params = int.from_bytes(streaminfo[10:18], "big")
                    sample_rate = (params >> 44) & 0xfffff
                    if sample_rate == 0:
                        return unverified
                else:
                    f.seek(size, os.SEEK_CUR)
                count += 1
                if last:
                    break
            audio_start = f.tell()
            # A two-byte sync marker is not a FLAC frame. Require room for
            # the fixed header fields, header CRC and final frame CRC before
            # accepting an audio region as a candidate stream.
            if file_size - audio_start < 12:
                return unverified
            sync = f.read(2)
            if sync[0] != 0xff or (sync[1] & 0xfe) != 0xf8:
                return unverified
            f.seek(audio_start)
            h = hashlib.sha256()
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
            return "VERIFIED", h.hexdigest()
    except (OSError, ValueError, OverflowError, IndexError):
        return unverified


def compute_wav_audio_stream_digest(path: str) -> Tuple[str, str]:
    """Hash nonempty PCM data within validated RIFF and chunk bounds."""
    unverified = ("AUDIO_STREAM_NOT_VERIFIED", "")
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size
            if file_size < 12 or f.read(4) != b"RIFF":
                return unverified
            riff_size = int.from_bytes(f.read(4), "little")
            if f.read(4) != b"WAVE" or riff_size < 4 or 8 + riff_size > file_size:
                return unverified
            riff_end = 8 + riff_size
            seen_fmt = False
            data_chunks = 0
            h = hashlib.sha256()
            while f.tell() + 8 <= riff_end:
                chunk = f.read(8)
                name = chunk[:4]
                size = int.from_bytes(chunk[4:], "little")
                if size > riff_end - f.tell():
                    return unverified
                if name == b"fmt ":
                    if seen_fmt or size < 16:
                        return unverified
                    fmt_bytes = f.read(16)
                    audio_format = int.from_bytes(fmt_bytes[0:2], "little")
                    channels = int.from_bytes(fmt_bytes[2:4], "little")
                    sample_rate = int.from_bytes(fmt_bytes[4:8], "little")
                    byte_rate = int.from_bytes(fmt_bytes[8:12], "little")
                    block_align = int.from_bytes(fmt_bytes[12:14], "little")
                    bits_per_sample = int.from_bytes(fmt_bytes[14:16], "little")
                    if channels == 0 or sample_rate == 0 or block_align == 0 or bits_per_sample == 0:
                        return unverified
                    expected_block_align = (channels * bits_per_sample + 7) // 8
                    if block_align != expected_block_align:
                        return unverified
                    if byte_rate != sample_rate * block_align:
                        return unverified
                    if audio_format not in (1, 3, 0xFFFE):
                        return unverified
                    if audio_format == 0xFFFE:
                        if size < 40:
                            return unverified
                        ext_bytes = f.read(24)
                        sub_format = int.from_bytes(ext_bytes[8:10], "little")
                        if sub_format not in (1, 3):
                            return unverified
                        f.seek(size - 40, os.SEEK_CUR)
                    else:
                        f.seek(size - 16, os.SEEK_CUR)
                    if size & 1:
                        f.seek(1, os.SEEK_CUR)
                    seen_fmt = True
                    continue
                if name == b"data":
                    if not seen_fmt or size == 0:
                        return unverified
                    data_chunks += 1
                    left = size
                    while left:
                        block = f.read(min(1024 * 1024, left))
                        if not block:
                            return unverified
                        h.update(block)
                        left -= len(block)
                    if size & 1:
                        f.seek(1, os.SEEK_CUR)
                else:
                    # Other chunks may be metadata; padding remains in RIFF.
                    f.seek(size + (size & 1), os.SEEK_CUR)
                if f.tell() > riff_end:
                    return unverified
            if f.tell() != riff_end:
                return unverified
            return ("VERIFIED", h.hexdigest()) if (data_chunks and seen_fmt) else unverified
    except (OSError, ValueError, OverflowError):
        return unverified


def compute_mp3_audio_stream_digest(path: str) -> Tuple[str, str]:
    """Hash nonempty MPEG Layer III frames after validating basic boundaries.

    Verifies MPEG sync/header lengths for at least two consecutive frames and
    the ID3/APE metadata envelopes. A verified digest means *unchanged bytes*,
    not that an entire MP3 bitstream was decoded or every frame CRC passed.
    """
    unverified = ("AUDIO_STREAM_NOT_VERIFIED", "")
    try:
        with open(path, "rb") as f:
            file_size = os.fstat(f.fileno()).st_size
            if file_size < 8:
                return unverified
            initial = f.read(10)
            start = 0
            if initial.startswith(b"ID3"):
                if len(initial) != 10 or initial[3] not in (2,3,4) or any(b & 0x80 for b in initial[6:10]):
                    return unverified
                tag_size = ((initial[6] & 0x7f) << 21) | ((initial[7] & 0x7f) << 14) | ((initial[8] & 0x7f) << 7) | (initial[9] & 0x7f)
                start = 10 + tag_size + (10 if initial[5] & 0x10 else 0)
                if start >= file_size:
                    return unverified
            elif initial[:3] == b"ID3":
                return unverified
            end = file_size
            if end >= 128:
                f.seek(end - 128)
                if f.read(3) == b"TAG":
                    end -= 128
            if end - start >= 32:
                f.seek(end - 32)
                if f.read(8) == b"APETAGEX":
                    f.seek(end - 32 + 12)
                    raw = f.read(4)
                    if len(raw) != 4:
                        return unverified
                    ape_size = int.from_bytes(raw, "little")
                    if ape_size < 32 or ape_size > end - start:
                        return unverified
                    end -= ape_size
            if end - start < 8:
                return unverified
            # Walk the complete frame region, not merely two prefix headers.
            # Reject truncation or arbitrary payload trailing after valid frames.
            pos = start
            frame_count = 0
            while pos < end:
                if pos + 4 > end:
                    return unverified
                f.seek(pos)
                frame_length = _mpeg_layer3_frame_length(f.read(4))
                if frame_length is None or pos + frame_length > end:
                    return unverified
                pos += frame_length
                frame_count += 1
            if frame_count < 2 or pos != end:
                return unverified
            f.seek(start)
            h = hashlib.sha256()
            left = end - start
            while left:
                block = f.read(min(1024 * 1024, left))
                if not block:
                    return unverified
                h.update(block)
                left -= len(block)
            return "VERIFIED", h.hexdigest()
    except (OSError, ValueError, OverflowError, IndexError):
        return unverified


def compute_audio_stream_digest(filepath: str) -> Tuple[str, str]:
    """Dispatch conservative audio boundary and payload digest verification."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".flac":
        return compute_flac_audio_stream_digest(filepath)
    if ext == ".wav":
        return compute_wav_audio_stream_digest(filepath)
    if ext == ".mp3":
        return compute_mp3_audio_stream_digest(filepath)
    return "AUDIO_STREAM_NOT_VERIFIED", ""


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
        "discnumber": "",
        "genre": "",
        "isrc": "",
        "has_cover": False,
        "cover_digest": "",
        "id3_version": "",
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
            info["cover_digest"] = _id3_cover_digest(id3)
            info["id3_version"] = str(getattr(id3,"version", ""))
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
        info["discnumber"] = audio.get("discnumber", [""])[0]
        info["albumartist"] = "/".join(audio.get("albumartist", [""]))
        info["genre"] = audio.get("genre", [""])[0]
        info["isrc"] = (audio.get("isrc") or audio.get("ISRC") or [""])[0]
        info["lyrics"] = audio.get("lyrics", [""])[0]
        info["ttml"] = (audio.get("ttmllyric") or audio.get("TTMLLYRIC") or [""])[0]
        info["has_cover"] = bool(audio.pictures)
        info["cover_digest"] = hashlib.sha256(b"".join(
            bytes(p.data) + p.mime.encode('utf-8') + int(p.type).to_bytes(4,'big')
            for p in audio.pictures)).hexdigest() if audio.pictures else ""
        for k in audio.keys():
            if k.lower().startswith("replaygain_"):
                info["replaygain"][k.lower()] = audio.get(k)

    elif isinstance(audio, WAVE):
        if getattr(audio, "tags", None):
            _extract_id3_all(audio.tags, info)
            info["has_cover"] = any(k.startswith("APIC") for k in audio.tags.keys())
            info["cover_digest"] = _id3_cover_digest(audio.tags)
            info["id3_version"] = str(getattr(audio.tags,"version", ""))

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
            info["cover_digest"] = _id3_cover_digest(tags)
            info["id3_version"] = str(getattr(tags,"version", ""))

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
    info["discnumber"] = _get("TPOS")
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
    """Write to a private, unique same-directory staging copy, validate and
    atomically publish ONLY to a target that did not previously exist.

    Unlike os.replace, os.link prevents overwrite on a concurrent target
    creation; failure cleanup affects exclusively our own temporary path.
    In-place overwrite is intentionally unsupported in v1.8.3.
    """
    source = os.path.realpath(source_audio_path)
    target = os.path.realpath(target_work_path)
    if not os.path.isfile(source):
        raise FileNotFoundError("Source audio file not found")
    if source == target or (os.path.exists(target) and os.path.samefile(source, target)):
        raise ValueError("IN_PLACE_DISABLED: existing audio cannot be overwritten")
    if os.path.lexists(target_work_path):
        raise FileExistsError("TARGET_EXISTS: destination is already occupied")
    if os.path.splitext(target)[1].lower() not in ('.flac','.mp3','.wav'):
        raise ValueError("Unsupported v1.8.3 output container")

    # Check generated pair BEFORE making any copy.
    comp=compare_elrc_ttml(elrc_text,ttml_text)
    if not comp.passed:
        raise ValueError("FORMAT_COMPARISON_FAILED: "+",".join(e.code for e in comp.issues))
    orig=read_container_tags(source)
    for field,val in (("lyricist",lyricist),("composer",composer)):
        if val and orig.get(field) and orig[field].strip()!=val.strip():
            raise ValueError("REVIEW_REQUIRED_CREDIT_CONFLICT:"+field)
    if lyricist and orig.get('lyricist'): lyricist=None
    if composer and orig.get('composer'): composer=None
    orig_stream_st, orig_stream_hash = compute_audio_stream_digest(source)
    original_hash = _sha_file(source)
    os.makedirs(os.path.dirname(target),exist_ok=True)
    ext = os.path.splitext(target)[1]
    fd,tmp=tempfile.mkstemp(prefix='.mtl182-stage-',suffix=ext,dir=os.path.dirname(target))
    os.close(fd)
    try:
        shutil.copy2(source,tmp)
        audio=mutagen.File(tmp)
        if isinstance(audio,FLAC):
            audio['lyrics']=[elrc_text];audio['ttmllyric']=[ttml_text]
            if lyricist:audio['lyricist']=[lyricist.strip()]
            if composer:audio['composer']=[composer.strip()]
            audio.save()
        elif isinstance(audio,WAVE):
            if audio.tags is None: audio.add_tags()
            _apply_id3_standard_lyrics(audio.tags,elrc_text,ttml_text,lyricist,composer)
            audio.save()
        elif ext=='.mp3':
            try:
                id3 = ID3(tmp)
            except ID3NoHeaderError:
                # Only a genuinely absent ID3 header permits creating new tags.
                # Malformed/unreadable existing tags must abort, not be erased.
                id3 = ID3()
            old_v2 = getattr(id3, 'version', (2,4,0))[1]
            _apply_id3_standard_lyrics(id3,elrc_text,ttml_text,lyricist,composer)
            id3.save(tmp,v2_version=old_v2 if old_v2 in (3,4) else 4)
        else:
            raise ValueError('Unrecognized or unsupported audio container')
        rb=read_container_tags(tmp)
        if rb.get('lyrics')!=elrc_text or rb.get('ttml')!=ttml_text:
            raise RuntimeError('READBACK_LYRICS_MISMATCH')
        for field in ('title','artist','album','year','tracknumber','discnumber',
                      'genre','albumartist','isrc','has_cover','cover_digest','replaygain'):
            if orig.get(field)!=rb.get(field):
                raise RuntimeError('PROTECTED_METADATA_CHANGED:'+field)
        if orig.get('id3_version') and rb.get('id3_version') != orig.get('id3_version'):
            raise RuntimeError('ID3_VERSION_CHANGED')
        if lyricist and rb.get('lyricist')!=lyricist.strip():
            raise RuntimeError('CREDIT_READBACK_FAILED:lyricist')
        if composer and rb.get('composer')!=composer.strip():
            raise RuntimeError('CREDIT_READBACK_FAILED:composer')
        r1=compare_elrc_ttml(rb['lyrics'],rb['ttml'])
        if not r1.passed:
            raise RuntimeError('READBACK_FORMAT_MISMATCH')
        if _sha_file(source)!=original_hash:
            raise RuntimeError('SOURCE_CHANGED_DURING_WRITE')

        # Audio stream invariance verification
        rb_stream_st, rb_stream_hash = compute_audio_stream_digest(tmp)
        if orig_stream_st != "VERIFIED" or rb_stream_st != "VERIFIED":
            raise RuntimeError("AUDIO_STREAM_NOT_VERIFIED")
        if orig_stream_hash != rb_stream_hash:
            raise RuntimeError("AUDIO_STREAM_CORRUPTED")

        # Link is an atomic, non-clobbering commit to an absent target.
        # Never unlink an existing target, including in an exception handler.
        os.link(tmp,target_work_path)
        return {'status':'PASS','target_path':target_work_path,
                'readback_tags':rb,'r1_max_delta_ms':r1.max_time_delta_ms,
                'audio_stream_digest': rb_stream_hash,
                'audio_stream_status': 'VERIFIED'}
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


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
