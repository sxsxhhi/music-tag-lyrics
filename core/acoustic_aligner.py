# -*- coding: utf-8 -*-
"""声学对齐引擎 v1.4.0：双引擎混合流水线 + 无逐字时的 numpy 行级合成；译文行不写 words。

v1.4.0 架构（收编 Work\\任务 执行件方案 4，经交叉评审修订）：
  - DualEngineHybridAligner：Demucs 人声 → 行级提示窗 Stable-Whisper Token 对齐 →
    44.1kHz STFT 谱流单字拆分/瞬态吸附 → 25ms/5ms RMS 换气谷底双向锁定 →
    sustain 衰减门控（60ms 静默判定）→ 双声回音覆盖（行间 +50ms 硬门禁，铁律 19）。
  - AcousticAligner：向后兼容 Facade（align_lyrics 类方法签名不变）。
  - 铁律 20 严进宽出：进入声学路径必须 GPU + stable_whisper（缺失抛
    AcousticEnvironmentError，严禁伪逐字）；用户拒绝 GPU 时才走保留的
    numpy 行级合成（synthesized_words=True，产物显式标注）。

依赖纪律（A1）：torch / torchaudio / stable_whisper 一律函数内懒加载，
模块顶层仅 numpy 允许 try-guard 导入——CI 与无 GPU 机器可安全 import 本模块。
"""
from __future__ import annotations

import os
import re
from typing import List, Dict, Any, Optional, Tuple

try:
    import numpy as np
except ImportError:  # C-01: numpy 只在声学/合成路径需要，缺失时不让抓词链路崩
    np = None

from .utils.text_utils import CJK_RE, is_source_or_credit_line

# ffmpeg 桥：声学环境自带 imageio-ffmpeg 二进制，自动挂入 PATH（供 Demucs/Torch 使用）
try:
    import imageio_ffmpeg
    _ff_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    if _ff_dir and _ff_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ff_dir + os.pathsep + os.environ.get("PATH", "")
except (ImportError, Exception):
    pass

# ==========================================
# 0. 声学环境硬门禁（铁律 20，严进）
# ==========================================


class AcousticEnvironmentError(Exception):
    """声学对齐环境不达标（缺 torch / 无 CUDA / 缺 stable_whisper）。

    抛出即阻断，绝不回退伪逐字；降级由调度层的用户决策分支负责（宽出）。
    """


def _check_acoustic_environment(device: str = "cuda"):
    """声学硬门禁：返回 (torch, stable_whisper) 模块；不达标抛 AcousticEnvironmentError。"""
    try:
        import torch
    except ImportError as exc:
        raise AcousticEnvironmentError(
            f"声学打轴需要 PyTorch（未安装：{exc}）。"
            f"请使用声学环境解释器（Work\\AI_Audio_Env\\Scripts\\python.exe）；"
            f"严禁降级为伪逐字——如需行级合成请走 --non-interactive / 拒绝 GPU 分支。"
        ) from exc
    if device == "cuda" and not torch.cuda.is_available():
        raise AcousticEnvironmentError(
            "声学打轴要求可用 GPU（CUDA），当前 torch.cuda.is_available()=False。"
            "拒绝生成伪逐字；如需继续请在交互中拒绝 GPU 或使用 --non-interactive"
            "（产物将标注 synthesized_words=True 的行级合成）。"
        )
    try:
        import stable_whisper
    except ImportError as exc:
        raise AcousticEnvironmentError(f"声学打轴需要 stable-whisper（未安装：{exc}）。") from exc
    return torch, stable_whisper


# ==========================================
# 1. 向后兼容：轻量级音素加权打轴模块（用户拒绝 GPU 时的合法降级，铁律 11）
# ==========================================


def split_units(text: str) -> List[str]:
    """
    Splits text into singing units (characters for CJK, words for Western).
    """
    units = []
    current_word = []

    for ch in text:
        if CJK_RE.match(ch):
            if current_word:
                units.append("".join(current_word))
                current_word = []
            units.append(ch)
        elif ch in " \t\r\n":
            if current_word:
                units.append("".join(current_word))
                current_word = []
            units.append(ch)
        elif ch in ",.!?;:，。！？；：":
            if current_word:
                units.append("".join(current_word))
                current_word = []
            units.append(ch)
        else:
            current_word.append(ch)

    if current_word:
        units.append("".join(current_word))

    return [u for u in units if u]


def align_line_to_words(line_text: str, start_ms: int, duration_ms: int) -> List[Dict[str, Any]]:
    """
    Synthesizes word/syllable level timestamps for a line using phoneme weights.
    （铁律 11 降级路径：仅用于用户拒绝 GPU / 非交互场景，产物强制 synthesized_words=True）
    """
    units = split_units(line_text)
    if not units:
        return []

    weights = []
    for i, u in enumerate(units):
        w = 1.0
        if u in " \t":
            w = 0.2
        elif u in ",.!?;:，。！？；：":
            w = 0.5
        elif CJK_RE.match(u):
            if i == len(units) - 1 or (i < len(units) - 1 and units[i+1] in ",.!?;:，。！？；："):
                w = 1.8  # Sustained outro syllable
            else:
                w = 1.0
        else:
            w = max(0.8, min(2.5, len(u) * 0.35))
            if i == len(units) - 1:
                w *= 1.5
        weights.append(w)

    total_weight = sum(weights) or 1.0

    words = []
    current_ms = start_ms
    for i, u in enumerate(units):
        allocated_dur = int((weights[i] / total_weight) * duration_ms)
        allocated_dur = max(60, allocated_dur)
        w_end = current_ms + allocated_dur

        words.append({
            "start": current_ms,
            "duration": allocated_dur,
            "end": w_end,
            "text": u
        })
        current_ms = w_end

    return words


