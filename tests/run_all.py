# -*- coding: utf-8 -*-
"""一键离线测试入口（v1.5.2-B0）。

背景：本仓测试是三态 runner 的**函数式**用例（`tests/_testkit.py`），
不是 `unittest.TestCase`。因此：

    python -m unittest discover tests     # ← 错误用法！

会输出 `NO TESTS RAN` 并返回 0（假绿）或 5/1，取决于具体调用形式。
CI（`.github/workflows/tests.yml`）走的是 15 条独立 `python tests/test_xxx.py`，
本脚本用**同样的方式**逐个跑一遍再汇总，保证「本地一条命令 = CI 全量」。

用法：
    python tests/run_all.py
    python tests/run_all.py -k canto      # 只跑文件名含 canto 的套件
    python tests/run_all.py -v            # 失败时打印完整子进程输出

退出码：只由 FAIL 决定（SKIP 不计 PASS 也不算失败，与 `_testkit` 口径一致）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

# `_testkit.run` 的汇总行：  "\n{passed}/{total} passed[, N skipped][, N failed]"
_SUMMARY_RE = re.compile(
    r"(\d+)/(\d+) passed"
    r"(?:,\s*(\d+) skipped)?"
    r"(?:,\s*(\d+) failed)?"
)


def discover(keyword: str = "") -> list[Path]:
    files = sorted(TESTS_DIR.glob("test_*.py"))
    if keyword:
        files = [p for p in files if keyword.lower() in p.name.lower()]
    return files


def run_one(path: Path, verbose: bool) -> dict:
    """子进程执行单个套件，返回 {name, passed, total, skipped, failed, rc, secs}。"""
    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    secs = time.time() - t0
    out = (proc.stdout or "") + (proc.stderr or "")

    m = None
    for m in _SUMMARY_RE.finditer(out):
        pass  # 取最后一处汇总行（最外层）

    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        skipped = int(m.group(3) or 0)
        failed = int(m.group(4) or 0)
    else:
        passed = total = skipped = 0
        failed = 1

    # 进程非 0 但汇总行说 0 failed（或压根没汇总行）→ 一律算失败
    if proc.returncode != 0 and failed == 0:
        failed = max(1, total - passed - skipped)

    if failed or proc.returncode != 0:
        print(f"  ✗ {path.name}: rc={proc.returncode} {passed}/{total} passed, {failed} failed  ({secs:.1f}s)")
        if verbose or not m:
            print(out.rstrip())
    else:
        extra = f", {skipped} skipped" if skipped else ""
        print(f"  ✓ {path.name}: {passed}/{total} passed{extra}  ({secs:.1f}s)")

    return {
        "name": path.name,
        "passed": passed,
        "total": total,
        "skipped": skipped,
        "failed": failed,
        "rc": proc.returncode,
        "secs": secs,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="一键跑全部离线测试套件（三态 runner）")
    ap.add_argument("-k", "--keyword", default="", help="只跑文件名含该关键词的套件")
    ap.add_argument("-v", "--verbose", action="store_true", help="失败时打印完整输出")
    args = ap.parse_args(argv)

    files = discover(args.keyword)
    if not files:
        print("未找到匹配的 tests/test_*.py")
        return 1

    print(f"🧪 离线测试：{len(files)} 个套件（解释器 {sys.executable}）\n")
    results = [run_one(p, args.verbose) for p in files]

    passed = sum(r["passed"] for r in results)
    total = sum(r["total"] for r in results)
    skipped = sum(r["skipped"] for r in results)
    failed = sum(r["failed"] for r in results)
    secs = sum(r["secs"] for r in results)

    print("\n" + "─" * 56)
    tail = f"TOTAL: {passed}/{total} passed"
    if skipped:
        tail += f", {skipped} skipped"
    if failed:
        tail += f", {failed} failed"
    tail += f"  ({secs:.1f}s)"
    print(tail)
    if failed:
        bad = [r["name"] for r in results if r["failed"] or r["rc"] != 0]
        print(f"FAILED SUITES: {bad}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
