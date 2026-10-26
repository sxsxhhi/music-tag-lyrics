# -*- coding: utf-8 -*-
"""语言路由检测器 (Language Route Detector) —— 判别普通话 (zh) 与粤语 (yue)。

架构原则（v1.5.0 §四）：
1. 歌手/曲库元数据白名单（强先验）；
2. 粤歌书面意象 N-Gram 词表（高权重加分，解决海阔天空/富士山下全书面语盲区）；
3. 口语粤字命中（仅加分，命中率为 0 不扣分）；
4. 原始混音前段音频探针（无需 Demucs 分离）；
5. 全低置信交互询问 / 非交互模式默认普通话并告警。
"""

import os
import re
from typing import Dict, Any, Optional

from .jyutping import is_cantonese_slang, CANTONESE_IMAGERY_WORDS

# 典型粤语/Cantopop 歌手白名单（强先验）
CANTOPOP_ARTISTS = frozenset([
    "beyond", "黄家驹", "黃家駒",
    "黄凯芹", "黃凱芹",
    "张国荣", "張國榮",
    "谭咏麟", "譚詠麟",
    "陈奕迅", "陳奕迅",
    "李克勤",
    "张学友", "張學友",
    "刘德华", "劉德華",
    "黎明",
    "郭富城",
    "王菲",
    "梅艳芳", "梅艷芳",
    "陈慧娴", "陳慧嫻",
    "林忆莲", "林憶蓮",
    "叶倩文", "葉蒨文",
    "林子祥",
    "许冠杰", "許冠傑",
    "罗文", "羅文",
    "甄妮",
    "汪明荃",
    "关正杰", "關正傑",
    "关淑怡", "關淑怡",
    "卢巧音", "盧巧音",
    "容祖儿", "容祖兒",
    "杨千嬅", "楊千嬅",
    "Twins", "twins", "钟欣潼", "蔡卓妍",
    "古巨基",
    "张敬轩", "張敬軒",
    "谢霆锋", "謝霆鋒",
    "草蜢",
    "达明一派", "達明一派",
    "侧田", "側田",
    "卫兰", "衛蘭",
    "周柏豪",
    "许廷铿", "許廷鏗",
    "梁汉文", "梁漢文",
    "王杰", "王傑",
    "泰迪罗宾",
    "叶振棠", "葉振棠",
    "彭羚",
    "汤宝如", "湯寶如",
    "黎瑞恩",
    "薛凯琪", "薛凱琪",
    "泳儿", "泳兒",
    "连诗雅", "連詩雅",
    "AGA", "aga", "江海迦",
    "陈柏宇", "陳柏宇",
    "邓丽欣", "鄧麗欣",
    "方力申",
    "林家谦", "林家謙",
    "MC 张天赋", "张天赋", "張天賦",
])


def is_cantopop_artist(artist: str) -> bool:
    """检查歌手名是否命中 Cantopop 白名单。"""
    if not artist:
        return False
    a_lower = artist.strip().lower()
    for cand in CANTOPOP_ARTISTS:
        if cand.lower() in a_lower:
            return True
    return False


def match_imagery_ngrams(text: str) -> int:
    """检查文本命中粤歌书面意象词数量。"""
    if not text:
        return 0
    count = 0
    for w in CANTONESE_IMAGERY_WORDS:
        if w in text:
            count += 1
    return count


def probe_audio_cantonese_confidence(
    audio_path: str,
    probe_seconds: float = 20.0,
    device: str = "cuda"
) -> float:
    """原始混音前段音频探针（无需 Demucs），检测粤音 CTC 置信度。

    返回 0.0 ~ 1.0 的置信度分数。若无模型或环境不可用，安全返回 0.0。
    """
    if not audio_path or not os.path.isfile(audio_path):
        return 0.0
    try:
        import soundfile as sf
        import torch
        import torchaudio.transforms as T
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        model_id = "scottykwok/wav2vec2-large-xlsr-cantonese"
        # 尝试本地加载
        try:
            proc = Wav2Vec2Processor.from_pretrained(model_id, local_files_only=True)
            model = Wav2Vec2ForCTC.from_pretrained(model_id, local_files_only=True)
        except Exception:
            return 0.0

        if device == "cuda" and torch.cuda.is_available():
            model = model.to("cuda")
        else:
            model = model.to("cpu")
            device = "cpu"
        model.eval()

        info = sf.info(audio_path)
        sr = info.samplerate
        read_frames = int(min(probe_seconds, info.duration) * sr)
        data, _ = sf.read(audio_path, frames=read_frames, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)

        if sr != 16000:
            resampler = T.Resample(sr, 16000)
            data_t = torch.from_numpy(data)
            audio_16k = resampler(data_t).numpy()
        else:
            audio_16k = data

        inputs = proc(audio_16k, sampling_rate=16000, return_tensors="pt").input_values.to(device)
        with torch.no_grad():
            logits = model(inputs).logits

        probs = torch.softmax(logits, dim=-1)  # (1, T, V)
        max_probs, preds = torch.max(probs[0], dim=-1)

        # 过滤 pad_token (3554) 与空白
        pad_id = getattr(model.config, "pad_token_id", 3554)
        non_pad_mask = preds != pad_id
        if non_pad_mask.sum() == 0:
            return 0.0

        mean_conf = float(max_probs[non_pad_mask].mean().item())
        del model
        if device == "cuda":
            torch.cuda.empty_cache()
        return mean_conf
    except Exception:
        return 0.0


