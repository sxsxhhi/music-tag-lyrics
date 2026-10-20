# -*- coding: utf-8 -*-
"""
AcousticAligner: Demucs v4 + Stable-Whisper 声学咬合高精打轴引擎 (v1.2.0)
绝对红线：严禁语音听写，仅在 100% 正确的正版/权威歌词文本基础上执行时间轴咬合。
核心特性：
1. Demucs v4 GPU 干声剥离 (vocals.mono)
2. 混合双轨短窗切分 (LRC 粗轴划窗 / 纯文本声学 VAD 划窗，隔离长间奏)
3. 双端声学能量门禁 (Onset 15ms 逆向回溯 + Offset 20ms 正向跌落截断 RMS < 0.015)
4. 行内停顿空闭合标签 (Pause Gap Tags) 消除行内假滚动
5. 保留原有 split_units, align_line_to_words, enrich_track_with_acoustic_words 以维持100%向后兼容
"""

import os
import sys
import re
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

from .utils.text_utils import CJK_RE

# ==========================================
# 1. 向后兼容：轻量级音素加权打轴模块 (v1.1.3 保留)
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
    """
    lines = data.get("lines", [])
    for line in lines:
        if not line.get("words") and line.get("text"):
            start = line.get("start", 0)
            duration = line.get("duration", 3000)
            line["words"] = align_line_to_words(line["text"], start, duration)
            if line["words"]:
                line["end"] = line["words"][-1]["end"]
    return data

# ==========================================
# 2. v1.2.0 重构核心：AcousticAligner 高精声学打轴引擎
# ==========================================

class AcousticAligner:
    """
    Demucs v4 + Stable-Whisper 高精声学咬合打轴引擎。
    【绝对红线】：严禁无词语音听写，仅在正版权威歌词文本基础上执行声学打轴。
    """
    _demucs_model = None
    _whisper_model = None

    @classmethod
    def get_models(cls, device: str = 'cuda'):
        """Lazy-loads and caches Demucs and Stable-Whisper models."""
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
    def extract_vocals_mono(cls, audio_path: str, device: str = 'cuda') -> Tuple[np.ndarray, int]:
        """
        Uses Demucs v4 (htdemucs) to extract pure vocals from audio file.
        Returns (vocals_mono_array, sample_rate).
        """
        import soundfile as sf
        import torch
        from demucs.apply import apply_model

        demucs_model, _, device = cls.get_models(device)
        wav, sr = sf.read(audio_path, dtype='float32')
        if len(wav.shape) == 1:
            wav = np.repeat(wav[:, None], 2, axis=1)
        elif wav.shape[1] == 1:
            wav = np.repeat(wav, 2, axis=1)
            
        wav_t = torch.from_numpy(wav).t().unsqueeze(0)
        with torch.no_grad():
            out = apply_model(demucs_model, wav_t, device=device, split=True, segment=7.8, progress=False)
        vocals = out[0, cls._vocal_idx, :, :].t().cpu().numpy()
        v_mono = np.mean(vocals, axis=1)
        return v_mono, sr

    @classmethod
    def find_onset_backward(
        cls, 
        v_mono: np.ndarray, 
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
        v_mono: np.ndarray, 
        sr: int, 
        last_word_start_s: float, 
        orig_end_s: float, 
        next_line_start_s: Optional[float] = None
    ) -> float:
        """
        Line-Tail Offset Gate: Scans forward in vocals.mono with 20ms step to detect falling edge (RMS < 0.015).
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
    def build_hybrid_windows(
        cls, 
        v_mono: np.ndarray, 
        sr: int, 
        lyrics_lines: List[str], 
        line_timing_hints: Optional[List[Tuple[float, str]]] = None
    ) -> List[Tuple[float, float, str]]:
        """
        Builds safe local time windows for alignment:
        1. If line_timing_hints (approximate timestamps in seconds) exist, uses them to bound each line.
        2. Otherwise, scans vocals.mono VAD energy to find vocal bursts separated by silence >= 2.5s.
        """
        total_audio_s = len(v_mono) / sr
        windows = []

        if line_timing_hints and len(line_timing_hints) == len(lyrics_lines):
            # Track hints windowing
            for i in range(len(lyrics_lines)):
                hint_t, hint_txt = line_timing_hints[i]
                prev_t = line_timing_hints[i-1][0] if i > 0 else 0.0
                next_t = line_timing_hints[i+1][0] if i < len(line_timing_hints) - 1 else total_audio_s
                
                # Expand window safely around hint
                st = max(0.0, hint_t - min(2.0, (hint_t - prev_t) / 2 if i > 0 else 2.0))
                ed = min(total_audio_s, next_t + 1.0 if (next_t - hint_t) < 8.0 else hint_t + 6.0)
                windows.append((round(st, 2), round(ed, 2), lyrics_lines[i]))
        else:
            # VAD-based slicing or progressive allocation
            # We can scan RMS with 100ms step to detect silence >= 2.5s
            step_s = 0.1
            step_len = int(step_s * sr)
            rms_curve = []
            for idx in range(0, len(v_mono) - step_len, step_len):
                chunk = v_mono[idx:idx + step_len]
                rms_curve.append(np.sqrt(np.mean(chunk**2)))
                
            active_spans = []
            in_active = False
            cur_start = 0.0
            silent_frames = 0
            
            for f_idx, r in enumerate(rms_curve):
                t = f_idx * step_s
                if r >= 0.018:
                    if not in_active:
                        in_active = True
                        cur_start = max(0.0, t - 0.4)
                    silent_frames = 0
                else:
                    if in_active:
                        silent_frames += 1
                        if silent_frames >= 25:  # 2.5s silence
                            in_active = False
                            active_spans.append((cur_start, t - 2.0))
            if in_active:
                active_spans.append((cur_start, total_audio_s))
                
            if not active_spans:
                active_spans = [(0.0, total_audio_s)]
                
            # If active spans roughly match stanza groups, map lines uniformly into spans
            lines_per_span = max(1, len(lyrics_lines) // len(active_spans))
            line_idx = 0
            for sp_st, sp_ed in active_spans:
                sub_lines = lyrics_lines[line_idx:line_idx + lines_per_span]
                if sub_lines:
                    for l in sub_lines:
                        windows.append((sp_st, sp_ed, l))
                    line_idx += len(sub_lines)
            while line_idx < len(lyrics_lines):
                windows.append((active_spans[-1][0], active_spans[-1][1], lyrics_lines[line_idx]))
                line_idx += 1

        return windows

    @classmethod
    def align_lyrics(
        cls, 
        audio_path: str, 
        lyrics_text: str, 
        line_timing_hints: Optional[List[Tuple[float, str]]] = None,
        device: str = 'cuda',
        scratch_dir: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes precision acoustic alignment on valid plain-text lyrics:
        【核心红线】：lyrics_text 严禁为空！禁止无词语音听写！
        """
        if not lyrics_text or not lyrics_text.strip():
            raise ValueError("AcousticAligner 绝对红线拦截：必须提供权威逐行歌词文本，严禁无词 ASR 语音听写！")

        import soundfile as sf
        import tempfile

        demucs_model, whisper_model, device = cls.get_models(device)
        
        # 1. Clean and split input text into singing lines
        raw_lines = [l.strip() for l in lyrics_text.strip().splitlines() if l.strip()]
        # Filter out meta tags like [ti:] [ar:] [00:00.00] from text if present
        clean_lines = []
        parsed_hints = []
        for l in raw_lines:
            # Check for LRC timestamp [mm:ss.xx]
            m = re.match(r"^\[(\d{2}):(\d{2}\.\d{2,3})\](.*)$", l)
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

        hints_to_use = line_timing_hints or (parsed_hints if len(parsed_hints) == len(clean_lines) else None)

        print(f"🎤 [AcousticAligner] 启动纯人声分离与声学咬合打轴 ({len(clean_lines)} 行歌词)...")
        v_mono, sr = cls.extract_vocals_mono(audio_path, device=device)

        # 2. Build local windows
        windows = cls.build_hybrid_windows(v_mono, sr, clean_lines, hints_to_use)

        work_dir = scratch_dir if scratch_dir and os.path.exists(scratch_dir) else tempfile.gettempdir()
        
        # 3. Whisper forced alignment in local windows
        all_line_words = []
        for i, (st, ed, line_txt) in enumerate(windows):
            idx1 = int(st * sr)
            idx2 = int(ed * sr)
            v_slice = v_mono[idx1:idx2]
            tmp_p = os.path.join(work_dir, f"_temp_align_{os.getpid()}_{i}.wav")
            sf.write(tmp_p, v_slice, sr)
            
            try:
                res = whisper_model.align(tmp_p, line_txt, language='zh')
                line_w = []
                for s in res.segments:
                    if s.words:
                        for w in s.words:
                            line_w.append({
                                "text": w.word.strip(),
                                "start": round(w.start + st, 2),
                                "end": round(w.end + st, 2)
                            })
                all_line_words.append((line_txt, line_w))
            finally:
                if os.path.exists(tmp_p):
                    os.remove(tmp_p)

        # 4. Dual-ended energy calibration and pause gap tagging
        output_lines = []
        for i in range(len(all_line_words)):
            raw_line, words = all_line_words[i]
            if not words:
                output_lines.append({
                    "start": 0,
                    "end": 3000,
                    "text": raw_line,
                    "words": []
                })
                continue
                
            prev_end = all_line_words[i-1][1][-1]["end"] if i > 0 and all_line_words[i-1][1] else 0.0
            next_start = all_line_words[i+1][1][0]["start"] if i < len(all_line_words)-1 and all_line_words[i+1][1] else None
            
            # (1) Onset Gate
            first_w = words[0]
            calib_st = cls.find_onset_backward(v_mono, sr, first_w["end"], first_w["start"], prev_end)
            first_w["start"] = calib_st
            
            # (2) Offset Gate
            last_w = words[-1]
            calib_ed = cls.find_offset_forward(v_mono, sr, last_w["start"], last_w["end"], next_start)
            last_w["end"] = calib_ed
            
            # (3) Inter-word pause defense
            for k in range(len(words) - 1):
                w_cur = words[k]
                w_nxt = words[k+1]
                if w_nxt["start"] - w_cur["start"] > 1.0:
                    off = cls.find_offset_forward(v_mono, sr, w_cur["start"], w_cur["end"], w_nxt["start"])
                    w_cur["end"] = off
                    
            # Convert to ms for converter compatibility
            line_start_ms = int(calib_st * 1000)
            line_end_ms = int(calib_ed * 1000)
            words_ms = []
            for w in words:
                words_ms.append({
                    "text": w["text"],
                    "start": int(w["start"] * 1000),
                    "end": int(w["end"] * 1000),
                    "duration": int((w["end"] - w["start"]) * 1000)
                })
                
            output_lines.append({
                "start": line_start_ms,
                "end": line_end_ms,
                "duration": line_end_ms - line_start_ms,
                "text": raw_line,
                "words": words_ms
            })

        print(f"✅ [AcousticAligner] 声学咬合完成，生成 {len(output_lines)} 行高精逐字数据。")
        return {
            "source": "demucs_gpu_alignment",
            "lyric_type": "verbatim",
            "synthesized_words": False,
            "lines": output_lines
        }
