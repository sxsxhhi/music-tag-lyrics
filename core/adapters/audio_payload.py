# -*- coding: utf-8 -*-
"""Conservative metadata-independent audio digests for Ogg Vorbis, MP4 audio and DSF.

These are byte/stream-structure invariants, NOT codec decoders. Unsupported,
ambiguous or malformed layouts return NOT_VERIFIED; callers must fail closed.
"""
from __future__ import annotations

import hashlib
import os
import struct
from typing import Tuple

UNVERIFIED = ("AUDIO_STREAM_NOT_VERIFIED", "")


def _crc_table():
    rows = []
    for val in range(256):
        c = val << 24
        for _ in range(8):
            c = ((c << 1) ^ (0x04C11DB7 if c & 0x80000000 else 0)) & 0xffffffff
        rows.append(c)
    return rows

_OGG_CRC_TABLE = _crc_table()


def _ogg_checksum(page: bytes) -> int:
    check = 0
    for b in page:
        check = ((check << 8) & 0xffffffff) ^ _OGG_CRC_TABLE[((check >> 24) ^ b) & 255]
    return check


def ogg_vorbis_audio_digest(path: str) -> Tuple[str, str]:
    """Validate every Ogg page CRC and hash all non-comment Vorbis packets.

    Audio packet order, page-granule (duration) at final packet, identification
    and codec setup are protected; vendor/user comments may change.
    Chained/multiplexed streams and extensions are explicitly unsupported.
    """
    try:
        digest = hashlib.sha256(b"OGG_VORBIS_AUDIO_V1\0")
        with open(path, "rb") as f:
            stream_id = None
            expected_seq = None
            previous_granule = -1
            packet = bytearray()
            packet_index = 0
            pages = 0
            eos = False
            while True:
                header = f.read(27)
                if not header:
                    break
                if len(header) != 27 or header[:4] != b"OggS" or header[4] != 0:
                    return UNVERIFIED
                flags = header[5]
                if flags & ~0x07:
                    return UNVERIFIED
                granule = int.from_bytes(header[6:14], "little")
                serial = int.from_bytes(header[14:18], "little")
                seq = int.from_bytes(header[18:22], "little")
                declared_crc = int.from_bytes(header[22:26], "little")
                lengths = f.read(header[26])
                if len(lengths) != header[26]:
                    return UNVERIFIED
                payload_size = sum(lengths)
                body = f.read(payload_size)
                if len(body) != payload_size:
                    return UNVERIFIED
                page = bytearray(header + lengths + body)
                page[22:26] = b"\0" * 4
                if _ogg_checksum(page) != declared_crc:
                    return UNVERIFIED
                if pages == 0:
                    if not flags & 0x02 or flags & 0x01:
                        return UNVERIFIED
                    stream_id = serial
                    expected_seq = seq
                elif serial != stream_id or flags & 0x02 or eos:
                    return UNVERIFIED
                if seq != expected_seq:
                    return UNVERIFIED
                expected_seq = (expected_seq + 1) & 0xffffffff
                if bool(flags & 0x01) != bool(packet):
                    return UNVERIFIED
                if flags & 0x04:
                    eos = True
                offset = 0
                for index, size in enumerate(lengths):
                    packet += body[offset:offset + size]
                    offset += size
                    if len(packet) > 32 * 1024 * 1024:
                        return UNVERIFIED
                    if size < 255:
                        if packet_index == 0:
                            if len(packet) < 30 or packet[:7] != b"\x01vorbis":
                                return UNVERIFIED
                            channels = packet[11]
                            sample_rate = int.from_bytes(packet[12:16], "little")
                            if channels == 0 or sample_rate == 0:
                                return UNVERIFIED
                            digest.update(b"IDENT")
                            digest.update(len(packet).to_bytes(8, "big"))
                            digest.update(packet)
                        elif packet_index == 1:
                            if packet[:7] != b"\x03vorbis":
                                return UNVERIFIED
                            # Ogg comments are mutable metadata, deliberately excluded.
                        elif packet_index == 2:
                            if packet[:7] != b"\x05vorbis":
                                return UNVERIFIED
                            digest.update(b"SETUP")
                            digest.update(len(packet).to_bytes(8, "big"))
                            digest.update(packet)
                        else:
                            if not packet or packet[0] & 1:
                                return UNVERIFIED
                            digest.update(b"DATA")
                            digest.update(len(packet).to_bytes(8, "big"))
                            digest.update(packet)
                        packet_index += 1
                        packet.clear()
                # Protect exact Ogg granule position for every audio-bearing page.
                # Header/comment-only pages may be repaginated by metadata editing.
                if packet_index > 3 and granule != 0xffffffffffffffff:
                    if previous_granule > granule:
                        return UNVERIFIED
                    digest.update(b"GRAN")
                    digest.update(granule.to_bytes(8, "big"))
                    previous_granule = granule
                pages += 1
            if packet or not eos or packet_index < 4 or pages < 3:
                return UNVERIFIED
        return ("VERIFIED", digest.hexdigest())
    except (OSError, ValueError, OverflowError, IndexError, MemoryError):
        return UNVERIFIED


