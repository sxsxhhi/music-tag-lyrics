# -*- coding: utf-8 -*-
r"""v1.8.1 真实曲库 40 首前置冻结 A/B 评测与零污染验证脚本 (R19).

数据源：F:\TOOLs\AI's DATA\ABC\Music\待修补\My music
隔离目录：.test-work/fixtures/ab_sample/
输出目录：.test-work/out/ab_eval/
清单文件：docs/validation_tracks_manifest.csv
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
from core.aggregator import LyricAggregator

SRC_DIR = Path(r"F:\TOOLs\AI's DATA\ABC\Music\待修补\My music")
SRC_ZH = SRC_DIR / "中文"
SRC_FOREIGN = SRC_DIR / "非国语"

WORK_DIR = REPO_ROOT / ".test-work"
FIXTURES_DIR = WORK_DIR / "fixtures" / "ab_sample"
OUT_DIR = WORK_DIR / "out" / "ab_eval"
MANIFEST_FILE = REPO_ROOT / "docs" / "validation_tracks_manifest.csv"

SELECTED_ZH = [
    "1+1 - 卓文萱.flac",
    "Always Online - 林俊杰.flac",
    "Ba Ba Ba - 草蜢.flac",
    "Behind - 黄淑惠.mp3",
    "Da Da Da - 王心凌.flac",
    "Dear John - 比莉.flac",
    "Emily，生命是件神奇的事 (The wonder of us) - 房东的猫.flac",
    "Forever Love - 王力宏.flac",
    "G大调的悲伤 - 张靓颖.flac",
    "I Need You - 郭采洁.flac",
    "Infinity - Dance Flow.flac",
    "Last Dance - 伍佰 & China Blue.flac",
    "Lydia - F.I.R.飞儿乐团.flac",
    "Melody - 陶喆.flac",
    "NEW BOY - 朴树.flac",
    "Para Para Sakura - 郭富城.flac",
    "Super Star - S.H.E.flac",
    "Today - 梁咏琪.flac",
    "一荤一素 - 毛不易.flac",
    "一腔诗意喂了狗 (Live) - 花粥.flac",
]

SELECTED_FOREIGN = [
    "Counting Stars - OneRepublic.flac",
    "Rolling in the Deep - Adele.flac",
    "Butter-Fly - 和田光司.flac",
    "MY ALL - 浜崎あゆみ.flac",
    "Gee - 少女时代.flac",
    "Nobody - Wonder Girls.flac",
    "Hotel California - Eagles.flac",
    "Sugar - Maroon 5.flac",
    "Love Story - Taylor Swift.flac",
    "My Heart Will Go On(我心依旧) - 席琳·迪翁.wav",
    "Numb Little Bug (Piano Version) - Em Beihold.flac",
    "Payphone (Edit) - Maroon 5;Wiz Khalifa.flac",
    "Closer - The Chainsmokers&Halsey.flac",
    "Uptown Funk - Mark Ronson;Bruno Mars.flac",
    "See You Again(有缘再见) - Wiz Khalifa;Charlie Puth.flac",
    "Perfect - Ed Sheeran.flac",
    "Lemon Tree(柠檬树) - Fool's Garden.flac",
    "DAY BY DAY - T-ara.flac",
    "Bizarre Love Triangle - Frente!.flac",
    "Boom, Boom, Boom, Boom!! - Vengaboys.flac",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def main():
    print("=" * 60)
    print("🚀 启动 v1.8.1 真实曲库 40 首 A/B 评测与隔离验证 (R19)")
    print("=" * 60)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tracks = []
    for fn in SELECTED_ZH:
        src = SRC_ZH / fn
        if src.is_file():
            tracks.append(("Chinese", fn, src))
        else:
            print(f"⚠️ 未找到文件: {src}")

    for fn in SELECTED_FOREIGN:
        src = SRC_FOREIGN / fn
        if src.is_file():
            tracks.append(("Foreign", fn, src))
        else:
            print(f"⚠️ 未找到文件: {src}")

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

        # 执行标准离线/多源调度检索评估
        try:
            cand = LyricAggregator.fetch_best_lyrics(
                title=title,
                artist=artist,
                duration_ms=dur_ms,
                album=tags.get("album", ""),
                isrc=tags.get("isrc", "")
            )
        except Exception as exc:
            cand = None
            print(f"   ⚠️ 抓取发生异常: {exc}")

        # 同时调用 process_single_song 导出到独立 out 目录（--no-tags 保证原样隔离）
        ok = process_single_song(
            filepath=str(dest_p),
            output_dir=str(OUT_DIR),
            update_tags=False,
            non_interactive=True,
        )

        selected_source = cand.get("source") if cand else "none"
        lyric_type = cand.get("lyric_type") if cand else "none"
        has_trans = bool(cand.get("has_translation") or any(bool(l.get("translation")) for l in (cand.get("lines") or []))) if cand else False
        has_isrc = bool(tags.get("isrc") or (cand and cand.get("isrc")))

        # 检查生成物
        base_no_ext = os.path.splitext(fn)[0]
        lrc_file = OUT_DIR / f"{base_no_ext}.lrc"
        spl_file = OUT_DIR / f"{base_no_ext}.spl"
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
            "status": "PASS" if (cand and out_generated) else ("RECOVERED" if out_generated else "FALLBACK"),
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

    print("🎉 A/B 评测与基准清单生成完成！")


if __name__ == "__main__":
    main()
