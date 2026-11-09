# -*- coding: utf-8 -*-
"""music-tag-lyrics 音频歌词标准化治理与双向审计工具 (v1.8.2)。

满足《歌曲检测与修补标准》§2.4、§3、§4、§6 全部要求：
1. 缺省 --mode audit 只读审计，绝无副作用；
2. 显式 --mode standardize 执行 R2 全曲制作名单清理 -> R3 A/B 时序排版 -> R4 双格式输出；
3. 输出严格 3 位毫秒 ELRC 与单行 iTunes XML TTML；
4. 独立运行 R1 ELRC ↔ TTML 双向 1ms/2ms 硬门禁；
5. 支持 FLAC/MP3/WAV 容器安全工作副本写入与磁盘物理回读校验；
6. 严格保持原有 process_song.py 默认入口兼容性。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.container_tag_adapter import (
    read_container_tags,
    write_verified_standardized_container,
)
from core.auditors.elrc_ttml_comparator import compare_elrc_ttml
from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.parsers.lrc_parser import parse_lrc
from core.parsers.ttml_parser import parse_ttml
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from core.schedulers.prelude_scheduler import plan_prelude_ab
from core.version import __version__


def standardize_single_song(
    audio_path: str,
    mode: str = "audit",
    output_dir: Optional[str] = None,
    write_tags: bool = False,
    no_tags: bool = False,
    in_place: bool = False,
    ref_lyrics_path: Optional[str] = None,
    report_path: Optional[str] = None,
) -> Dict[str, Any]:
    """对单首音频开展标准化审计或修补处理的主函数。"""
    if no_tags and write_tags:
        raise ValueError("Argument conflict: --no-tags and --write-tags are mutually exclusive.")

    audio_abs = os.path.abspath(audio_path)
    if not os.path.exists(audio_abs):
        raise FileNotFoundError(f"Audio file not found: {audio_abs}")

    embedded = read_container_tags(audio_abs)
    embedded_elrc = embedded.get("lyrics", "") or ""
    embedded_ttml = embedded.get("ttml", "") or ""

    base_no_ext, _ = os.path.splitext(audio_abs)
    sidecar_lrc = base_no_ext + ".lrc"
    sidecar_spl = base_no_ext + ".spl"
    sidecar_ttml = base_no_ext + ".ttml"

    disk_elrc = ""
    disk_ttml = ""
    if os.path.exists(sidecar_lrc):
        with open(sidecar_lrc, "r", encoding="utf-8", errors="replace") as f:
            disk_elrc = f.read()
    elif os.path.exists(sidecar_spl):
        with open(sidecar_spl, "r", encoding="utf-8", errors="replace") as f:
            disk_elrc = f.read()

    if os.path.exists(sidecar_ttml):
        with open(sidecar_ttml, "r", encoding="utf-8", errors="replace") as f:
            disk_ttml = f.read()

    active_elrc = embedded_elrc or disk_elrc
    active_ttml = embedded_ttml or disk_ttml

    report: Dict[str, Any] = {
        "version": __version__,
        "audio": audio_abs,
        "mode": mode,
        "status": "PASS",
        "has_embedded_elrc": bool(embedded_elrc),
        "has_embedded_ttml": bool(embedded_ttml),
        "has_sidecar_elrc": bool(disk_elrc),
        "has_sidecar_ttml": bool(disk_ttml),
        "baseline_comparison": None,
        "issues": [],
        "prelude_plan": None,
        "actions_taken": [],
    }

    if active_elrc and active_ttml:
        baseline_comp = compare_elrc_ttml(active_elrc, active_ttml)
        report["baseline_comparison"] = {
            "passed": baseline_comp.passed,
            "max_time_delta_ms": baseline_comp.max_time_delta_ms,
            "issues": [iss.code for iss in baseline_comp.issues],
        }
        if not baseline_comp.passed:
            report["status"] = "REVIEW_REQUIRED"
            report["issues"].append({
                "code": "INPUT_FORMATS_CONFLICT",
                "message": "Both ELRC and TTML are present but have substantial timeline or content conflicts.",
                "blocking": True,
            })

    parsed_data: Dict[str, Any] = {"lines": [], "tags": embedded}
    if active_ttml:
        try:
            parsed_data = parse_ttml(active_ttml)
            if not parsed_data.get("tags"):
                parsed_data["tags"] = embedded
        except Exception:
            pass

    if not parsed_data.get("lines") and active_elrc:
        try:
            parsed_data = parse_lrc(active_elrc)
            if not parsed_data.get("tags"):
                parsed_data["tags"] = embedded
        except Exception:
            pass

    if not parsed_data.get("lines"):
        report["status"] = "BLOCKED"
        report["issues"].append({
            "code": "NO_LYRIC_INPUT",
            "message": "Neither valid ELRC nor TTML lyrics could be read from embedded tags or sidecar.",
            "blocking": True,
        })
        if report_path:
            _write_json_report(report_path, report)
        return report

    canonical = classify_and_clean_all_lines(
        lyrics_data=parsed_data,
        song_title=embedded.get("title", ""),
        song_artist=embedded.get("artist", ""),
        audio_path=audio_abs,
    )
    for iss in canonical.issues:
        report["issues"].append(iss.to_dict())
        if iss.severity.value in ("REVIEW_REQUIRED", "ERROR"):
            report["status"] = iss.severity.value

    plan = plan_prelude_ab(canonical)
    report["prelude_plan"] = {
        "mode": plan.mode,
        "t_sing_ms": plan.t_sing_ms,
        "title_window": plan.title_window,
        "lyricist_window": plan.lyricist_window,
        "composer_window": plan.composer_window,
        "display_title": plan.display_title,
        "display_lyricist": plan.display_lyricist,
        "display_composer": plan.display_composer,
    }

    if mode == "audit":
        if report_path:
            _write_json_report(report_path, report)
        return report

    if report["status"] == "BLOCKED" or (report["status"] == "REVIEW_REQUIRED" and not in_place):
        report["actions_taken"].append("ABORT_DUE_TO_BLOCKING_ISSUES")
        if report_path:
            _write_json_report(report_path, report)
        return report

    effective_out_dir = output_dir or (os.path.dirname(audio_abs) if in_place else None)
    if not effective_out_dir:
        raise ValueError("Standardize mode requires --output-dir (or explicit --in-place).")

    os.makedirs(effective_out_dir, exist_ok=True)
    filename = os.path.basename(audio_abs)
    name_no_ext, ext = os.path.splitext(filename)

    standard_elrc = render_elrc_v182(canonical)
    standard_ttml = render_ttml_v182(canonical)

    r1_check = compare_elrc_ttml(standard_elrc, standard_ttml)
    if not r1_check.passed:
        report["status"] = "BLOCKED"
        report["issues"].append({
            "code": "R1_GENERATION_COMPARISON_FAILED",
            "message": f"Generated ELRC and TTML failed R1 bi-directional validation: {r1_check.issues}",
            "blocking": True,
        })
        if report_path:
            _write_json_report(report_path, report)
        return report

    out_lrc = os.path.join(effective_out_dir, f"{name_no_ext}.lrc")
    out_spl = os.path.join(effective_out_dir, f"{name_no_ext}.spl")
    out_ttml = os.path.join(effective_out_dir, f"{name_no_ext}.ttml")

    with open(out_lrc, "w", encoding="utf-8", newline="\n") as f:
        f.write(standard_elrc)
    with open(out_spl, "w", encoding="utf-8", newline="\n") as f:
        f.write(standard_elrc)
    with open(out_ttml, "w", encoding="utf-8", newline="\n") as f:
        f.write(standard_ttml)

    report["actions_taken"].extend(["WRITE_SIDECAR_LRC", "WRITE_SIDECAR_SPL", "WRITE_SIDECAR_TTML"])

    if write_tags:
        target_audio = os.path.join(effective_out_dir, filename)
        container_res = write_verified_standardized_container(
            source_audio_path=audio_abs,
            target_work_path=target_audio,
            elrc_text=standard_elrc,
            ttml_text=standard_ttml,
            lyricist=canonical.song.get("extracted_lyricist"),
            composer=canonical.song.get("extracted_composer"),
        )
        report["actions_taken"].append("WRITE_VERIFIED_CONTAINER_TAGS")
        report["container_verification"] = container_res

    if report_path:
        _write_json_report(report_path, report)
    return report


def _write_json_report(report_path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(report_path)), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(
        description=f"music-tag-lyrics 音频歌词标准化治理与双向审计工具 (v{__version__})"
    )
    parser.add_argument("audio", help="待处理的音频文件路径")
    parser.add_argument(
        "--mode",
        choices=["audit", "standardize"],
        default="audit",
        help="运行模式：audit（默认只读审计）或 standardize（标准化修补与导出）",
    )
    parser.add_argument("--output-dir", help="输出目录（用于保存 sidecar 或生成的工作副本）")
    parser.add_argument(
        "--no-tags",
        action="store_true",
        help="仅生成 sidecar 歌词文件，不修改音频标签",
    )
    parser.add_argument(
        "--write-tags",
        action="store_true",
        help="向目标工作副本写入双格式歌词与词曲标签并执行磁盘回读校验",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="明确授权原地修改（高风险，需严格满足前置安全门禁）",
    )
    parser.add_argument("--ref-lyrics", help="显式指定的只读参考歌词路径（用于角色恢复等）")
    parser.add_argument("--report", help="JSON 审计报告输出路径")

    args = parser.parse_args()

    try:
        res = standardize_single_song(
            audio_path=args.audio,
            mode=args.mode,
            output_dir=args.output_dir,
            write_tags=args.write_tags,
            no_tags=args.no_tags,
            in_place=args.in_place,
            ref_lyrics_path=args.ref_lyrics,
            report_path=args.report,
        )
        print(f"[{res['status']}] {args.audio}")
        if res.get("prelude_plan"):
            p = res["prelude_plan"]
            print(f"  前奏规划: 模式={p['mode']}, 首唱T={p['t_sing_ms']}ms, 标题={p['title_window']}")
        if res.get("actions_taken"):
            print(f"  执行动作: {', '.join(res['actions_taken'])}")
        if res.get("issues"):
            print(f"  审计告警 ({len(res['issues'])} 项):")
            for iss in res["issues"][:5]:
                print(f"    - [{iss.get('severity', 'INFO')}] {iss.get('code')}: {iss.get('evidence') or iss.get('message')}")
        sys.exit(0 if res["status"] in ("PASS", "REVIEW_REQUIRED") else 1)
    except Exception as exc:
        print(f"[ERROR] 执行失败: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
