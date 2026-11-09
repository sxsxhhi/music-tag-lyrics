# -*- coding: utf-8 -*-
"""离线测试公共 runner 与网络安全屏障 (v1.5.1-E1 / v1.8.1-R18)。

背景：
1. 离线测试是三态 runner：
   · PASS  用例真的跑完且断言通过
   · SKIP  环境缺失（无 torch / 无 CUDA / 无模型），不计 PASS 也不算失败
   · FAIL  断言失败或异常
2. R18 网络屏障（Tripwire）：
   离线测试期间默认拦截一切非本地（localhost / 127.0.0.1 / ::1）的网络连接、HTTP 请求与 DNS 解析。
   继承 BaseException 杜绝被第三方代码中通配的 `except Exception:` 静默吞掉。
   任何漏 Mock 的真实网络请求都会立即抛出 NetworkTripwireError 并打印完整堆栈。
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import os
import socket
import sys
import urllib.parse
from typing import Any, Dict, Set

# 离线测试默认注入合规的测试邮箱，确保 LrcRedFetcher mock 请求不被条款守卫阻断
os.environ.setdefault("MTL_LRC_RED_CONTACT", "test-runner@example.invalid")


class SkipTest(Exception):
    """用例因环境缺失被跳过（不计入 PASS，也不计失败）。"""


def skip(reason: str) -> None:
    """在用例内部调用：声明本条因环境缺失跳过。"""
    raise SkipTest(reason)


class NetworkTripwireError(BaseException):
    """离线测试尝试访问真实外部网络时由 Tripwire 拦截抛出。
    
    继承 BaseException 以防止被第三方代码中通配的 `except Exception:` 静默吞掉。
    """


_ORIGINAL_CONNECT = socket.socket.connect
_ORIGINAL_GETADDRINFO = socket.getaddrinfo
_ORIGINAL_CREATE_CONNECTION = socket.create_connection
_ORIGINAL_HTTP_CONNECT = http.client.HTTPConnection.connect
_ORIGINAL_HTTPS_CONNECT = http.client.HTTPSConnection.connect
_ORIGINAL_HTTP_REQUEST = http.client.HTTPConnection.request

_TRIPWIRE_ENABLED = os.environ.get("MTL_DISABLE_TRIPWIRE") != "1"
_ALLOWED_HOSTS: Set[str] = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


def _is_loopback(host: str) -> bool:
    if not host or host in _ALLOWED_HOSTS:
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback
    except ValueError:
        return False


def _check_host_allowed(host: str, context: str = ""):
    if not _TRIPWIRE_ENABLED:
        return
    if not _is_loopback(host):
        raise NetworkTripwireError(
            f"[NetworkTripwire] 拦截到未经 Mock 的真实网络访问 ({context}): {host}。请在离线测试中完整 Mock 外部依赖。"
        )


def _tripwire_connect(self, address, *args, **kwargs):
    if _TRIPWIRE_ENABLED:
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str):
            _check_host_allowed(host, "socket.connect")
    return _ORIGINAL_CONNECT(self, address, *args, **kwargs)


def _tripwire_create_connection(address, *args, **kwargs):
    if _TRIPWIRE_ENABLED:
        host = address[0] if isinstance(address, tuple) and address else address
        if isinstance(host, str):
            _check_host_allowed(host, "socket.create_connection")
    return _ORIGINAL_CREATE_CONNECTION(address, *args, **kwargs)


def _tripwire_getaddrinfo(host, port, *args, **kwargs):
    if _TRIPWIRE_ENABLED and isinstance(host, str):
        _check_host_allowed(host, f"DNS getaddrinfo {port}")
    return _ORIGINAL_GETADDRINFO(host, port, *args, **kwargs)


def _tripwire_http_connect(self):
    if _TRIPWIRE_ENABLED:
        host = getattr(self, "_tunnel_host", None) or getattr(self, "host", "")
        if isinstance(host, str):
            _check_host_allowed(host, "HTTPConnection.connect")
    return _ORIGINAL_HTTP_CONNECT(self)


def _tripwire_https_connect(self):
    if _TRIPWIRE_ENABLED:
        host = getattr(self, "_tunnel_host", None) or getattr(self, "host", "")
        if isinstance(host, str):
            _check_host_allowed(host, "HTTPSConnection.connect")
    return _ORIGINAL_HTTPS_CONNECT(self)


def _tripwire_http_request(self, method, url, *args, **kwargs):
    if _TRIPWIRE_ENABLED:
        if isinstance(url, str) and (url.startswith("http://") or url.startswith("https://")):
            parsed = urllib.parse.urlparse(url)
            if parsed.hostname:
                _check_host_allowed(parsed.hostname, f"HTTP request {method}")
        else:
            host = getattr(self, "_tunnel_host", None) or getattr(self, "host", "")
            if isinstance(host, str):
                _check_host_allowed(host, f"HTTP request {method}")
    return _ORIGINAL_HTTP_REQUEST(self, method, url, *args, **kwargs)


def install_tripwire() -> None:
    socket.socket.connect = _tripwire_connect
    socket.getaddrinfo = _tripwire_getaddrinfo
    socket.create_connection = _tripwire_create_connection
    http.client.HTTPConnection.connect = _tripwire_http_connect
    http.client.HTTPSConnection.connect = _tripwire_https_connect
    http.client.HTTPConnection.request = _tripwire_http_request


def uninstall_tripwire() -> None:
    socket.socket.connect = _ORIGINAL_CONNECT
    socket.getaddrinfo = _ORIGINAL_GETADDRINFO
    socket.create_connection = _ORIGINAL_CREATE_CONNECTION
    http.client.HTTPConnection.connect = _ORIGINAL_HTTP_CONNECT
    http.client.HTTPSConnection.connect = _ORIGINAL_HTTPS_CONNECT
    http.client.HTTPConnection.request = _ORIGINAL_HTTP_REQUEST


@contextlib.contextmanager
def disable_tripwire():
    global _TRIPWIRE_ENABLED
    old = _TRIPWIRE_ENABLED
    _TRIPWIRE_ENABLED = False
    try:
        yield
    finally:
        _TRIPWIRE_ENABLED = old


@contextlib.contextmanager
def allow_hosts(*hosts: str):
    global _ALLOWED_HOSTS
    old = set(_ALLOWED_HOSTS)
    _ALLOWED_HOSTS.update(hosts)
    try:
        yield
    finally:
        _ALLOWED_HOSTS = old


# 默认激活 Tripwire
install_tripwire()


def run(namespace: Dict[str, Any]) -> int:
    """收集 namespace 里所有 test_* 可调用对象并执行，返回进程退出码。"""
    tests = [
        (name, fn)
        for name, fn in sorted(namespace.items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = 0
    skipped = []
    failed = []

    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except SkipTest as exc:
            print(f"  SKIP  {name} ({exc})")
            skipped.append(name)
        except NetworkTripwireError as exc:
            print(f"  FAIL  {name}: {exc}")
            import traceback

            traceback.print_exc()
            failed.append(name)
        except Exception as exc:  # noqa: BLE001 - 测试 runner 需要捕获一切
            print(f"  FAIL  {name}: {exc}")
            import traceback

            traceback.print_exc()
            failed.append(name)

    total = len(tests)
    summary = f"\n{passed}/{total} passed"
    if skipped:
        summary += f", {len(skipped)} skipped"
    if failed:
        summary += f", {len(failed)} failed"
    print(summary)
    if skipped:
        print(f"SKIPPED: {skipped}")
    if failed:
        print(f"FAILED: {failed}")
    return 0 if not failed else 1


def main(namespace: Dict[str, Any]) -> int:
    """兼容旧调用点：`sys.exit(main())` → `sys.exit(_testkit.main(globals()))`。"""
    return run(namespace)
