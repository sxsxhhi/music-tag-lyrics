# -*- coding: utf-8 -*-
"""轻量 HTTP GET/POST 与 JSON 解析。"""

import urllib.request
import urllib.parse
import urllib.error
import json
import logging
import time
import socket
from typing import Optional, Dict, Any, Union, NamedTuple

from ..version import __version__

logger = logging.getLogger(__name__)

# v1.8.5 / H-C1：歌词类 API 的正常响应都在几十 KB 量级，2 MiB 已远超实际需要。
# 未显式指定上限的调用点用这个保守默认值，避免异常/恶意响应导致无界内存占用。
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
# Resource downloads may be larger than lyric JSON, but must also have a
# finite default limit. Cover art already supplies a tighter explicit limit.
DEFAULT_MAX_BINARY_BYTES = 16 * 1024 * 1024

UA_DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
UA_KUGOU = "KuGouMusic"
UA_DEFAULT = f"music-tag-lyrics/{__version__}"

def _read_bounded(resp, max_bytes: int) -> Optional[bytes]:
    """读取响应体并在超过上限时放弃（绝不先整体读入再判断）。

    ``max_bytes <= 0`` 表示沿用旧的无界行为，仅用于显式选择退出的调用方。
    """
    if max_bytes <= 0:
        return resp.read()
    raw = resp.read(max_bytes + 1)
    if len(raw) > max_bytes:
        logger.warning("HTTP 响应超过上限 %d 字节，已放弃", max_bytes)
        return None
    return raw


def http_get_text(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 6,
    user_agent: str = UA_DEFAULT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Optional[str]:
    """Performs HTTP GET and returns decoded text（超限返回 None）。"""
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = _read_bounded(resp, max_bytes)
            if raw is None:
                return None
            return raw.decode("utf-8", errors="ignore")
    except Exception:
        return None

def http_get_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 6,
    user_agent: str = UA_DEFAULT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Optional[Union[Dict[str, Any], list]]:
    """Performs HTTP GET and returns parsed JSON data."""
    text = http_get_text(
        url, headers=headers, timeout=timeout, user_agent=user_agent, max_bytes=max_bytes
    )
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
    user_agent: str = UA_DESKTOP,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Optional[Union[Dict[str, Any], list]]:
    """Performs HTTP POST (form-encoded) and returns parsed JSON data."""
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
        
    post_bytes = urllib.parse.urlencode(data or {}).encode("utf-8")
    req = urllib.request.Request(url, data=post_bytes, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = _read_bounded(resp, max_bytes)
            if raw is None:
                return None
            return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def http_post_json_body(
    url: str,
    payload: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 8,
    user_agent: str = UA_DESKTOP,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Optional[Union[Dict[str, Any], list]]:
    """Performs HTTP POST with a **JSON request body** and returns parsed JSON.

    与 ``http_post_json`` 的区别：那个走 form-encoded（``a=1&b=2``），
    QQ 音乐 musicu 接口要求 ``Content-Type: application/json`` 且 body 是 JSON 文本，
    用 form-encoded 会被服务端判成非法请求。
    """
    req_headers = {
        "User-Agent": user_agent,
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json",
    }
    if headers:
        req_headers.update(headers)

    try:
        body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        return None

    req = urllib.request.Request(url, data=body, headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = _read_bounded(resp, max_bytes)
            if raw is None:
                return None
            return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return None


def http_get_bytes(
    url: str,
    timeout: float = 10.0,
    max_bytes: int = DEFAULT_MAX_BINARY_BYTES,
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
    headers: Optional[Dict[str, str]] = None


def http_get_text_ex(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 3.0,
    user_agent: str = UA_DEFAULT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> HttpResult:
    """带状态码和耗时的 HTTP GET；默认对所有响应（含 HTTPError）施加 2 MiB 上限。

    显式 ``max_bytes<=0`` 才允许无界读取，供确有需要的受控调用方使用。
    超限时返回 ``error='oversized'`` 且 ``text=''``，不向上层暴露截断正文。
    """
    req_headers = {"User-Agent": user_agent}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_headers = dict(resp.headers.items()) if hasattr(resp, "headers") and resp.headers else {}
            if max_bytes > 0:
                raw = resp.read(max_bytes + 1)
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                status = getattr(resp, "status", getattr(resp, "code", 200))
                if len(raw) > max_bytes:
                    return HttpResult(status=status, text="", bytes_len=len(raw), elapsed_ms=elapsed_ms, error="oversized", headers=resp_headers)
            else:
                raw = resp.read()
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                status = getattr(resp, "status", getattr(resp, "code", 200))
            text = raw.decode("utf-8", errors="ignore")
            return HttpResult(status=status, text=text, bytes_len=len(raw), elapsed_ms=elapsed_ms, error="", headers=resp_headers)
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        try:
            raw = exc.read(max_bytes + 1) if max_bytes > 0 else exc.read()
            bytes_len = len(raw)
            if max_bytes > 0 and bytes_len > max_bytes:
                # Avoid returning partial error HTML/JSON as if the body were complete.
                error, text = "oversized", ""
            else:
                error, text = "", raw.decode("utf-8", errors="ignore")
        except Exception:
            text, bytes_len, error = "", 0, "body_read_error"
        resp_headers = dict(exc.headers.items()) if hasattr(exc, "headers") and exc.headers else {}
        return HttpResult(status=exc.code, text=text, bytes_len=bytes_len, elapsed_ms=elapsed_ms, error=error, headers=resp_headers)
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
