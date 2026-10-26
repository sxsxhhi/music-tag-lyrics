# -*- coding: utf-8 -*-
"""离线测试公共 runner（v1.5.1-E1）。

背景：13 个测试文件各自复制了一份 `main()`，且都只有 PASS/FAIL 两态——
用例在函数体内 `print("SKIP"); return` 会被记成 PASS。实测在无 torch 的
CI 上，189 条里有 5 条是空转，徽章却显示 189/189 全绿。

本模块提供三态 runner：
  · PASS  用例真的跑完且断言通过
  · SKIP  环境缺失（无 torch / 无 CUDA / 无模型），不计 PASS 也不算失败
  · FAIL  断言失败或异常

退出码只由 FAIL 决定 —— CI 里没有 GPU 也必须能过。
"""

from __future__ import annotations

import sys
from typing import Any, Dict


class SkipTest(Exception):
    """用例因环境缺失被跳过（不计入 PASS，也不计失败）。"""


def skip(reason: str) -> None:
    """在用例内部调用：声明本条因环境缺失跳过。"""
    raise SkipTest(reason)


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
