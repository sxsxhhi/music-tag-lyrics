# -*- coding: utf-8 -*-
"""容器标签读取、安全写入与磁盘回读门禁适配器 (v1.8.4 R4-C)。

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
import json
from typing import Any, Dict, Optional, Tuple

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    SYLT,
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
                    # STREAMINFO audio decode parameters and MD5 are immutable.
                    h_streaminfo = hashlib.sha256(streaminfo).digest()
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
            h = hashlib.sha256(b"FLAC_STREAMINFO_AND_AUDIO_V2\0")
            h.update(h_streaminfo)
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
                    fmt_bytes = f.read(size)
                    h.update(b"WAV_FORMAT_V2\0")
                    h.update(size.to_bytes(8, "little"))
                    h.update(fmt_bytes)
                    if len(fmt_bytes) != size:
                        return unverified
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
                        ext_bytes = fmt_bytes[16:40]
                        sub_format = int.from_bytes(ext_bytes[8:10], "little")
                        if sub_format not in (1, 3):
                            return unverified
                        pass  # the complete fmt chunk was already consumed
                    else:
                        pass  # the complete fmt chunk was already consumed
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
    from .audio_payload import ogg_vorbis_audio_digest, mp4_audio_digest, dsf_audio_digest
    if ext == ".ogg":
        return ogg_vorbis_audio_digest(filepath)
    if ext in (".m4a", ".mp4"):
        return mp4_audio_digest(filepath)
    if ext == ".dsf":
        return dsf_audio_digest(filepath)
    return "AUDIO_STREAM_NOT_VERIFIED", ""


def read_container_tags(filepath: str) -> Dict[str, Any]:
    """只读提取 FLAC / MP3 / WAV 的全量元数据与双格式歌词（含副轨与同步轨完整盘点）。"""
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
        "extra_lyrics": [],
        "extra_ttml": [],
        "_extra_lyrics_raw": [],
        "_extra_ttml_raw": [],
        "_primary_uslt_key": None,
        "_primary_ttml_key": None,
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

        flac_lyrics = audio.get("lyrics", [])
        if flac_lyrics:
            info["lyrics"] = flac_lyrics[0]
            for idx, item in enumerate(flac_lyrics[1:], start=1):
                item_bytes = item.encode("utf-8")
                info["extra_lyrics"].append({
                    "container": "FLAC",
                    "kind": "lyrics",
                    "key": f"lyrics[{idx}]",
                    "lang": "",
                    "desc": "",
                    "sha256": hashlib.sha256(item_bytes).hexdigest(),
                    "bytes": len(item_bytes),
                })
                info["_extra_lyrics_raw"].append(item)

        flac_ttml = audio.get("ttmllyric") or audio.get("TTMLLYRIC") or []
        if flac_ttml:
            info["ttml"] = flac_ttml[0]
            for idx, item in enumerate(flac_ttml[1:], start=1):
                item_bytes = item.encode("utf-8")
                info["extra_ttml"].append({
                    "container": "FLAC",
                    "kind": "ttmllyric",
                    "key": f"ttmllyric[{idx}]",
                    "sha256": hashlib.sha256(item_bytes).hexdigest(),
                    "bytes": len(item_bytes),
                })
                info["_extra_ttml_raw"].append(item)

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
    """从 ID3 结构中提取完整字段（含 TXXX:TTMLLYRIC、USLT、SYLT 与 ReplayGain）。"""
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

    # Keep *every* value of the selected TXXX:TTMLLYRIC frame. Mutagen
    # stores a multi-value TXXX in one frame; replacing the frame wholesale
    # otherwise silently destroys all trailing values.
    for k, frame in id3.items():
        if isinstance(frame, TXXX):
            desc = (frame.desc or "").upper()
            if desc == "TTMLLYRIC":
                values = list(frame.text or [])
                if info["_primary_ttml_key"] is None:
                    info["_primary_ttml_key"] = str(k)
                    info["ttml"] = values[0] if values else ""
                    secondary = enumerate(values[1:], start=1)
                else:
                    secondary = enumerate(values, start=0)
                for idx, ttml_val in secondary:
                    value_bytes = ttml_val.encode("utf-8")
                    info["extra_ttml"].append({
                        "container": "ID3",
                        "kind": "TXXX:TTMLLYRIC",
                        "key": f"{k}[{idx}]",
                        "sha256": hashlib.sha256(value_bytes).hexdigest(),
                        "bytes": len(value_bytes),
                    })
                    info["_extra_ttml_raw"].append(ttml_val)
            elif desc == "LYRICIST" and not info["lyricist"]:
                info["lyricist"] = frame.text[0] if frame.text else ""
            elif desc == "COMPOSER" and not info["composer"]:
                info["composer"] = frame.text[0] if frame.text else ""
            elif desc.startswith("REPLAYGAIN_"):
                info["replaygain"][desc] = frame.text[0] if frame.text else ""

    # USLT 与 SYLT 完整提取与主副轨判定
    all_uslt = []
    all_sylt = []
    for k, frame in id3.items():
        if isinstance(frame, USLT):
            all_uslt.append((str(k), frame))
        elif isinstance(frame, SYLT):
            all_sylt.append((str(k), frame))

    if all_uslt:
        empty_desc_uslt = [item for item in all_uslt if not getattr(item[1], "desc", "")]
        if empty_desc_uslt:
            eng_empty = next((item for item in empty_desc_uslt if getattr(item[1], "lang", "").lower() == "eng"), None)
            primary_k, primary_frame = eng_empty if eng_empty else empty_desc_uslt[0]
        else:
            # Described-only USLT frames are translations/commentaries, not
            # evidence of an editable primary. Never promote the first one.
            primary_k, primary_frame = None, None

        if primary_frame is not None:
            info["lyrics"] = primary_frame.text or ""
            info["_primary_uslt_key"] = primary_k

        for k, frame in all_uslt:
            if primary_k is not None and k == primary_k:
                continue
            text_bytes = (frame.text or "").encode("utf-8")
            info["extra_lyrics"].append({
                "container": "ID3",
                "kind": "USLT",
                "key": k,
                "lang": getattr(frame, "lang", "") or "",
                "desc": getattr(frame, "desc", "") or "",
                "sha256": hashlib.sha256(text_bytes).hexdigest(),
                "bytes": len(text_bytes),
            })
            info["_extra_lyrics_raw"].append((k, frame))

    for k, frame in all_sylt:
        # Include the complete semantically relevant frame envelope, not just
        # timestamped text; type/format/encoding alterations must fail closed.
        sylt_payload = {
            "lang": str(getattr(frame, "lang", "")),
            "desc": str(getattr(frame, "desc", "")),
            "encoding": int(getattr(frame, "encoding", -1)),
            "format": int(getattr(frame, "format", -1)),
            "type": int(getattr(frame, "type", -1)),
            "text": [[str(t), int(ms)] for t, ms in getattr(frame, "text", [])],
        }
        sylt_bytes = json.dumps(sylt_payload, ensure_ascii=False,
                                sort_keys=True, separators=(",", ":")).encode("utf-8")
        info["extra_lyrics"].append({
            "container": "ID3",
            "kind": "SYLT",
            "key": k,
            "lang": getattr(frame, "lang", "") or "",
            "desc": getattr(frame, "desc", "") or "",
            "sha256": hashlib.sha256(sylt_bytes).hexdigest(),
            "bytes": len(sylt_bytes),
        })
        info["_extra_lyrics_raw"].append((k, frame))


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
    In-place overwrite is intentionally unsupported in v1.8.4.
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
        raise ValueError("Unsupported v1.8.4 output container")

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

    ext = os.path.splitext(target)[1].lower()

    # 快照原始全部标签帧与元数据键值，用于最严苛的未授权元数据不可变性校验
    orig_vorbis_tags = None
    orig_id3_frames = None
    try:
        source_audio = mutagen.File(source)
    except Exception:
        source_audio = None

    if isinstance(source_audio, FLAC):
        orig_vorbis_tags = {k.lower(): list(source_audio.get(k, [])) for k in source_audio.keys()}
    elif isinstance(source_audio, WAVE) and getattr(source_audio, "tags", None):
        orig_id3_frames = dict(source_audio.tags.items())
    elif ext == '.mp3' or (source_audio is not None and getattr(source_audio, "tags", None) and isinstance(source_audio.tags, ID3)):
        try:
            source_id3 = ID3(source)
            orig_id3_frames = dict(source_id3.items())
        except Exception:
            orig_id3_frames = None

    orig_stream_st, orig_stream_hash = compute_audio_stream_digest(source)
    original_hash = _sha_file(source)
    os.makedirs(os.path.dirname(target),exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.mtl182-stage-',suffix=ext,dir=os.path.dirname(target))
    os.close(fd)
    try:
        shutil.copy2(source,tmp)
        audio=mutagen.File(tmp)
        if isinstance(audio,FLAC):
            audio['lyrics'] = [elrc_text] + orig.get('_extra_lyrics_raw', [])
            audio['ttmllyric'] = [ttml_text] + orig.get('_extra_ttml_raw', [])
            if lyricist:audio['lyricist']=[lyricist.strip()]
            if composer:audio['composer']=[composer.strip()]
            audio.save()
        elif isinstance(audio,WAVE):
            if audio.tags is None: audio.add_tags()
            _apply_id3_standard_lyrics(audio.tags, elrc_text, ttml_text, lyricist, composer,
                                       primary_uslt_key=orig.get('_primary_uslt_key'),
                                       primary_ttml_key=orig.get('_primary_ttml_key'))
            audio.save()
        elif ext=='.mp3':
            try:
                id3 = ID3(tmp)
            except ID3NoHeaderError:
                # Only a genuinely absent ID3 header permits creating new tags.
                # Malformed/unreadable existing tags must abort, not be erased.
                id3 = ID3()
            old_v2 = getattr(id3, 'version', (2,4,0))[1]
            _apply_id3_standard_lyrics(id3, elrc_text, ttml_text, lyricist, composer,
                                       primary_uslt_key=orig.get('_primary_uslt_key'),
                                       primary_ttml_key=orig.get('_primary_ttml_key'))
            id3.save(tmp,v2_version=old_v2 if old_v2 in (3,4) else 4)
        else:
            raise ValueError('Unrecognized or unsupported audio container')
        rb=read_container_tags(tmp)
        if rb.get('lyrics')!=elrc_text or rb.get('ttml')!=ttml_text:
            raise RuntimeError('READBACK_LYRICS_MISMATCH')
        for field in ('title','artist','album','year','tracknumber','discnumber',
                      'genre','albumartist','isrc','has_cover','cover_digest','replaygain',
                      'extra_lyrics','extra_ttml'):
            if orig.get(field)!=rb.get(field):
                raise RuntimeError('PROTECTED_METADATA_CHANGED:'+field)
        if orig.get('id3_version') and rb.get('id3_version') != orig.get('id3_version'):
            raise RuntimeError('ID3_VERSION_CHANGED')
        if lyricist and rb.get('lyricist')!=lyricist.strip():
            raise RuntimeError('CREDIT_READBACK_FAILED:lyricist')
        if not lyricist and orig.get('lyricist') != rb.get('lyricist'):
            raise RuntimeError('PROTECTED_METADATA_CHANGED:lyricist')
        if composer and rb.get('composer')!=composer.strip():
            raise RuntimeError('CREDIT_READBACK_FAILED:composer')
        if not composer and orig.get('composer') != rb.get('composer'):
            raise RuntimeError('PROTECTED_METADATA_CHANGED:composer')

        # 全量 Vorbis 注释不可变性回读门禁
        if orig_vorbis_tags is not None:
            rb_flac = FLAC(tmp)
            rb_vorbis = {k.lower(): list(rb_flac.get(k, [])) for k in rb_flac.keys()}
            authorized_vorbis = {'lyrics', 'ttmllyric'}
            if lyricist:
                authorized_vorbis.add('lyricist')
            if composer:
                authorized_vorbis.add('composer')
            for k, v in orig_vorbis_tags.items():
                if k not in authorized_vorbis:
                    if k not in rb_vorbis or rb_vorbis[k] != v:
                        raise RuntimeError(f'PROTECTED_METADATA_CHANGED:vorbis_{k}')
            for k in rb_vorbis:
                if k not in orig_vorbis_tags and k not in authorized_vorbis:
                    raise RuntimeError(f'PROTECTED_METADATA_CHANGED:unexpected_vorbis_{k}')

        # 全量 ID3 帧不可变性回读门禁（含 PRIV / COMM / 自定义 TXXX 等）
        if orig_id3_frames is not None:
            rb_id3_container = mutagen.File(tmp)
            rb_id3 = rb_id3_container.tags if isinstance(rb_id3_container, WAVE) else ID3(tmp)
            authorized_id3 = set()
            if orig.get('_primary_uslt_key'):
                authorized_id3.add(orig['_primary_uslt_key'])
            authorized_id3.add('USLT::eng')
            if orig.get('_primary_ttml_key'):
                authorized_id3.add(orig['_primary_ttml_key'])
            authorized_id3.add('TXXX:TTMLLYRIC')
            if lyricist:
                authorized_id3.add('TEXT')
            if composer:
                authorized_id3.add('TCOM')
            for k, frame in orig_id3_frames.items():
                if k not in authorized_id3:
                    if k not in rb_id3 or rb_id3[k] != frame:
                        raise RuntimeError(f'PROTECTED_METADATA_CHANGED:id3_{k}')
            for k in rb_id3.keys():
                if k not in orig_id3_frames and k not in authorized_id3:
                    raise RuntimeError(f'PROTECTED_METADATA_CHANGED:unexpected_id3_{k}')

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
        # Public result is a deliberately small, serializable verification DTO.
        # rb contains raw lyrics, raw secondary tracks and Mutagen Frame objects;
        # none of them may cross into a JSON report or caller-facing result.
        public_readback = {
            'has_cover': bool(rb['has_cover']),
            'cover_digest': rb['cover_digest'],
            'id3_version': rb['id3_version'],
            'secondary_lyrics_count': len(rb['extra_lyrics']),
            'secondary_ttml_count': len(rb['extra_ttml']),
            'primary_elrc_sha256': hashlib.sha256(rb['lyrics'].encode('utf-8')).hexdigest(),
            'primary_ttml_sha256': hashlib.sha256(rb['ttml'].encode('utf-8')).hexdigest(),
            'protected_metadata_verified': True,
        }
        return {'status':'PASS','target_path':target_work_path,
                'readback_tags':public_readback,'r1_max_delta_ms':r1.max_time_delta_ms,
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
    primary_uslt_key: Optional[str] = None,
    primary_ttml_key: Optional[str] = None,
) -> None:
    """安全向 ID3 帧集合写入标准歌词与词曲，完整保留其余语言与同步歌词轨。"""
    # Only an explicitly identified main USLT may be replaced. Described
    # translation tracks and other empty-desc language tracks remain intact.
    for k in list(id3.keys()):
        if primary_uslt_key is not None and str(k) == primary_uslt_key:
            if isinstance(id3.get(k), USLT):
                del id3[k]

    # The target slot must never hide another unprotected frame.
    eng_slot = id3.get("USLT::eng")
    if eng_slot is not None:
        raise ValueError('AMBIGUOUS_PRIMARY_USLT: canonical slot occupied')
    id3["USLT::eng"] = USLT(encoding=3, lang="eng", desc="", text=elrc_text)

    # Replace only the selected TTML frame, retaining *all trailing values*.
    # Other case-variant TTMLLYRIC frames, if present, are untouched.
    ttml_extra_values = []
    if primary_ttml_key is not None:
        selected = id3.get(primary_ttml_key)
        if not isinstance(selected, TXXX) or not selected.text:
            raise ValueError('AMBIGUOUS_PRIMARY_TTML: selected frame unavailable')
        ttml_extra_values = list(selected.text[1:])
        del id3[primary_ttml_key]
    if id3.get('TXXX:TTMLLYRIC') is not None:
        raise ValueError('AMBIGUOUS_PRIMARY_TTML: canonical slot occupied')
    id3['TXXX:TTMLLYRIC'] = TXXX(encoding=3, desc='TTMLLYRIC',
                                 text=[ttml_text] + ttml_extra_values)

    # 词曲
    if lyricist:
        id3["TEXT"] = TEXT(encoding=3, text=[lyricist.strip()])
    if composer:
        id3["TCOM"] = TCOM(encoding=3, text=[composer.strip()])
