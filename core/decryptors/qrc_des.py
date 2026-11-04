# -*- coding: utf-8 -*-
"""QQ 音乐 QRC：3DES(ECB) 解密 + zlib 解压。

⚠️ 位序陷阱：QQ 自带的 DES 实现**位号与标准 DES 不同**（PC1/PC2/IP/FP 的下标都是
按它自己的 `bitnum()` 取位方式定义的）。实测 pycryptodome 的 `DES3.new()` 用
`K1K2K3 / K3K2K1 / 单 DES` 三种排列全部解不出来（前 16 字节是乱码），只有照这份
JS 实现逐行移植才对得上。**不要**用标准 DES 表替换下面的任何一张表。

密钥调度顺序（实测）：D(K3) → E(K2) → D(K1)
"""

import zlib
from typing import List, Tuple

# 24 字节，三段各 8 字节：K1 / K2 / K3
QRC_KEY = b"!@#)(*$%123ZXC!@!@#)(NHL"

# S 盒（8 组 × 64），取值与 QQ 原实现一致，非 FIPS 顺序
SBOX = [
    [14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
     0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
     4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
     15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13],
    [15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
     3, 13, 4, 7, 15, 2, 8, 15, 12, 0, 1, 10, 6, 9, 11, 5,
     0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
     13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9],
    [10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
     13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
     13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
     1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12],
    [7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
     13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
     10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
     3, 15, 0, 6, 10, 10, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14],
    [2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
     14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
     4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
     11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3],
    [12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
     10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
     9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
     4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13],
    [4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
     13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
     1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
     6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12],
    [13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
     1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
     7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
     2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11],
]

_PC1_C = [56, 48, 40, 32, 24, 16, 8, 0, 57, 49, 41, 33, 25, 17, 9, 1,
          58, 50, 42, 34, 26, 18, 10, 2, 59, 51, 43, 35]
_PC1_D = [62, 54, 46, 38, 30, 22, 14, 6, 61, 53, 45, 37, 29, 21, 13, 5,
          60, 52, 44, 36, 28, 20, 12, 4, 27, 19, 11, 3]
_PC2 = [13, 16, 10, 23, 0, 4, 2, 27, 14, 5, 20, 9, 22, 18, 11, 3,
        25, 7, 15, 6, 26, 19, 12, 1, 40, 51, 30, 36, 46, 54, 29, 39,
        50, 44, 32, 47, 43, 48, 38, 55, 33, 52, 45, 41, 49, 35, 28, 31]
_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)

_IP_LEFT = [57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
            61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7]
_IP_RIGHT = [56, 48, 40, 32, 24, 16, 8, 0, 58, 50, 42, 34, 26, 18, 10, 2,
             60, 52, 44, 36, 28, 20, 12, 4, 62, 54, 46, 38, 30, 22, 14, 6]

_MASK32 = 0xFFFFFFFF
_SBOX0 = SBOX[0]
_SBOX1 = SBOX[1]
_SBOX2 = SBOX[2]
_SBOX3 = SBOX[3]
_SBOX4 = SBOX[4]
_SBOX5 = SBOX[5]
_SBOX6 = SBOX[6]
_SBOX7 = SBOX[7]

# 三组子密钥只与固定 QRC_KEY 有关，进程内缓存（首次计算后复用）
_SCHEDULE_CACHE = {}


