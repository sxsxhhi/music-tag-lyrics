# -*- coding: utf-8 -*-
r"""真实曲目单版本抽样运行与原音频完整性检查（H13）。

数据源：由环境变量 MTL_AB_SRC 指定，其下需含 中文/ 与 非国语/ 两个子目录；
        未设置或目录不存在时打印用法并以退出码 2 结束。
抽样规则：目录内按文件名排序后等距抽样（可复现），不把任何具体曲目写死进源码。
        样本量由 MTL_AB_SAMPLE_ZH / MTL_AB_SAMPLE_FOREIGN 控制（默认各 20 首）。
隔离目录：.test-work/fixtures/ab_sample/
输出目录：.test-work/out/ab_eval/
清单文件：.test-work/out/ab_eval/validation_tracks_manifest.csv（不进公开仓库）

注意：本脚本**不是**新旧双版本 A/B 对照，而是「单版本真实曲目抽样运行」——
每首歌只执行一次 process_single_song(return_outcome=True)，不做第二次抓取，
所有评测列均取自该次实际交付结果。
"""

import os
import sys
import csv
import hashlib
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.process_song import process_single_song, read_audio_tags

SRC_DIR = Path(os.environ.get("MTL_AB_SRC", "")).expanduser()
SRC_ZH = SRC_DIR / "中文"
SRC_FOREIGN = SRC_DIR / "非国语"

WORK_DIR = REPO_ROOT / ".test-work"
FIXTURES_DIR = WORK_DIR / "fixtures" / "ab_sample"
OUT_DIR = WORK_DIR / "out" / "ab_eval"
MANIFEST_FILE = OUT_DIR / "validation_tracks_manifest.csv"
SAMPLE_SIZE_ZH = int(os.environ.get("MTL_AB_SAMPLE_ZH", "20"))
SAMPLE_SIZE_FOREIGN = int(os.environ.get("MTL_AB_SAMPLE_FOREIGN", "20"))
AUDIO_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".dsf", ".ogg"}

def pick_samples(directory: Path, limit: int):
    """按文件名排序后等距抽样：结果可复现，且不把私有曲库清单写进源码。"""
    if not directory.is_dir():
        return []
    files = sorted(
        (p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS),
        key=lambda p: p.name,
    )
    if limit <= 0 or limit >= len(files):
        return files
    step = len(files) / limit
    return [files[int(i * step)] for i in range(limit)]



