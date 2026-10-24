# -*- coding: utf-8 -*-
"""轻量 HTTP GET/POST 与 JSON 解析。"""

import urllib.request
import urllib.parse
import urllib.error
import json
import time
import socket
from typing import Optional, Dict, Any, Union, NamedTuple

UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
UA_KUGOU = "KuGouMusic"
UA_DEFAULT = "music-tag-lyrics/1.3.4"

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


def http_get_bytes(
    url: str,
    timeout: float = 10.0,
    max_bytes: int = 0,
    user_agent: str = UA_DESKTOP
) -> Optional[bytes]:
    """下载二进制内容。非 2xx、传输失败、超过 max_bytes 上限（>0 时）一律返回 None。

    A3 去重：全仓库唯一的二进制下载出口（原先 cover_art 自写 urllib 直连）。
    F1：缺省 UA 用桌面浏览器——二进制/资源下载面向商用 CDN，脚本 UA 极易 403。
    """
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", getattr(resp, "code", 200))
            if status != 200:
                return None
            data = resp.read(max_bytes + 1) if max_bytes > 0 else resp.read()
    except Exception:
        return None
    if max_bytes > 0 and len(data) > max_bytes:
        return None
    return data


class HttpResult(NamedTuple):
    status: int       # 0 = 传输层失败；4xx/5xx 来自 HTTPError.code
    text: str
    bytes_len: int
    elapsed_ms: int
    error: str        # timeout / ssl_eof / urlerror / ""


def http_get_text_ex(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 3.0,
    user_agent: str = UA_DEFAULT
) -> HttpResult:
    """带状态码、体长、耗时与精细异常的 HTTP GET。校验证书，不吞状态码。"""
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            text = raw.decode("utf-8", errors="ignore")
            status = getattr(resp, "status", getattr(resp, "code", 200))
            return HttpResult(status=status, text=text, bytes_len=len(raw), elapsed_ms=elapsed_ms, error="")
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        try:
            raw = exc.read()
            text = raw.decode("utf-8", errors="ignore")
            bytes_len = len(raw)
        except Exception:
            raw, text, bytes_len = b"", "", 0
        return HttpResult(status=exc.code, text=text, bytes_len=bytes_len, elapsed_ms=elapsed_ms, error="")
    except (socket.timeout, TimeoutError) as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=elapsed_ms, error="timeout")
    except urllib.error.URLError as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        reason_str = str(getattr(exc, "reason", exc)).lower()
        if "timed out" in reason_str or "timeout" in reason_str:
            err = "timeout"
        elif "ssl" in reason_str or "eof" in reason_str:
            err = "ssl_eof"
        else:
            err = "urlerror"
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=elapsed_ms, error=err)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        err_str = str(exc).lower()
        if "ssl" in err_str or "eof" in err_str:
            err = "ssl_eof"
        elif "timeout" in err_str:
            err = "timeout"
        else:
            err = "urlerror"
        return HttpResult(status=0, text="", bytes_len=0, elapsed_ms=elapsed_ms, error=err)