def enrich_track_with_acoustic_words(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Traverses lines and enriches any line missing word-level timing with synthesized words.

    X-21: 来源标注/纯符号行不参与合成 —— 把 "LRC：xxx[https://lrcshare.com]" 这种
    按字符均分出来的伪逐字，会污染整条时间轴。
    """
    lines = data.get("lines", [])
    for line in lines:
        if not line.get("words") and line.get("text"):
            text = line["text"]
            # 来源/署名行不参与合成。译文行应由 validator 先剥离到 translation；
            # 这里不能再用 is_translation_line，否则华语原文（全是汉字）会被整行跳过。
            if is_source_or_credit_line(text):
                continue
            start = line.get("start", 0)
            duration = line.get("duration", 3000)
            line["words"] = align_line_to_words(text, start, duration)
            if line["words"]:
                line["end"] = line["words"][-1]["end"]
    return data


# ==========================================
# 2. 声学 DSP 原语（执行件方案 4 移植，torch 懒加载）
# ==========================================

FLUX_WIN = 512
FLUX_HOP = 128


def compute_spectral_flux(chunk: "np.ndarray", sr: int) -> Tuple["np.ndarray", "np.ndarray"]:
    """44.1kHz STFT 半波整流谱流 ODF。

    v1.4.0 修正（B6/R 组）：center=False 消除半窗（256/44100≈5.8ms）时基漂移；
    加权核 w(k)=k 标准线性频率积分，聚焦摩擦辅音与塞音爆破瞬态。
    返回 (times, flux)，times 相对 chunk 起点、单位秒。
    """
    import torch
    sig_t = torch.from_numpy(np.ascontiguousarray(chunk, dtype=np.float32))
    window = torch.hann_window(FLUX_WIN)
    stft = torch.stft(sig_t, n_fft=FLUX_WIN, hop_length=FLUX_HOP, win_length=FLUX_WIN,
                      window=window, center=False, return_complex=True)
    mag = torch.abs(stft)
    diff = mag[:, 1:] - mag[:, :-1]
    pos_diff = torch.clamp(diff, min=0)
    k = torch.arange(1, pos_diff.shape[0] + 1, dtype=pos_diff.dtype).unsqueeze(1)
    flux = torch.sum(pos_diff * k, dim=0).cpu().numpy()
    times = (np.arange(len(flux)) + 1) * (FLUX_HOP / sr)
    return times, flux


def spectral_flux_split_time(chunk: "np.ndarray", sr: int) -> Optional[float]:
    """双字 token 的谱流权威切分点（执行件移植）：在 [0.2d, 0.8d] 内取谱流峰；
    chunk 过短或无有效峰时返回 None（调用方回退中点）。返回值相对 chunk 起点。"""
    if np is None or len(chunk) < FLUX_WIN + 1:
        return None
    times, flux = compute_spectral_flux(chunk, sr)
    dur = len(chunk) / sr
    valid = (times >= 0.20 * dur) & (times <= 0.80 * dur)
    if not np.any(valid):
        return None
    sub_t = times[valid]
    sub_f = flux[valid]
    return float(sub_t[int(np.argmax(sub_f))])


def snap_to_flux_peak(times: "np.ndarray", flux: "np.ndarray",
                      t_center: float, window_s: float = 0.060) -> Optional[float]:
    """单字 token 的 ±60ms 瞬态吸附（C2）：返回窗内谱流峰时刻（相对 chunk 起点），
    无峰返回 None。"""
    if times is None or len(times) == 0:
        return None
    lo, hi = t_center - window_s, t_center + window_s
    valid = (times >= lo) & (times <= hi)
    if not np.any(valid):
        return None
    sub_t = times[valid]
    sub_f = flux[valid]
    peak = float(sub_t[int(np.argmax(sub_f))])
    return peak


def find_rms_onset(v_mono: "np.ndarray", sr: int,
                   t_search_st: float, t_search_ed: float, thresh: float = 0.025) -> float:
    """25ms 窗 / 5ms 步进的首个发声起振点（执行件移植）。找不到返回窗口起点。"""
    idx_st = max(0, int(t_search_st * sr))
    idx_ed = min(len(v_mono), int(t_search_ed * sr))
    sub = v_mono[idx_st:idx_ed]
    win = int(0.025 * sr)
    hop = int(0.005 * sr)
    for i in range(0, max(0, len(sub) - win), hop):
        c = sub[i:i + win]
        r = float(np.sqrt(np.mean(c ** 2)))
        if r >= thresh:
            return t_search_st + i / sr
    return t_search_st


def find_rms_offset(v_mono: "np.ndarray", sr: int,
                    t_search_st: float, t_search_max: float,
                    thresh: float = 0.020, min_silent_ms: int = 60) -> float:
    """sustain/衰减门控（执行件移植，铁律 12 口径 0.015 不受影响——本函数服务
    行间释放场景，门限 0.020 见 R1 作用域划分）：自起点向后追踪最后活跃点，
    连续静默 ≥min_silent_ms 即收束。"""
    idx_st = max(0, int(t_search_st * sr))
    idx_ed = min(len(v_mono), int(t_search_max * sr))
    sub = v_mono[idx_st:idx_ed]
    win = int(0.025 * sr)
    hop = int(0.005 * sr)

    last_active = t_search_st
    silent_duration = 0.0
    for i in range(0, max(0, len(sub) - win), hop):
        t_curr = t_search_st + i / sr
        c = sub[i:i + win]
        r = float(np.sqrt(np.mean(c ** 2)))
        if r >= thresh:
            last_active = t_curr + win / sr
            silent_duration = 0.0
        else:
            silent_duration += hop / sr
            if silent_duration >= (min_silent_ms / 1000.0) and last_active > t_search_st:
                break
    return min(t_search_max, max(t_search_st + 0.1, last_active))


def compute_rms_timeline(v_mono: "np.ndarray", sr: int,
                         win_s: float = 0.025, hop_s: float = 0.005) -> Tuple["np.ndarray", "np.ndarray"]:
    """全曲短时 RMS 包络（25ms 窗 / 5ms 步进，向量化）。返回 (hop_times, hop_rms)。"""
    win = int(win_s * sr)
    hop = int(hop_s * sr)
    n = (len(v_mono) - win) // hop
    if n <= 0:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(v_mono[:n * hop + win], win)[::hop]
    rms = np.sqrt(np.mean(frames ** 2, axis=-1))
    times = np.arange(n) * (hop / sr)
    return times.astype(np.float64), rms.astype(np.float32)


def resolve_inter_phrase_boundaries(hop_times: "np.ndarray", hop_rms: "np.ndarray",
                                    raw_end_prev: float, raw_start_next: float,
                                    min_gap_ms: int = 50) -> Tuple[float, float]:
    """行间换气静音谷底双向锁定（v1.4.0 算法化，R1：0.020 门限仅作用于此函数）。

    1. 动态搜索窗口: [raw_end_prev - 0.40s, raw_start_next + 0.30s]
    2. 谷底定位: t_valley = argmin(RMS)
    3. 释放回溯: 自谷底向左至 RMS >= max(0.020, min_rms * 2.0)，+15ms 尾释放
    4. 起振前探: 自谷底向右至 RMS >= max(0.020, min_rms * 2.0)
    5. 硬约束保底: next_start >= prev_end + min_gap_ms（不足则中点对半）
    返回 (t_prev_end, t_next_start)。
    """
    if len(hop_times) < 2:
        return raw_end_prev, raw_start_next
    step = float(hop_times[1] - hop_times[0])
    total_len = float(hop_times[-1])

    t_search_st = max(0.0, raw_end_prev - 0.40)
    t_search_ed = min(total_len, raw_start_next + 0.30)
    i0 = int(t_search_st / step)
    i1 = min(len(hop_rms), int(t_search_ed / step) + 1)
    if i1 <= i0:
        return raw_end_prev, raw_start_next

    window = hop_rms[i0:i1]
    min_i = int(np.argmin(window))
    min_rms = float(window[min_i])

    thresh = max(0.020, min_rms * 2.0)

    j = i0 + min_i
    while j > i0 and hop_rms[j] < thresh:
        j -= 1
    t_prev_end = float(hop_times[j]) + 0.015

    k = i0 + min_i
    while k < i1 - 1 and hop_rms[k] < thresh:
        k += 1
    t_next_start = float(hop_times[k])

    min_gap = min_gap_ms / 1000.0
    if t_next_start - t_prev_end < min_gap:
        mid = (t_prev_end + t_next_start) / 2
        t_prev_end = mid - min_gap / 2
        t_next_start = mid + min_gap / 2
    return t_prev_end, t_next_start


# ==========================================
# 3. v1.4.0 核心：DualEngineHybridAligner 双引擎混合流水线
# ==========================================

_ONSET_THRESH = 0.025          # 发声起振（执行件口径）
_RELEASE_THRESH = 0.020        # 行间释放（R1：仅谷底/释放场景）
_MIN_SILENT_MS = 60            # sustain 收束静默
_MIN_CHAR_S = 0.060            # 行内字间最小推进（执行件口径）
_MIN_WORD_MS = 100             # 单字下限（铁律 4）
_LINE_GAP_MS = 50              # 行间零重叠间隙（铁律 19）
_HINT_PAD_BEFORE = 1.5         # 提示窗前向余量（v3 实证）
_HINT_PAD_AFTER = 0.5          # 提示窗后向余量
_NO_HINT_WIN_S = 14.0          # 无提示顺序游标单窗上限
_NO_HINT_ADVANCE_S = 2.0       # 无提示失败前移步长
_NO_HINT_MAX_ADV = 12          # 无提示最多前移次数


class DualEngineHybridAligner:
    """方案 4 双引擎混合声学逐字流水线（v1.4.0 原生收编）。

    【绝对红线】仅基于权威歌词文本做声学对齐，严禁无词语音听写（铁律 11/20）。
    懒加载：whisper 权重仅在首次真实对齐时初始化（@property，CI/轻量场景零加载）。
    """

    _instance: Optional["DualEngineHybridAligner"] = None

    def __init__(self, device: str = "cuda"):
        self._device = device
        self._whisper_model = None

    @classmethod
    def get_instance(cls, device: str = "cuda") -> "DualEngineHybridAligner":
        if cls._instance is None:
            cls._instance = cls(device=device)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @property
    def whisper_model(self):
        if self._whisper_model is None:
            import stable_whisper
            self._whisper_model = stable_whisper.load_model("base", device=self._device)
        return self._whisper_model

    # ------------------------------------------------------------------
    def align_pipeline(self, audio_path: str, lyrics_text: str,
                       line_timing_hints: Optional[List[Tuple[float, str]]] = None,
                       lang: str = "auto") -> Dict[str, Any]:
        """主入口：权威文本（+可选行级提示）→ 逐字时间轴。毫秒整数交付。"""
        import torch  # noqa: F401  懒加载守卫（_check_acoustic_environment 已验证）
        import torchaudio

        if not lyrics_text or not lyrics_text.strip():
            raise ValueError("AcousticAligner 绝对红线拦截：必须提供权威逐行歌词文本，严禁无词 ASR 语音听写！")

        # 1. 文本清洗与行切分（沿用既有口径：剥元数据行，识别内联 [mm:ss.xx] 行提示）
        raw_lines = [l.strip() for l in lyrics_text.strip().splitlines() if l.strip()]
        clean_lines: List[str] = []
        parsed_hints: List[Tuple[float, str]] = []
        for l in raw_lines:
            m = re.match(r"^\[(\d{1,3}):(\d{1,2}(?:\.\d{1,3})?)\](.*)$", l)
            if m:
                sec = int(m.group(1)) * 60 + float(m.group(2))
                txt = m.group(3).strip()
                if txt and not any(txt.startswith(p) for p in ("ti:", "ar:", "al:", "by:", "offset:")):
                    clean_lines.append(txt)
                    parsed_hints.append((sec, txt))
            elif not l.startswith("[") and not l.startswith("<"):
                clean_lines.append(l)
        if not clean_lines:
            raise ValueError("AcousticAligner 绝对红线拦截：未能从输入文本中解析出有效的歌词唱词！")

        hints: Optional[List[Tuple[float, str]]] = None
        if line_timing_hints:
            filtered_hints = [(float(t), str(x)) for t, x in line_timing_hints if str(x).strip() and not is_source_or_credit_line(str(x))]
            if len(filtered_hints) == len(clean_lines):
                hints = filtered_hints
            else:
                hints = [(float(t), str(x)) for t, x in line_timing_hints]
        elif len(parsed_hints) == len(clean_lines):
            hints = parsed_hints

        # 语种分支裁决 (v1.5.0)
        route_lang = (lang or "auto").lower().strip()
        if route_lang == "auto":
            from .utils.lang_detect import detect_language
            det = detect_language(
                lyrics_text=lyrics_text,
                audio_path=audio_path,
                interactive=False,
                device=self._device,
            )
            route_lang = det.get("lang", "zh")

        mode = "hint" if hints else "cursor"
        lang_label = "粤语 (yue)" if route_lang == "yue" else "普通话 (zh)"
        print(f"🎤 [DualEngine] 双引擎流水线启动：{len(clean_lines)} 行，模式={mode}，语种={lang_label}"
              + ("（行级提示窗锚定）" if hints else "（顺序游标，无行级提示）"))

        # 2. 粤语分支调度 (Cantonese CTC Aligner)
        if route_lang == "yue":
            v_mono, sr = AcousticAligner.extract_vocals_mono(audio_path, device=self._device)
            # Demucs 分离完成，立即释放 Demucs 显存（A2 显存纪律：两模型严禁同驻显存）
            AcousticAligner.release_models()
            t16 = torch.from_numpy(v_mono.astype(np.float32)).unsqueeze(0)
            v16 = torchaudio.functional.resample(t16, sr, 16000).squeeze(0).numpy()
            from .acoustic_canto import CantoneseCTCAligner
            canto_aligner = CantoneseCTCAligner.get_instance(device=self._device)
            return canto_aligner.align_cantonese_lines(
                audio_16k=v16,
                clean_lines=clean_lines,
                hints=hints,
                v_mono_44k=v_mono,
                sr_44k=sr,
            )

        # 3. 普通话分支调度 (Whisper Token Alignment)
        v_mono, sr = AcousticAligner.extract_vocals_mono(audio_path, device=self._device)
        AcousticAligner.release_models()
        total_len = len(v_mono) / sr
        t16 = torch.from_numpy(v_mono.astype(np.float32)).unsqueeze(0)
        v16 = torchaudio.functional.resample(t16, sr, 16000).squeeze(0).numpy()
        wmodel = self.whisper_model

        # 3. 行级切片由抗咬窗梯在逐行循环内构建（hint 锚定 / cursor 游标）
        cursor = 0.0

        # 4. 逐行 Whisper Token 对齐（v3 实证宽窗：[hint-1.5, next+0.5]；
        #    起点精度交给 step 6 的 find_onset_backward 相对阈值反向扫描——对咬窗免疫）
        raw_lines_data: List[Dict[str, Any]] = []
        cursor = 0.0
        for i, text in enumerate(clean_lines):
            n_chars = len(text.replace(" ", ""))
            if hints:
                hint = hints[i][0]
                next_hint = hints[i + 1][0] if i + 1 < len(hints) else hint + 8.0
                ladder = [(max(0.0, hint - _HINT_PAD_BEFORE), min(total_len, next_hint + _HINT_PAD_AFTER))]
            else:
                base = min(_NO_HINT_WIN_S, max(4.0, n_chars * 0.55 + 3.0))
                ladder = [(min(cursor, max(0.0, total_len - 1.0)), min(total_len, cursor + base))]

            got: Optional[Tuple[float, List[Dict[str, float]]]] = None
            for w_st, w_ed in ladder:
                if w_ed - w_st < 1.0:
                    continue
                seg = v16[int(w_st * 16000):int(w_ed * 16000)]
                try:
                    res = wmodel.align(seg, text, language="zh")
                except Exception:
                    continue
                words: List[Dict[str, float]] = []
                for s in (res.segments or []):
                    for w in (s.words or []):
                        wt = (w.word or "").strip()
                        if wt:
                            words.append({"t": wt, "s": float(w.start), "e": float(w.end)})
                cov = sum(len(w["t"]) for w in words) / max(1, n_chars) if words else 0.0
                if words and cov >= 0.6 and words[-1]["e"] > words[0]["s"]:
                    got = (w_st, words)
                    break
            if got is None:
                print(f"   ⚠️ [DualEngine] 行 {i+1} 未对齐（保留行级）：{text}")
                hint_t = hints[i][0] if hints else cursor
                raw_lines_data.append({
                    "text": text, "words": [],
                    "raw_start": hint_t, "raw_end": min(total_len, hint_t + max(2.0, n_chars * 0.4)),
                })
                if mode == "cursor":
                    cursor = min(total_len, hint_t + max(2.0, n_chars * 0.4))
                continue
            w_st, words = got
            raw_start = w_st + words[0]["s"]
            raw_end = w_st + words[-1]["e"]
            raw_lines_data.append({"text": text, "words": words, "raw_start": raw_start,
                                   "raw_end": raw_end, "_slice_st": w_st})
            if mode == "cursor":
                cursor = min(total_len, raw_end + 0.12)
            print(f"   [{i+1:02d}/{len(clean_lines)}] ✓ {raw_start:7.2f}s → {raw_end:7.2f}s {text}")

        # 4.5 提示 sanitize（hint 模式）：行级提示是同表演真值（±0.3s 级精度），
        # 对 whisper 原始起止做有界纠偏——防咬窗/漂移值污染谷底搜索窗
        # （执行件 test_valley_alignment 隐性依赖干净 raw 值，此处显式化）。
        if hints:
            for i, item in enumerate(raw_lines_data):
                hint = hints[i][0]
                next_hint = hints[i + 1][0] if i + 1 < len(hints) else hint + 8.0
                rs = hint + min(max(item["raw_start"] - hint, -0.8), 0.5)
                re_ = min(max(item["raw_end"], rs + 0.2), next_hint - 0.05)
                item["raw_start"], item["raw_end"] = rs, max(rs + 0.2, re_)

        # 5. RMS 包络 + 行间谷底双向锁定（铁律 19：end(i)+50ms ≤ start(i+1)）
        hop_times, hop_rms = compute_rms_timeline(v_mono, sr)
        n = len(raw_lines_data)
        bounds: List[Tuple[float, float]] = []
        # 首行起唱：RMS 起振扫描（执行件口径 ±0.4s）
        first_raw = raw_lines_data[0]["raw_start"]
        if len(hop_times):
            st_i = int(max(0.0, first_raw - 0.40) / 0.005)
            ed_i = int(min(total_len, first_raw + 0.40) / 0.005)
            seg = hop_rms[st_i:ed_i]
            act = np.where(seg >= _ONSET_THRESH)[0]
            first_start = float(hop_times[st_i + act[0]]) if len(act) else first_raw
        else:
            first_start = first_raw
        cur_start = first_start
        for i in range(n - 1):
            # 谷底搜索窗：hint 模式锚定在下一行提示（呼吸谷底物理上就在下一行开嗓前），
            # 彻底与 whisper 原始值漂移解耦；桥段/和声回音行的早于提示起唱天然覆盖。
            if hints:
                hn = hints[i + 1][0]
                win_end_prev = max(0.0, hn - 1.10)
                win_start_next = min(total_len, hn + 0.25)
            else:
                win_end_prev = raw_lines_data[i]["raw_end"]
                win_start_next = raw_lines_data[i + 1]["raw_start"]
            cal_end, cal_start = resolve_inter_phrase_boundaries(
                hop_times, hop_rms, win_end_prev, win_start_next, min_gap_ms=_LINE_GAP_MS)
            if cal_end <= cur_start + 0.2:
                cal_end = cur_start + max(0.4, len(raw_lines_data[i]["text"]) * 0.15)
                if cal_start < cal_end + _LINE_GAP_MS / 1000.0:
                    cal_start = cal_end + _LINE_GAP_MS / 1000.0
            bounds.append((cur_start, cal_end))
            cur_start = cal_start
        bounds.append((cur_start, min(total_len - 0.05, raw_lines_data[-1]["raw_end"] + 1.0)))

        # 6. 字级精修：谱流拆分/吸附 + onset 校准 + 行内单调 + sustain 门控
        output_lines: List[Dict[str, Any]] = []
        for i, item in enumerate(raw_lines_data):
            text = item["text"]
            line_st_bound, line_ed_bound = bounds[i]
            words_raw = item["words"]
            if not words_raw:
                st_ms = int(line_st_bound * 1000)
                ed_ms = int(line_ed_bound * 1000)
                output_lines.append({"start": st_ms, "end": ed_ms,
                                     "duration": max(300, ed_ms - st_ms),
                                     "text": text, "words": []})
                continue

            # 6.1 字展开：1 字原样；2 字谱流权威切分；≥3 字均分（执行件口径）
            #     raw words 的 s/e 为切片相对时间，_slice_st 换算为绝对时间
            chars = self._expand_chars(item, v_mono, sr)

            # 6.2 字时间钳入行界。权威顺序（v1.4.0 定案）：谷底物理边界 > whisper。
            #     谷底窗已提示锚定（物理有界，无级联风险），行起点完全交由谷底行使——
            #     和声回音行早于提示网格起唱（golden 实测提前 0.5~0.9s）由谷底自然表达。
            lo = line_st_bound
            hi_end = line_ed_bound
            if hints:
                hint_i = hints[i][0]
                hint_next = hints[i + 1][0] if i + 1 < len(hints) else total_len
                hi_end = min(hi_end, hint_next - _LINE_GAP_MS / 1000.0)
            hi = max(lo + 0.15, hi_end - 0.10)
            for c in chars:
                c["start"] = min(max(c["start"], lo), hi)

            # 6.3 首字物理起振精修（v3 实证内核）：find_onset_backward 相对阈值
            #     （本地歌唱能量 20%）反向扫描——对 whisper 咬窗与回音尾双重免疫；
            #     结果钳入 [lo, hint+0.35]（hint 模式）防漂移级联。
            prev_end_s = bounds[i - 1][1] if i > 0 else 0.0
            cal_st = AcousticAligner.find_onset_backward(
                v_mono, sr, chars[0]["end"], chars[0]["start"],
                prev_line_end_s=prev_end_s)
            if hints:
                cal_st = min(max(cal_st, hints[i][0] - 1.2), hints[i][0] + 0.35)
                cal_st = max(cal_st, lo)
            else:
                cal_st = max(cal_st, lo)
            chars[0]["start"] = cal_st

            # 6.4 行内单调推进（60ms 最小字距；字 end = 下一字 start）
            for ci in range(len(chars) - 1):
                if chars[ci + 1]["start"] < chars[ci]["start"] + _MIN_CHAR_S:
                    chars[ci + 1]["start"] = chars[ci]["start"] + _MIN_CHAR_S
                chars[ci]["end"] = chars[ci + 1]["start"]

            # 6.5 末字 sustain/衰减门控（R1：0.020；铁律 19/回音覆盖：tail ≤ next_start − 50ms）
            max_tail = max(hi_end, chars[-1]["start"] + _MIN_WORD_MS / 1000.0)
            actual_offset = find_rms_offset(
                v_mono, sr, max(chars[-1]["start"] + 0.1, min(item["raw_end"], hi_end) - 0.1),
                max(max_tail, chars[-1]["start"] + 0.15),
                thresh=_RELEASE_THRESH, min_silent_ms=_MIN_SILENT_MS)
            chars[-1]["end"] = max(chars[-1]["start"] + _MIN_WORD_MS / 1000.0,
                                   min(max_tail, actual_offset))

            line_start = chars[0]["start"]
            line_end = chars[-1]["end"]
            output_lines.append({
                "start": int(line_start * 1000),
                "end": int(line_end * 1000),
                "duration": max(_MIN_WORD_MS, int((line_end - line_start) * 1000)),
                "text": text,
                "words": [{
                    "text": c["char"],
                    "start": int(c["start"] * 1000),
                    "end": int(c["end"] * 1000),
                    "duration": max(_MIN_WORD_MS // 2, int((c["end"] - c["start"]) * 1000)),
                } for c in chars],
            })

        # 7. 终局钳制：行间零重叠（铁律 19）——绝对间隙优先，不设任何制造重叠的地板
        output_lines.sort(key=lambda l: l["start"])
        for i in range(len(output_lines)):
            lim = output_lines[i + 1]["start"] - _LINE_GAP_MS if i + 1 < len(output_lines) \
                else output_lines[i]["end"]
            ln = output_lines[i]
            if ln["end"] > lim:
                ln["end"] = lim
                ln["duration"] = max(100, ln["end"] - ln["start"])
            ws = ln.get("words") or []
            if ws and ws[-1]["end"] > lim:
                s0 = min(ws[0]["start"], max(lim - 100, 0))
                e0 = ws[-1]["end"]
                span = max(1, e0 - s0)
                target = max(1, lim - s0)
                for w in ws:
                    w["start"] = s0 + (w["start"] - s0) * target // span
                    w["end"] = min(lim, s0 + (w["end"] - s0) * target // span)
                    w["duration"] = max(_MIN_WORD_MS // 2, w["end"] - w["start"])

        print(f"✅ [DualEngine] 声学咬合完成：{len(output_lines)} 行逐字"
              f"（模式={mode}，谷底双向锁定，行间 ≥{_LINE_GAP_MS}ms）")
        return {
            "source": "demucs_gpu_alignment",
            "lyric_type": "verbatim",
            "synthesized_words": False,
            "time_precision": 3,
            "lines": output_lines,
        }

    # ------------------------------------------------------------------
    def _expand_chars(self, item: Dict[str, Any], v_mono: "np.ndarray", sr: int) -> List[Dict[str, Any]]:
        """把 whisper 原始 token 展开为字级（执行件移植；2 字 token 用谱流权威切分）。

        item 需含 _slice_st（切片绝对起点）与 words（切片相对时间）。
        """
        slice_st = item.get("_slice_st", 0.0)
        chars: List[Dict[str, Any]] = []
        for w in item["words"]:
            wt = w["t"]
            w_st = slice_st + w["s"]
            w_ed = slice_st + w["e"]
            dur = max(0.05, w_ed - w_st)
            if len(wt) == 1:
                chars.append({"char": wt, "start": w_st, "end": w_ed})
            elif len(wt) == 2:
                i_st = int(w_st * sr)
                i_ed = int(w_ed * sr)
                split_rel = spectral_flux_split_time(v_mono[i_st:i_ed], sr)
                split_t = w_st + split_rel if split_rel is not None else w_st + dur / 2
                chars.append({"char": wt[0], "start": w_st, "end": split_t})
                chars.append({"char": wt[1], "start": split_t, "end": w_ed})
            else:
                step = dur / len(wt)
                for k, c in enumerate(wt):
                    chars.append({"char": c, "start": w_st + k * step, "end": w_st + (k + 1) * step})
        # 文本完整性自检（执行件同款 assert 语义，软化为告警防整轮报废）
        joined = "".join(c["char"] for c in chars)
        if joined != item["text"]:
            import logging
            logging.getLogger(__name__).warning(
                "DualEngine 字序列与行文本不一致（%r vs %r），保留 whisper 原始序列", joined, item["text"])
        return chars


# ==========================================
# 4. 向后兼容 Facade（A2：签名不变，process_song 无感升级）
# ==========================================


class AcousticAligner:
    """Public Facade maintaining 100% backward compatibility（v1.4.0）。

    GPU 打轴由 DualEngineHybridAligner 承载；本类保留既有静态 DSP 方法供
    外部脚本与测试使用。进入 align_lyrics 即受铁律 20 硬门禁约束。
    """
    _demucs_model = None
    _whisper_model = None

    @classmethod
    def get_models(cls, device: str = 'cuda'):
        """Lazy-loads and caches Demucs and Stable-Whisper models."""
        # C-03: 只查通用位置，不再写死开发机路径；找不到就明确报错，不静默改全局 PATH
        import shutil
        if not shutil.which("ffmpeg"):
            for cand_dir in [
                os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
                r"C:\ProgramData\chocolatey\bin",
                r"C:\ffmpeg\bin",
            ]:
                if os.path.isdir(cand_dir) and os.path.isfile(os.path.join(cand_dir, "ffmpeg.exe")):
                    os.environ["PATH"] = cand_dir + os.pathsep + os.environ.get("PATH", "")
                    break
            else:
                print("⚠️ 未找到 ffmpeg（Demucs 声学打轴需要）。请安装后重试，或使用非 GPU 路径。")

        try:
            import torch
            import demucs.pretrained
            import stable_whisper
        except ImportError as e:
            raise ImportError(
                f"AcousticAligner 需要 PyTorch, Demucs 与 Stable-Whisper 环境支持: {e}。\n"
                f"请确保运行在配置有 AI 音频环境的 Python 中。"
            )

        if cls._demucs_model is None:
            if not torch.cuda.is_available() and device == 'cuda':
                device = 'cpu'
            cls._demucs_model = demucs.pretrained.get_model('htdemucs').to(device)
            cls._vocal_idx = cls._demucs_model.sources.index('vocals')

        if cls._whisper_model is None:
            if not torch.cuda.is_available() and device == 'cuda':
                device = 'cpu'
            cls._whisper_model = stable_whisper.load_model('base', device=device)

        return cls._demucs_model, cls._whisper_model, device

    @classmethod
    def release_models(cls) -> None:
        """释放缓存的 Demucs 与 Whisper 权重，严格守卫显存上限。"""
        import torch
        if cls._demucs_model is not None:
            del cls._demucs_model
            cls._demucs_model = None
        if cls._whisper_model is not None:
            del cls._whisper_model
            cls._whisper_model = None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    @classmethod
    def extract_vocals_mono(cls, audio_path: str, device: str = 'cuda') -> Tuple["np.ndarray", int]:
        """
        Uses Demucs v4 (htdemucs) to extract pure vocals from audio file.
        Returns (vocals_mono_array, sample_rate).
        """
        import soundfile as sf
        import torch
        import torchaudio
        from demucs.apply import apply_model

        demucs_model, _, device = cls.get_models(device)
        wav, sr = sf.read(audio_path, dtype='float32')
        if len(wav.shape) == 1:
            wav = np.repeat(wav[:, None], 2, axis=1)
        elif wav.shape[1] == 1:
            wav = np.repeat(wav, 2, axis=1)

        wav_t = torch.from_numpy(wav).t().unsqueeze(0)
        target_sr = getattr(demucs_model, 'samplerate', 44100)
        if sr != target_sr:
            wav_t = torchaudio.functional.resample(wav_t, sr, target_sr)
            sr = target_sr

        with torch.no_grad():
            out = apply_model(demucs_model, wav_t, device=device, split=True, segment=7.8, progress=False)
        vocals = out[0, cls._vocal_idx, :, :].t().cpu().numpy()
        v_mono = np.mean(vocals, axis=1)
        return v_mono, sr

    @classmethod
    def find_onset_backward(
        cls,
        v_mono: "np.ndarray",
        sr: int,
        word_end_s: float,
        orig_start_s: float,
        prev_line_end_s: float = 0.0
    ) -> float:
        """
        Line-Head Onset Gate: Scans backward in vocals.mono with 15ms step to pinpoint true acoustic entry.
        """
        search_start = max(orig_start_s - 1.2, prev_line_end_s + 0.05, 0.0)
        search_end = min(word_end_s, orig_start_s + 0.3)
        if search_start >= search_end:
            return orig_start_s

        st_idx = int(search_start * sr)
        ed_idx = int(search_end * sr)
        audio_slice = v_mono[st_idx:ed_idx]

        win_len = int(0.03 * sr)
        step_len = int(0.015 * sr)

        rms_list, time_list = [], []
        for cur_idx in range(0, len(audio_slice) - win_len, step_len):
            chunk = audio_slice[cur_idx:cur_idx + win_len]
            rms = np.sqrt(np.mean(chunk**2))
            t = search_start + cur_idx / sr
            rms_list.append(rms)
            time_list.append(t)

        if not rms_list:
            return orig_start_s

        max_rms = max(rms_list)
        thresh = max(0.020, max_rms * 0.20)

        onset_t = orig_start_s
        for t, r in reversed(list(zip(time_list, rms_list))):
            if r < thresh:
                onset_t = t
                break

        return round(max(search_start, min(word_end_s - 0.1, onset_t)), 2)

    @classmethod
    def find_offset_forward(
        cls,
        v_mono: "np.ndarray",
        sr: int,
        last_word_start_s: float,
        orig_end_s: float,
        next_line_start_s: Optional[float] = None
    ) -> float:
        """
        Line-Tail Offset Gate: Scans forward in vocals.mono with 20ms step to detect falling edge (RMS < 0.015).
        （铁律 12 口径：0.015 保持不变——R1 已把 0.020 划归行间谷底场景）
        """
        max_search_end = max(last_word_start_s + 0.35, min(orig_end_s + 0.35, last_word_start_s + 2.5))
        if next_line_start_s:
            max_search_end = min(max_search_end, next_line_start_s - 0.05)

        st_idx = int(last_word_start_s * sr)
        ed_idx = int(max_search_end * sr)
        if st_idx >= ed_idx:
            return round(last_word_start_s + 0.25, 2)

        audio_slice = v_mono[st_idx:ed_idx]
        win_len = int(0.04 * sr)
        step_len = int(0.02 * sr)

        offset_t = orig_end_s
        consecutive_low = 0
        found = False
        for cur_idx in range(0, len(audio_slice) - win_len, step_len):
            chunk = audio_slice[cur_idx:cur_idx + win_len]
            rms = np.sqrt(np.mean(chunk**2))
            t = last_word_start_s + cur_idx / sr
            if rms < 0.015:
                consecutive_low += 1
                if consecutive_low >= 2 and (t - last_word_start_s) >= 0.18:
                    offset_t = t - 0.04
                    found = True
                    break
            else:
                consecutive_low = 0

        if not found:
            offset_t = min(orig_end_s, max_search_end)

        return round(max(last_word_start_s + 0.18, min(max_search_end, offset_t)), 2)

    @classmethod
    def align_lyrics(
        cls,
        audio_path: str,
        lyrics_text: str,
        line_timing_hints: Optional[List[Tuple[float, str]]] = None,
        device: str = 'cuda',
        scratch_dir: Optional[str] = None,
        lang: str = "auto",
    ) -> Dict[str, Any]:
        """
        Executes precision acoustic alignment on valid plain-text lyrics (v1.5.0 双引擎流水线).
        【核心红线】：lyrics_text 严禁为空！禁止无词语音听写！
        （scratch_dir 为 v1.3 兼容形参，新流水线全程内存直馈，不再落临时 wav。）
        """
        if not lyrics_text or not lyrics_text.strip():
            raise ValueError("AcousticAligner 绝对红线拦截：必须提供权威逐行歌词文本，严禁无词 ASR 语音听写！")

        # 1. 铁律 20 声学硬门禁（不达标抛 AcousticEnvironmentError，绝不伪逐字）
        _check_acoustic_environment(device)

        # 2. 调度双引擎混合流水线
        engine = DualEngineHybridAligner.get_instance(device=device)
        return engine.align_pipeline(
            audio_path=audio_path,
            lyrics_text=lyrics_text,
            line_timing_hints=line_timing_hints,
            lang=lang,
        )