def detect_language(
    lyrics_text: str = "",
    title: str = "",
    artist: str = "",
    audio_path: Optional[str] = None,
    interactive: bool = False,
    device: str = "cuda",
) -> Dict[str, Any]:
    """综合多判据判定曲目语种：'zh' (普通话) 或 'yue' (粤语)。

    返回格式：
    {
        "lang": "yue" | "zh",
        "confidence": float,
        "is_low_confidence": bool,
        "details": { ... }
    }
    """
    canto_score = 0.0
    details: Dict[str, Any] = {
        "artist_matched": False,
        "slang_count": 0,
        "imagery_count": 0,
        "audio_probe_conf": 0.0,
    }

    # 1. 歌手元数据先验
    if is_cantopop_artist(artist):
        canto_score += 0.40
        details["artist_matched"] = True

    # 2. 歌词口语字命中（仅加分，海阔天空等书面歌命中率为 0 不扣分）
    slang_matches = sum(1 for ch in lyrics_text if is_cantonese_slang(ch))
    details["slang_count"] = slang_matches
    if slang_matches >= 3:
        canto_score += 0.50
    elif slang_matches >= 1:
        canto_score += 0.30

    # 3. 粤歌书面意象词命中（解决书面粤语歌盲区）
    full_text = f"{title} {lyrics_text}"
    imagery_matches = match_imagery_ngrams(full_text)
    details["imagery_count"] = imagery_matches
    if imagery_matches >= 2:
        canto_score += 0.40
    elif imagery_matches >= 1:
        canto_score += 0.25

    # 4. 音频探针终裁（当先验在模糊区 0.20 ~ 0.50 且提供了音频时）
    if 0.15 <= canto_score <= 0.60 and audio_path and os.path.isfile(audio_path):
        probe_conf = probe_audio_cantonese_confidence(audio_path, probe_seconds=20.0, device=device)
        details["audio_probe_conf"] = round(probe_conf, 3)
        if probe_conf >= 0.35:
            canto_score += 0.30
        elif probe_conf < 0.15 and canto_score < 0.40:
            canto_score -= 0.15

    confidence = min(1.0, max(0.0, canto_score))
    details["total_score"] = round(canto_score, 3)

    # 裁决阈值
    if canto_score >= 0.50:
        return {
            "lang": "yue",
            "confidence": confidence,
            "is_low_confidence": False,
            "details": details,
        }

    if canto_score < 0.20:
        return {
            "lang": "zh",
            "confidence": 1.0 - confidence,
            "is_low_confidence": False,
            "details": details,
        }

    # 模糊区 (0.20 <= canto_score < 0.50)
    if interactive:
        try:
            choice = input(
                f"\n❓ [lang_detect] 检测到曲目疑似粤语 (置信得分 {canto_score:.2f})，"
                f"是否启用粤语声学打轴内核？[y/N]: "
            ).strip().lower()
            if choice in ("y", "yes"):
                return {
                    "lang": "yue",
                    "confidence": confidence,
                    "is_low_confidence": True,
                    "details": details,
                }
        except (EOFError, KeyboardInterrupt):
            pass

    # 非交互默认普通话，并打印规范告警
    print(
        f"⚠️  [lang_detect] 语种低置信（得分 {canto_score:.2f}），已按普通话链路处理；"
        f"如为粤语请使用 --lang yue 显式指定。"
    )
    return {
        "lang": "zh",
        "confidence": 1.0 - confidence,
        "is_low_confidence": True,
        "details": details,
    }
