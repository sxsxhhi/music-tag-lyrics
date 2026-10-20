# -*- coding: utf-8 -*-
"""
Lightweight Universal HTTP Networking Utility (v1.1.3)
Centralizes GET/POST requests, JSON decoding, custom User-Agents, and timeout handling.
Zero external dependencies.
"""

import urllib.request
import urllib.parse
import json
from typing import Optional, Dict, Any, Union

UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
UA_KUGOU = "KuGouMusic"
UA_DEFAULT = "music-tag-lyrics/1.1.3"

def http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 6,
    user_agent: str = UA_DEFAULT
) -> Optional[str]:
    """Performs HTTP GET and returns decoded text."""
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None

def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 6,
    user_agent: str = UA_DEFAULT
) -> Optional[Union[Dict[str, Any], list]]:
    """Performs HTTP GET and returns parsed JSON data."""
    text = http_get_text(url, headers=headers, timeout=timeout, user_agent=user_agent)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None

def http_post_json(
    url: str,
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 6,
    user_agent: str = UA_DESKTOP
) -> Optional[Union[Dict[str, Any], list]]:
    """Performs HTTP POST (form-encoded) and returns parsed JSON data."""
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
        
    post_bytes = urllib.parse.urlencode(data or {}).encode("utf-8")
    req = urllib.request.Request(url, data=post_bytes, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8", errors="ignore")
            return json.loads(raw_text)
    except Exception:
        return None
