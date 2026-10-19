# -*- coding: utf-8 -*-
"""
Kugou KRC (Karaoke Lyric) Decryptor
Decrypts Kugou's proprietary KRC format using the standard 16-byte XOR key and zlib decompression.
Zero external dependencies.
"""

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