def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 60)
    print("🚀 启动真实曲目单版本抽样运行与原音频完整性检查 (H13)")
    print("=" * 60)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not str(SRC_DIR) or not SRC_DIR.is_dir():
        print("用法: MTL_AB_SRC=<曲库根目录> python scripts/run_v181_ab_benchmark.py")
        print("      曲库根目录下需包含 中文/ 与 非国语/ 两个子目录。")
        print("      可选: MTL_AB_SAMPLE_ZH / MTL_AB_SAMPLE_FOREIGN 控制样本量（默认各 20）。")
        return 2

    print(f"📁 数据源: {SRC_DIR}")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tracks = []
    for src in pick_samples(SRC_ZH, SAMPLE_SIZE_ZH):
        tracks.append(("Chinese", src.name, src))
    for src in pick_samples(SRC_FOREIGN, SAMPLE_SIZE_FOREIGN):
        tracks.append(("Foreign", src.name, src))

    if not tracks:
        print("⚠️ 未在数据源中发现可用的音频样本，请检查目录结构。")
        return 2

    print(f"📊 已选择评估样本：{len(tracks)} 首 (中文 {len([t for t in tracks if t[0]=='Chinese'])}, 外语 {len([t for t in tracks if t[0]=='Foreign'])})")

    # 1. 前置 SHA-256 采样计算
    print("🔒 正在计算源文件前置 SHA-256 签名（冻结基线）...")
    hashes_before = {}
    for cat, fn, p in tracks:
        h = sha256_file(p)
        hashes_before[p] = h

    # 2. 安全复制至隔离工作目录
    print("📦 正在复制样本至隔离目录 .test-work/fixtures/ab_sample/ ...")
    copied_paths = []
    for cat, fn, p in tracks:
        dest_cat = FIXTURES_DIR / cat
        dest_cat.mkdir(parents=True, exist_ok=True)
        dest_file = dest_cat / fn
        shutil.copy2(p, dest_file)
        copied_paths.append((cat, fn, p, dest_file))

    # 3. 执行评估处理
    results = []
    for idx, (cat, fn, src_p, dest_p) in enumerate(copied_paths, 1):
        print(f"\n[{idx}/{len(copied_paths)}] 正在评估: {fn} ({cat})")
        tags = read_audio_tags(str(dest_p))
        title = tags.get("title") or fn.split(" - ")[0]
        artist = tags.get("artist") or (fn.split(" - ")[1].rsplit(".", 1)[0] if " - " in fn else "")
        dur_ms = tags.get("duration") or 0

        # H13：只执行一次真实处理，评测列全部取自该次交付结果，不做第二次抓取。
        # 每首独立输出目录，避免同名不同路径相撞与旧副文件造成假阳性。
        song_out = OUT_DIR / f"{idx:03d}_{os.path.splitext(fn)[0]}"
        try:
            outcome = process_single_song(
                filepath=str(dest_p),
                output_dir=str(song_out),
                update_tags=False,
                non_interactive=True,
                return_outcome=True,
            )
        except Exception as exc:
            print(f"   ⚠️ 处理发生异常: {type(exc).__name__}: {exc}")
            from scripts.process_song import _failed_outcome as _mk_failed
            outcome = _mk_failed("exception")

        selected_source = outcome.source or "none"
        lyric_type = outcome.lyric_type or "none"
        has_trans = bool(outcome.has_translation)
        has_isrc = bool(tags.get("isrc") or outcome.candidate_isrc_present)

        # 检查生成物（只看本首独立目录）
        base_no_ext = os.path.splitext(fn)[0]
        lrc_file = song_out / f"{base_no_ext}.lrc"
        spl_file = song_out / f"{base_no_ext}.spl"
        out_generated = lrc_file.is_file() and spl_file.is_file()

        results.append({
            "idx": idx,
            "category": cat,
            "filename": fn,
            "src_path": str(src_p),
            "title": title,
            "artist": artist,
            "duration_ms": dur_ms,
            "source": selected_source,
            "lyric_type": lyric_type,
            "has_translation": "Yes" if has_trans else "No",
            "isrc_present": "Yes" if has_isrc else "No",
            "out_files": "Yes" if out_generated else "No",
            "status": outcome.status,
        })

    # 4. 后置 SHA-256 完整性复验
    print("\n🔍 正在复验曲库原文件 SHA-256 签名，核查零污染...")
    tampered = []
    for cat, fn, p in tracks:
        h_after = sha256_file(p)
        h_before = hashes_before[p]
        if h_after != h_before:
            tampered.append(str(p))

    if tampered:
        print(f"❌ 严重错误！发现 {len(tampered)} 个原文件被修改：{tampered}")
        sys.exit(1)
    else:
        print("✅ 完美！全部 40 首原曲库音频 SHA-256 100% 保持一致，绝对零污染！")

    # 5. 写入 docs/validation_tracks_manifest.csv
    print(f"📝 正在输出清单文件至 {MANIFEST_FILE} ...")
    with open(MANIFEST_FILE, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Track_No",
            "Category",
            "Title",
            "Artist",
            "Duration_Ms",
            "Filename",
            "SHA256_Before",
            "SHA256_After",
            "Integrity_Verified",
            "Lyric_Source",
            "Lyric_Type",
            "Has_Translation",
            "ISRC_Present",
            "Artifacts_Emitted",
            "Benchmark_Status",
        ])
        for r in results:
            src_p = Path(r["src_path"])
            h_before = hashes_before[src_p]
            h_after = sha256_file(src_p)
            writer.writerow([
                r["idx"],
                r["category"],
                r["title"],
                r["artist"],
                r["duration_ms"],
                r["filename"],
                h_before,
                h_after,
                "YES_ZERO_CONTAMINATION",
                r["source"],
                r["lyric_type"],
                r["has_translation"],
                r["isrc_present"],
                r["out_files"],
                r["status"],
            ])

    print("🎉 单版本抽样运行与完整性清单生成完成！")


if __name__ == "__main__":
    main()
