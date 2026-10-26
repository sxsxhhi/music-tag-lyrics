# -*- coding: utf-8 -*-
"""酷狗 KRC：16 字节 XOR + zlib 解密。"""

import zlib

KRC_XOR_KEY = [
    0x40, 0x47, 0x61, 0x77, 0x5e, 0x32, 0x74, 0x47,
    0x51, 0x36, 0x31, 0x2d, 0xce, 0xd2, 0x6e, 0x69
]

def decrypt_krc(raw_bytes: bytes) -> str:
    """
    Decrypts raw KRC bytes (with or without 'krc1' magic header) into text.
    """
    if not raw_bytes:
        return ""
    
    # Check for magic header 'krc1'
    offset = 4 if raw_bytes[:4] == b'krc1' else 0
    data = raw_bytes[offset:]
    if not data:
        # v1.5.1: 只有 magic 头、没有任何载荷（如下载被截断），与空输入同样处理，
        # 不要拿空字节去喂 zlib 然后抛 ValueError 打断整条流水线。
        return ""

    # XOR decryption
    decrypted_bytes = bytearray(len(data))
    for i, b in enumerate(data):
        decrypted_bytes[i] = b ^ KRC_XOR_KEY[i % 16]
        
    # Zlib decompression
    try:
        decompressed = zlib.decompress(decrypted_bytes)
        return decompressed.decode("utf-8", errors="ignore")
    except Exception:
        # Try raw inflate
        try:
            decompressed = zlib.decompress(decrypted_bytes, -zlib.MAX_WBITS)
            return decompressed.decode("utf-8", errors="ignore")
        except Exception as e:
            raise ValueError(f"Failed to decompress decrypted KRC data: {e}")