def _atoms(f, start: int, end: int):
    """Iterate bounded ISO-BMFF boxes; reject overlaps, invalid extends and gaps."""
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise ValueError("partial atom header")
        f.seek(pos)
        raw = f.read(8)
        length = int.from_bytes(raw[:4], "big")
        typ = raw[4:8]
        header = 8
        if length == 1:
            if pos + 16 > end:
                raise ValueError("partial largesize")
            length = int.from_bytes(f.read(8), "big")
            header = 16
        elif length == 0:
            length = end - pos
        if length < header or length > end - pos:
            raise ValueError("invalid atom bounds")
        yield typ, pos + header, pos + length, length - header
        pos += length
    if pos != end:
        raise ValueError("unclosed atom region")


def mp4_audio_digest(path: str) -> Tuple[str, str]:
    """Hash all mdat payloads and essential unchanging audio track descriptors.

    Chunk-offset tables are intentionally excluded because metadata edits move
    the mdat box; track codec description (stsd), movie and media time bases (mvhd/mdhd),
    durations and sample timing remain protected. Unsupported or malformed MP4 layouts fail closed.
    """
    try:
        from mutagen.mp4 import MP4
        info = MP4(path).info
        sr = int(getattr(info, "sample_rate", 0) or 0)
        ch = int(getattr(info, "channels", 0) or 0)
        if sr <= 0 or ch <= 0:
            return UNVERIFIED
        length = os.path.getsize(path)
        if length < 32:
            return UNVERIFIED
        h = hashlib.sha256(b"MP4_AUDIO_PAYLOAD_V1\0")
        h.update(struct.pack(">II", sr, ch))
        mdat_count = 0
        saw_ftyp = False
        saw_moov = False
        descriptor_count = 0
        chunk_locations = []
        mdat_regions = []
        with open(path, "rb") as f:
            for typ, begin, end, size in _atoms(f, 0, length):
                if typ == b"ftyp":
                    saw_ftyp = True
                elif typ == b"moov":
                    if saw_moov:
                        return UNVERIFIED
                    saw_moov = True
                    # Movie-level timescale/duration also determine playback
                    # duration, even when audio frames and mdhd are unchanged.
                    # User metadata (udta/meta) and absolute chunk offsets are
                    # intentionally excluded. Exactly one mvhd is mandatory.
                    moov_atoms = list(_atoms(f, begin, end))
                    movie_headers = [(b, e) for t, b, e, _ in moov_atoms if t == b"mvhd"]
                    if len(movie_headers) != 1:
                        return UNVERIFIED
                    mvhd_start, mvhd_end = movie_headers[0]
                    f.seek(mvhd_start)
                    mvhd_bytes = f.read(mvhd_end - mvhd_start)
                    if len(mvhd_bytes) < 20 or mvhd_bytes[0] not in (0, 1):
                        return UNVERIFIED
                    # Version 0 uses 32-bit timescale/duration; version 1
                    # uses 64-bit dates and duration. Zero timescale invalid.
                    timescale_offset = 12 if mvhd_bytes[0] == 0 else 20
                    if len(mvhd_bytes) < timescale_offset + 8 or int.from_bytes(
                            mvhd_bytes[timescale_offset:timescale_offset+4], 'big') == 0:
                        return UNVERIFIED
                    h.update(b"MOVIE_HEADER_FULL_V1\0")
                    h.update(len(mvhd_bytes).to_bytes(8, 'big'))
                    h.update(mvhd_bytes)
                    # Only protect track decode / playback tables. Exclude
                    # mutable user metadata and relocation-dependent offsets.
                    for t, b, e, _ in moov_atoms:
                        if t != b"trak":
                            continue
                        track_atoms = list(_atoms(f, b, e))
                        tkhd = None
                        edit_lists = []
                        for tk, tb, te, ts in track_atoms:
                            if tk == b"tkhd":
                                f.seek(tb)
                                tkhd = f.read(ts)
                            elif tk == b"edts":
                                for ek, eb, ee, es in _atoms(f, tb, te):
                                    if ek == b"elst":
                                        f.seek(eb)
                                        edit_lists.append(f.read(es))
                        for m, mb, me, _ in track_atoms:
                            if m != b"mdia":
                                continue
                            # mdhd stores media timescale/duration; hdlr identifies audio track.
                            mdhd = None
                            is_audio = False
                            minf = None
                            for k, kb, ke, ks in _atoms(f, mb, me):
                                if k == b"mdhd":
                                    f.seek(kb)
                                    mdhd = f.read(ks)
                                elif k == b"hdlr":
                                    f.seek(kb)
                                    data = f.read(min(ks, 16))
                                    is_audio = len(data) >= 12 and data[8:12] == b"soun"
                                elif k == b"minf":
                                    minf = (kb, ke)
                            if not is_audio or not minf or mdhd is None:
                                continue
                            if not tkhd:
                                return UNVERIFIED
                            h.update(b"TKHD")
                            h.update(len(tkhd).to_bytes(4, "big"))
                            h.update(tkhd)
                            for edit in edit_lists:
                                h.update(b"ELST")
                                h.update(len(edit).to_bytes(4, "big"))
                                h.update(edit)
                            h.update(b"MDHD")
                            h.update(len(mdhd).to_bytes(4, "big"))
                            h.update(mdhd)
                            for k, kb, ke, _ in _atoms(f, *minf):
                                if k != b"stbl":
                                    continue
                                saw_stsd = False
                                for p, pb, pe, ps in _atoms(f, kb, ke):
                                    if p in (b"stsd", b"stts", b"stsz", b"ctts", b"stsc"):
                                        f.seek(pb)
                                        data = f.read(ps)
                                        if len(data) != ps:
                                            raise ValueError("short sample table")
                                        h.update(p)
                                        h.update(len(data).to_bytes(8, "big"))
                                        h.update(data)
                                        if p == b"stsd":
                                            saw_stsd = True
                                    elif p in (b"stco", b"co64"):
                                        f.seek(pb)
                                        payload = f.read(ps)
                                        if len(payload) != ps or len(payload) < 8:
                                            raise ValueError("invalid chunk offset table")
                                        count = int.from_bytes(payload[4:8], "big")
                                        unit = 4 if p == b"stco" else 8
                                        if count <= 0 or count > 20000000 or ps != 8 + count * unit:
                                            raise ValueError("invalid chunk count")
                                        for n in range(count):
                                            offset = int.from_bytes(payload[8+n*unit:8+(n+1)*unit], "big")
                                            chunk_locations.append(offset)
                                if saw_stsd:
                                    descriptor_count += 1
                elif typ == b"mdat":
                    if size == 0:
                        return UNVERIFIED
                    mdat_count += 1
                    mdat_regions.append((begin, end))
                    h.update(b"mdat")
                    h.update(size.to_bytes(8, "big"))
                    f.seek(begin)
                    remaining = size
                    while remaining:
                        block = f.read(min(1024 * 1024, remaining))
                        if not block:
                            return UNVERIFIED
                        h.update(block)
                        remaining -= len(block)
        if not (saw_ftyp and saw_moov and mdat_count and descriptor_count == 1
                and chunk_locations):
            return UNVERIFIED
        # Tag rewrites may move mdat, so absolute stco/co64 pointers change.
        # Hash each pointer relative to its actual mdat payload: audio sample
        # relocation within that payload must not be silently accepted.
        h.update(b"NORMALIZED_CHUNK_OFFSETS_V1\0")
        for pointer in chunk_locations:
            for index, (begin, end) in enumerate(mdat_regions):
                if begin <= pointer < end:
                    h.update(index.to_bytes(4, "big"))
                    h.update((pointer - begin).to_bytes(8, "big"))
                    break
            else:
                return UNVERIFIED
        return "VERIFIED", h.hexdigest()
    except Exception:
        # Mutagen/parsers can raise format-specific errors; all fail closed.
        return UNVERIFIED


