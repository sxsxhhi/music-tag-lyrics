# -*- coding: utf-8 -*-
"""Golden 基线只读审计（v1.6.0）——**绝不写夹具**。

背景（方案 §7.1）：夹具 `tests/fixtures/golden_jinhaixin_2000.lrc` 同时充当
歌词文本、hints 与断言基准。若用引擎输出回填夹具，回归就变成自证，
从此再也发现不了同类 bug。本脚本只做三件事：
  1. 跑一次引擎（GPU），与夹具逐行比对；
  2. 输出 golden_diff.md（行号/文本/夹具行首/引擎行首/Δms/首字/末字/最大字宽）；
  3. 对 Δ > LINE_TOL_MS 的行自动测量「引擎起点有无起振 / 夹具起点是否落在静音」，
     给出初判 suspect_engine / suspect_fixture，**人工裁决**。

用法：
  python tests/audit_acoustic_baseline.py \
      --audio "F:\\...\\金海心 - 那么骄傲.flac" \
      --diff  Work/任务/2026-10-27-v22/golden_diff.md
退出码：全部 Δ <= LINE_TOL_MS → 0；否则 1。
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

GOLDEN_LRC = os.path.join(REPO_ROOT, "tests", "fixtures", "golden_jinhaixin_2000.lrc")
LINE_TOL_MS = 1500        # 与 run_acoustic_baseline.py 同口径；**严禁放宽**


def main() -> int:
    ap = argparse.ArgumentParser(description="Golden 基线只读审计")
    ap.add_argument("--audio", required=True)
    ap.add_argument("--golden", default=GOLDEN_LRC)
    ap.add_argument("--diff", default="")
    ap.add_argument("--lang", default="zh")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    from core.parsers.lrc_parser import parse_lrc
    from core.utils.io_safe import read_text_guess_encoding
    from core.utils.text_utils import is_credit_line

    golden = [
        (ln["start"], (ln.get("text") or "").strip())
        for ln in parse_lrc(read_text_guess_encoding(args.golden)).get("lines") or []
        if isinstance(ln.get("start"), int) and ln["start"] >= 12000
        and (ln.get("text") or "").strip() and not is_credit_line(ln.get("text") or "")
        and " - " not in (ln.get("text") or "")
    ]
    print(f"🎯 夹具锚：{len(golden)} 演唱行 | {args.golden}")

    from core.acoustic_aligner import AcousticAligner

    lyrics_text = "\n".join(x for _, x in golden)
    hints = [(t / 1000.0, x) for t, x in golden]
    out = AcousticAligner.align_lyrics(args.audio, lyrics_text, line_timing_hints=hints, lang=args.lang)
    engine = [ln for ln in (out.get("lines") or []) if (ln.get("text") or "").strip()]
    print(f"🎤 引擎产出：{len(engine)} 行（engine={out.get('align_engine', '?')}）")

    rows, n_bad = [], 0
    try:
        from core.acoustic.qc import Env
        v_mono, sr = AcousticAligner.extract_vocals_mono(args.audio, device="cuda")
        env = Env.from_vocals(v_mono, sr)
    except Exception as e:
        env = None
        print(f"⚠️ 包络不可用（跳过初判）：{e}")

    for i, ((gt, gtx), ln) in enumerate(zip(golden, engine)):
        es = ln["start"]
        ws = ln.get("words") or []
        widest = max((w["end"] - w["start"] for w in ws), default=0)
        delta = abs(es - gt)
        verdict = "ok"
        if delta > LINE_TOL_MS:
            n_bad += 1
            if env is not None:
                engine_has_onset = env.near_onset(es, 150)
                fixture_is_quiet = env.energy(gt / 1000.0, 0.10) < 0.012
                if engine_has_onset and fixture_is_quiet:
                    verdict = "suspect_fixture"
                elif not engine_has_onset:
                    verdict = "suspect_engine"
                else:
                    verdict = "need_human"
            else:
                verdict = "need_human"
        rows.append((i + 1, gtx, gt, es, es - gt, len(ws), widest, verdict))

    print("\n=== 逐行对照（Δ 按绝对值排序，前 20）===")
    for r in sorted(rows, key=lambda x: -abs(x[4]))[:20]:
        mark = "⚠️" if abs(r[4]) > LINE_TOL_MS else "  "
        print(f" {mark} 行{r[0]:3d} {r[2]:8d} → {r[3]:8d} (Δ{r[4]:+6d}ms) 字{r[5]:2d} 最宽{r[6]:5d}ms  {r[1]}")
    print(f"\n超差行数: {n_bad} / {len(rows)}（容差 {LINE_TOL_MS}ms）")
    print("纪律：禁止用引擎输出回填夹具；只有三重证据（起振+F0+听检）齐备才可逐行改夹具。")

    if args.diff:
        os.makedirs(os.path.dirname(os.path.abspath(args.diff)) or ".", exist_ok=True)
        with open(args.diff, "w", encoding="utf-8") as f:
            f.write("# Golden 只读审计报告\n\n")
            f.write(f"- 夹具：`{args.golden}`\n- 音频：`{args.audio}`\n"
                    f"- 引擎：`{out.get('align_engine', '?')}`\n"
                    f"- 容差：{LINE_TOL_MS}ms ｜ 超差行数：**{n_bad}/{len(rows)}**\n\n")
            f.write("| 行 | 文本 | 夹具行首 | 引擎行首 | Δms | 字数 | 最大字宽 | 判定 |\n|---|---|---|---|---|---|---|---|\n")
            for r in rows:
                f.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]:+d} | {r[5]} | {r[6]} | {r[7]} |\n")
        print(f"📄 diff 报告：{args.diff}")
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
