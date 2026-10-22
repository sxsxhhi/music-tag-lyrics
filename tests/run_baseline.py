# -*- coding: utf-8 -*-
"""
基线跑批脚本（M0）

目的：在**未改一行源码**的前提下，为一组音频生成可复现的基线：
每首产出 source / lyric_type / 行数 / .lrc 与 .spl 的 sha256，
并证明 `--no-tags` 确实没有改动音频（mtime + size + sha256 三重校验）。

三个必须遵守的事实（都已实测）：
  1. `process_song.py` 在无歌词时会调用 `input()`。本脚本给子进程喂 DEVNULL，
     让 `input()` 立刻抛 EOFError，由 `process_song.py:128` 捕获后安全返回。
  2. `process_song.py` **所有路径的退出码都是 0**（单文件忽略返回值、批量只打印计数），
     因此成败只能靠 stdout 标记判定，不能看 returncode。
  3. 产物是受版权保护的歌词全文，必须落在**仓库之外**（默认仓库同级 `_baseline_v1/`）。

用法：
    python tests/run_baseline.py --songs "D:/WindowsAppp/Desktop/测试"
    python tests/run_baseline.py --songs <dir> --out-dir <dir> --only 焚情 --timeout 180
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROCESS_SONG = os.path.join(REPO_ROOT, "scripts", "process_song.py")
SUPPORTED_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".ogg", ".wav"}

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RE_SOURCE = re.compile(r"Matched Source:\s*\[([^\]]+)\]\s*\(Type:\s*([A-Za-z]+)([^)]*)\)")
RE_WARN = re.compile(r"ℹ️")
RE_LINE_NO = re.compile(r"^\[\d{1,3}:\d{2}")


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def snapshot(path):
    st = os.stat(path)
    return {
        "mtime": round(st.st_mtime, 3),
        "size": st.st_size,
        "sha256": sha256_file(path),
    }


def count_lines(path):
    if not os.path.isfile(path):
        return None
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if RE_LINE_NO.match(line):
                n += 1
    return n


def run_one(song, out_dir, timeout, python_exe):
    name = os.path.basename(song)
    before = snapshot(song)
    print(f"\n{'=' * 66}\n▶ {name}\n{'=' * 66}", flush=True)

    cmd = [python_exe, PROCESS_SONG, song, "--no-tags", "--out-dir", out_dir]
    t0 = time.time()
    status = "ok"
    stdout = ""
    stderr = ""
    rc = None

    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,          # 关键：喂 EOF，避免 input() 挂死
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
        rc = proc.returncode
        stdout = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        status = "timeout"
        stdout = (e.stdout or b"").decode("utf-8", "replace")
        stderr = (e.stderr or b"").decode("utf-8", "replace")
    except Exception as e:                      # noqa: BLE001
        status = "error"
        stderr = f"{type(e).__name__}: {e}"

    elapsed = round(time.time() - t0, 2)
    for line in stdout.splitlines():
        if line.strip():
            print("   " + line.strip())

    after = snapshot(song)
    mtime_ok = (before == after)

    base = os.path.splitext(name)[0]
    lrc = os.path.join(out_dir, base + ".lrc")
    spl = os.path.join(out_dir, base + ".spl")

    source = lyric_type = None
    synthesized = False
    m = RE_SOURCE.search(stdout)
    if m:
        source = m.group(1).strip().lower()
        lyric_type = m.group(2).strip().lower()
        synthesized = "synthesized" in m.group(3).lower()

    if status == "ok":
        if "No lyrics found or authorized" in stdout:
            status = "no_lyrics"
        elif not (os.path.isfile(lrc) and os.path.isfile(spl)):
            status = "no_output"
        elif "Successfully finished processing" not in stdout:
            status = "unfinished"

    lrc_sha = sha256_file(lrc) if os.path.isfile(lrc) else None
    spl_sha = sha256_file(spl) if os.path.isfile(spl) else None

    rec = {
        "file": name,
        "status": status,
        "elapsed_s": elapsed,
        "returncode": rc,
        "source": source,
        "lyric_type": lyric_type,
        "synthesized_words": synthesized,
        "lines": count_lines(spl) if spl_sha else None,
        "lrc_sha256": lrc_sha,
        "spl_sha256": spl_sha,
        "trilateral_mirror": (lrc_sha == spl_sha) if (lrc_sha and spl_sha) else None,
        "audio_unchanged": mtime_ok,
        "audio_before": before,
        "audio_after": after,
        "warnings": len(RE_WARN.findall(stdout)),
    }

    flag = "OK " if status == "ok" else status.upper()
    print(f"\n   → [{flag}] source={source} type={lyric_type} lines={rec['lines']} "
          f"mirror={rec['trilateral_mirror']} audio_unchanged={mtime_ok} {elapsed}s")
    if not mtime_ok:
        print("   ⚠️ 音频被改动了！before/after 不一致")
    if stderr.strip():
        print("   [stderr] " + stderr.strip().splitlines()[-1][:300])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--songs", required=True, help="音频目录或单文件")
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(REPO_ROOT), "_baseline_v1"),
                    help="歌词产物目录，必须在仓库之外")
    ap.add_argument("--timeout", type=int, default=180, help="每首超时秒数")
    ap.add_argument("--only", default=None, help="只跑文件名含该子串的曲目")
    ap.add_argument("--json", default=os.path.join(REPO_ROOT, "tests", "baseline.json"))
    args = ap.parse_args()

    python_exe = sys.executable
    target = os.path.abspath(args.songs)
    out_dir = os.path.abspath(args.out_dir)

    if not os.path.exists(target):
        print(f"[ERROR] 路径不存在: {target}")
        return 2
    repo_abs = os.path.abspath(REPO_ROOT)
    # 同盘才可比；跨盘（如仓库在 F:、产物在 C:）必然在仓库之外
    if os.path.splitdrive(repo_abs)[0].lower() == os.path.splitdrive(out_dir)[0].lower():
        if os.path.commonpath([repo_abs, out_dir]) == repo_abs:
            print(f"[ERROR] 产物目录不能放在仓库内（版权）：{out_dir}")
            return 2

    if os.path.isfile(target):
        songs = [target]
    else:
        songs = []
        for root, _dirs, files in os.walk(target):
            for f in sorted(files):
                if os.path.splitext(f)[1].lower() in SUPPORTED_EXTS:
                    songs.append(os.path.join(root, f))
        songs.sort()
    if args.only:
        songs = [s for s in songs if args.only in os.path.basename(s)]
    if not songs:
        print("[ERROR] 没有找到可处理的音频")
        return 2

    os.makedirs(out_dir, exist_ok=True)
    print(f"解释器: {python_exe}")
    print(f"样本数: {len(songs)}   产物: {out_dir}   每首超时: {args.timeout}s")

    records = [run_one(s, out_dir, args.timeout, python_exe) for s in songs]

    ok = [r for r in records if r["status"] == "ok"]
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "python": python_exe,
        "source_dir": target,
        "out_dir": out_dir,
        "timeout_s": args.timeout,
        "git_head": subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "--short", "HEAD"],
                                   capture_output=True, text=True).stdout.strip(),
        "summary": {
            "total": len(records),
            "ok": len(ok),
            "no_lyrics": sum(1 for r in records if r["status"] == "no_lyrics"),
            "timeout": sum(1 for r in records if r["status"] == "timeout"),
            "failed": sum(1 for r in records if r["status"] not in ("ok", "no_lyrics")),
            "audio_all_unchanged": all(r["audio_unchanged"] for r in records),
            "trilateral_all_mirrored": all(r["trilateral_mirror"] for r in ok) if ok else None,
            "source_dist": {s: sum(1 for r in ok if r["source"] == s)
                            for s in sorted({r["source"] for r in ok if r["source"]})},
            "type_dist": {t: sum(1 for r in ok if r["lyric_type"] == t)
                          for t in sorted({r["lyric_type"] for r in ok if r["lyric_type"]})},
        },
        "songs": records,
    }

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n{'=' * 66}\n汇总: {payload['summary']}\n已写入: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