def _bitnum(buf: List[int], index: int, target_bit: int) -> int:
    """从字节数组按 QQ 位号取 1 位，左移到 target_bit。"""
    k = (index // 32) * 4 + 3 - ((index % 32) // 8)
    if k >= len(buf):
        return 0
    return (((buf[k] >> (7 - (index % 8))) & 1) << target_bit) & _MASK32


def _bitnum_intr(value: int, index: int, target_bit: int) -> int:
    """从 32 位字右取位。"""
    return (((value >> (31 - index)) & 1) << target_bit) & _MASK32


def _bitnum_intl(value: int, shift: int, target_bit: int) -> int:
    """从 32 位字左取位。"""
    return ((((value << shift) & _MASK32) & 0x80000000) >> target_bit) & _MASK32


def _sbox_bit(value: int) -> int:
    return (value & 32) | ((value & 31) >> 1) | ((value & 1) << 4)


def _initial_permutation(block: List[int]) -> Tuple[int, int]:
    s0 = 0
    s1 = 0
    for pos, idx in enumerate(_IP_LEFT):
        s0 |= _bitnum(block, idx, 31 - pos)
    for pos, idx in enumerate(_IP_RIGHT):
        s1 |= _bitnum(block, idx, 31 - pos)
    return s0 & _MASK32, s1 & _MASK32


def _inverse_permutation(s0: int, s1: int) -> List[int]:
    out = []
    for byte in range(8):
        base = (byte + 4) if byte < 4 else (byte - 4)
        v = 0
        for slot in range(8):
            idx = base + 8 * (slot // 2)
            src = s1 if slot % 2 == 0 else s0
            v |= _bitnum_intr(src, idx, 7 - slot)
        out.append(v & 0xFF)
    return out


def _des_f(state: int, subkey: List[int]) -> int:
    t1 = (_bitnum_intl(state, 31, 0)
          | ((state & 0xF0000000) >> 1)
          | _bitnum_intl(state, 4, 5)
          | _bitnum_intl(state, 3, 6)
          | ((state & 0x0F000000) >> 3)
          | _bitnum_intl(state, 8, 11)
          | _bitnum_intl(state, 7, 12)
          | ((state & 0x00F00000) >> 5)
          | _bitnum_intl(state, 12, 17)
          | _bitnum_intl(state, 11, 18)
          | ((state & 0x000F0000) >> 7)
          | _bitnum_intl(state, 16, 23)) & _MASK32

    t2 = (_bitnum_intl(state, 15, 0)
          | ((state & 0x0000F000) << 15)
          | _bitnum_intl(state, 20, 5)
          | _bitnum_intl(state, 19, 6)
          | ((state & 0x00000F00) << 13)
          | _bitnum_intl(state, 24, 11)
          | _bitnum_intl(state, 23, 12)
          | ((state & 0x000000F0) << 11)
          | _bitnum_intl(state, 28, 17)
          | _bitnum_intl(state, 27, 18)
          | ((state & 0x0000000F) << 9)
          | _bitnum_intl(state, 0, 23)) & _MASK32

    v0 = ((t1 >> 24) & 0xFF) ^ subkey[0]
    v1 = ((t1 >> 16) & 0xFF) ^ subkey[1]
    v2 = ((t1 >> 8) & 0xFF) ^ subkey[2]
    v3 = ((t2 >> 24) & 0xFF) ^ subkey[3]
    v4 = ((t2 >> 16) & 0xFF) ^ subkey[4]
    v5 = ((t2 >> 8) & 0xFF) ^ subkey[5]

    r = ((_SBOX0[_sbox_bit(v0 >> 2)] << 28)
         | (_SBOX1[_sbox_bit(((v0 & 3) << 4) | (v1 >> 4))] << 24)
         | (_SBOX2[_sbox_bit(((v1 & 15) << 2) | (v2 >> 6))] << 20)
         | (_SBOX3[_sbox_bit(v2 & 63)] << 16)
         | (_SBOX4[_sbox_bit(v3 >> 2)] << 12)
         | (_SBOX5[_sbox_bit(((v3 & 3) << 4) | (v4 >> 4))] << 8)
         | (_SBOX6[_sbox_bit(((v4 & 15) << 2) | (v5 >> 6))] << 4)
         | _SBOX7[_sbox_bit(v5 & 63)]) & _MASK32

    return (_bitnum_intl(r, 15, 0)
            | _bitnum_intl(r, 6, 1)
            | _bitnum_intl(r, 19, 2)
            | _bitnum_intl(r, 20, 3)
            | _bitnum_intl(r, 28, 4)
            | _bitnum_intl(r, 11, 5)
            | _bitnum_intl(r, 27, 6)
            | _bitnum_intl(r, 16, 7)
            | _bitnum_intl(r, 0, 8)
            | _bitnum_intl(r, 14, 9)
            | _bitnum_intl(r, 22, 10)
            | _bitnum_intl(r, 25, 11)
            | _bitnum_intl(r, 4, 12)
            | _bitnum_intl(r, 17, 13)
            | _bitnum_intl(r, 30, 14)
            | _bitnum_intl(r, 9, 15)
            | _bitnum_intl(r, 1, 16)
            | _bitnum_intl(r, 7, 17)
            | _bitnum_intl(r, 23, 18)
            | _bitnum_intl(r, 13, 19)
            | _bitnum_intl(r, 31, 20)
            | _bitnum_intl(r, 26, 21)
            | _bitnum_intl(r, 2, 22)
            | _bitnum_intl(r, 8, 23)
            | _bitnum_intl(r, 18, 24)
            | _bitnum_intl(r, 12, 25)
            | _bitnum_intl(r, 29, 26)
            | _bitnum_intl(r, 5, 27)
            | _bitnum_intl(r, 21, 28)
            | _bitnum_intl(r, 10, 29)
            | _bitnum_intl(r, 3, 30)
            | _bitnum_intl(r, 24, 31)) & _MASK32


def _key_schedule(key8: bytes, decrypt: bool) -> List[List[int]]:
    """生成 16 组子密钥，每组 6 字节。"""
    schedule = [[0, 0, 0, 0, 0, 0] for _ in range(16)]
    key = list(key8)
    c = 0
    d = 0
    for i in range(28):
        c |= _bitnum(key, _PC1_C[i], 31 - i)
        d |= _bitnum(key, _PC1_D[i], 31 - i)

    for i in range(16):
        shift = _SHIFTS[i]
        c = (((c << shift) | (c >> (28 - shift))) & 0xFFFFFFF0) & _MASK32
        d = (((d << shift) | (d >> (28 - shift))) & 0xFFFFFFF0) & _MASK32
        idx = (15 - i) if decrypt else i
        row = schedule[idx]
        for j in range(24):
            row[j // 8] |= _bitnum_intr(c, _PC2[j], 7 - (j % 8))
        for j in range(24, 48):
            row[j // 8] |= _bitnum_intr(d, _PC2[j] - 27, 7 - (j % 8))
    return schedule


def _crypt_block(block: List[int], schedule: List[List[int]]) -> List[int]:
    s0, s1 = _initial_permutation(block)
    for i in range(15):
        prev = s1
        s1 = (_des_f(s1, schedule[i]) ^ s0) & _MASK32
        s0 = prev
    s0 = (_des_f(s1, schedule[15]) ^ s0) & _MASK32
    return _inverse_permutation(s0, s1)


def _triple_schedule():
    """QRC 的 D(K3) → E(K2) → D(K1) 三组子密钥（带进程内缓存）。"""
    cached = _SCHEDULE_CACHE.get("schedules")
    if cached is not None:
        return cached
    key = QRC_KEY
    schedules = (
        _key_schedule(key[16:24], True),
        _key_schedule(key[8:16], False),
        _key_schedule(key[0:8], True),
    )
    _SCHEDULE_CACHE["schedules"] = schedules
    return schedules


def des_encrypt_block(key8: bytes, block8: bytes) -> bytes:
    """单块 DES 加密（仅测试用，用于与 JS 实现做块级对照）。"""
    return bytes(_crypt_block(list(block8), _key_schedule(key8, False)))


def des_decrypt_block(key8: bytes, block8: bytes) -> bytes:
    """单块 DES 解密（仅测试用）。"""
    return bytes(_crypt_block(list(block8), _key_schedule(key8, True)))


def des3_decrypt(raw: bytes) -> bytes:
    """QRC 的 3DES-ECB 解密。不足 8 字节或非 8 倍数时截断，不抛异常。"""
    if not raw:
        return b""
    usable = len(raw) - (len(raw) % 8)
    if usable <= 0:
        return b""
    schedules = _triple_schedule()
    out = bytearray()
    for offset in range(0, usable, 8):
        block = list(raw[offset:offset + 8])
        for schedule in schedules:
            block = _crypt_block(block, schedule)
        out.extend(block)
    return bytes(out)


def _inflate(data: bytes) -> bytes:
    """先按 zlib 头解压，失败再试裸 deflate。"""
    try:
        return zlib.decompress(data)
    except zlib.error:
        pass
    try:
        return zlib.decompress(data, -zlib.MAX_WBITS)
    except zlib.error:
        return b""


def decrypt_qrc(hex_text: str) -> str:
    """QQ 歌词 hex → QRC 明文。任何环节失败都返回空串，绝不返回半截乱码。"""
    if not hex_text:
        return ""
    cleaned = "".join(hex_text.split())
    if len(cleaned) < 16 or len(cleaned) % 2 != 0:
        return ""
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError:
        return ""
    plain = _inflate(des3_decrypt(raw))
    if not plain:
        return ""
    text = plain.decode("utf-8", "ignore")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text