def dsf_audio_digest(path: str) -> Tuple[str, str]:
    """Protect DSF fmt block and DSD payload (exclude trailing mutable ID3)."""
    try:
        with open(path, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            head = f.read(28)
            if len(head) != 28 or head[:4] != b"DSD " or int.from_bytes(head[4:12], "little") != 28:
                return UNVERIFIED
            declared = int.from_bytes(head[12:20], "little")
            metadata_offset = int.from_bytes(head[20:28], "little")
            if declared != size or metadata_offset and (metadata_offset >= size or metadata_offset < 28):
                return UNVERIFIED
            fmt_head = f.read(12)
            if fmt_head[:4] != b"fmt " or len(fmt_head) != 12:
                return UNVERIFIED
            fmt_len = int.from_bytes(fmt_head[4:12], "little")
            if fmt_len < 52 or fmt_len > size - 28:
                return UNVERIFIED
            fmt_payload = f.read(fmt_len - 12)
            if len(fmt_payload) != fmt_len - 12:
                return UNVERIFIED
            if (int.from_bytes(fmt_payload[12:16], "little") == 0 or
                    int.from_bytes(fmt_payload[16:20], "little") == 0):
                return UNVERIFIED
            data_head = f.read(12)
            if len(data_head) != 12 or data_head[:4] != b"data":
                return UNVERIFIED
            data_len = int.from_bytes(data_head[4:12], "little")
            if data_len <= 12 or data_len > size - f.tell() + 12:
                return UNVERIFIED
            payload_start = f.tell()
            if metadata_offset and metadata_offset != payload_start + data_len - 12:
                return UNVERIFIED
            h = hashlib.sha256(b"DSF_AUDIO_PAYLOAD_V1\0")
            h.update(fmt_payload)
            h.update((data_len - 12).to_bytes(8, "big"))
            remain = data_len - 12
            while remain:
                block = f.read(min(1024 * 1024, remain))
                if not block:
                    return UNVERIFIED
                h.update(block)
                remain -= len(block)
            if metadata_offset and f.read(3) != b"ID3":
                return UNVERIFIED
            return "VERIFIED", h.hexdigest()
    except (OSError, ValueError, OverflowError, IndexError):
        return UNVERIFIED
