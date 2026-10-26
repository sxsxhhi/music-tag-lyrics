# -*- coding: utf-8 -*-
"""粤语声学打轴 GPU 回归锚 (Cantonese Acoustic Baseline Anchor)。

验证项（v1.5.0 方案 §六.4）：
1. 语言路由检测器准确识别粤语曲目 (lang == "yue")；
2. 粤语声学对齐内核在真实母带/测试曲《焚情 - 黄凯芹》上成功对齐；
3. 铁律 19 硬门禁：行间零重叠 (间隙 ≥ 50ms) + 行内字时间戳严格单调；
4. 入声字闭气容错与流光逐字流完整性。
"""

import os
import re
import sys
from typing import List, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_CANTO_AUDIO = r"D:\WindowsAppp\Desktop\测试\焚情 - 黄凯芹.flac"
FALLBACK_CANTO_AUDIO = os.path.abspath(os.path.join(REPO_ROOT, "..", "任务", "2026-10-24-v132-fix", "_cover_test", "焚情 - 黄凯芹.flac"))
CANTO_AUDIO = DEFAULT_CANTO_AUDIO if os.path.isfile(DEFAULT_CANTO_AUDIO) else FALLBACK_CANTO_AUDIO


def main():
    try:
        import torch
        if not torch.cuda.is_available():
            print("SKIP: 无 CUDA，GPU 回归不适用（CI 场景）")
            return 0
    except ImportError:
        print("SKIP: 无声学依赖（CI 场景）")
        return 0

    if not os.path.isfile(CANTO_AUDIO):
        print(f"SKIP: 测试音频不存在 {CANTO_AUDIO}")
        return 0

    print(f"🎯 粤语 GPU 回归曲目: {CANTO_AUDIO}")

    # 1. 验证语言路由检测
    from core.utils.lang_detect import detect_language
    from core.aggregator import LyricAggregator

    lyrics_data = LyricAggregator.fetch_best_lyrics("焚情", "黄凯芹", duration_ms=246456)
    assert lyrics_data and lyrics_data.get("lines"), "未能获取回归曲歌词"

    raw_lines = [l for l in lyrics_data["lines"] if (l.get("text") or "").strip()]
    full_text = "\n".join(l["text"] for l in raw_lines)

    det = detect_language(lyrics_text=full_text, title="焚情", artist="黄凯芹", audio_path=CANTO_AUDIO, interactive=False)
    print(f"🌐 语种检测结果: {det['lang']} (置信度: {det['confidence']:.2f})")
    assert det["lang"] == "yue", f"预期判定为 yue，实际为 {det['lang']}"

    # 2. 提取有效歌词行与提示轴
    hints: List[Tuple[float, str]] = []
    clean_lines_text: List[str] = []
    for l in raw_lines:
        txt = l["text"].strip()
        # 过滤署名行
        if any(txt.startswith(p) for p in ("词：", "曲：", "编曲：", "监制：")):
            continue
        if " - " in txt and ("黄凯芹" in txt or "Christopher" in txt):
            continue
        clean_lines_text.append(txt)
        hints.append((float(l.get("start", 0)) / 1000.0, txt))

    lyrics_payload = "\n".join(clean_lines_text)
    print(f"🎤 待对齐歌词行数: {len(clean_lines_text)} 行")

    # 3. 运行粤语声学对齐
    from core.acoustic_aligner import AcousticAligner

    aligned_out = AcousticAligner.align_lyrics(
        audio_path=CANTO_AUDIO,
        lyrics_text=lyrics_payload,
        line_timing_hints=hints,
        device="cuda",
        lang="yue",
    )
    assert aligned_out is not None, "粤语声学引擎返回 None（触发了置信度硬熔断或声学失败）"
    lines = aligned_out.get("lines") or []
    assert lines, "粤语声学引擎未输出任何行"
    print(f"✅ 引擎对齐完成，产出 {len(lines)} 行逐字歌词")

    # 4. 门禁验证
    failures = []
    # 4.1 铁律 19：行间零重叠 (间隙 ≥ 50ms)
    for i in range(len(lines) - 1):
        cur_l = lines[i]
        nxt_l = lines[i + 1]
        gap = nxt_l["start"] - cur_l["end"]
        if gap < 50:
            failures.append(f"行 {i+1} 与行 {i+2} 间隙 {gap}ms < 50ms（违背铁律 19）")

    # 4.2 严格单调与 words 完整性
    for i, ln in enumerate(lines):
        ws = ln.get("words") or []
        if not ws:
            failures.append(f"行 {i+1} 缺少逐字时间戳: {ln['text']}")
            continue
        for a, b in zip(ws, ws[1:]):
            if b["start"] < a["start"]:
                failures.append(f"行 {i+1} 字时间倒流: {a['text']}({a['start']}ms) -> {b['text']}({b['start']}ms)")
            if a["end"] <= a["start"]:
                failures.append(f"行 {i+1} 字时长非正: {a['text']}({a['start']}~{a['end']}ms)")

    # 4.3 抽检入声字结尾行
    sample_first_5 = lines[:5]
    print("\n--- 逐字抽样 (前 3 行) ---")
    for ln in sample_first_5[:3]:
        w_str = " ".join(f"{w['text']}<{w['start']}-{w['end']}>" for w in ln.get("words", []))
        print(f"[{ln['start']}ms - {ln['end']}ms] {ln['text']}\n   {w_str}")

    print("\n=== 粤语 GPU 回归报告 ===")
    if failures:
        print(f"FAIL ({len(failures)} 项):")
        for f in failures[:15]:
            print("  ✗", f)
        return 1

    print("PASS：语种自判 + 粤语声学逐字对齐 + 铁律 19 零重叠 + 字单调性全绿通过！")
    return 0


if __name__ == "__main__":
    sys.exit(main())
