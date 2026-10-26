# -*- coding: utf-8 -*-
"""粤语声学对齐引擎 (Cantonese Acoustic Alignment Engine v1.5.0)。

核心技术路线：
1. 粤拼中转桥（Jyutping Bridge）：统一字表与声学模型投影空间至 729 维无调粤拼类别；
2. torchaudio 原生 forced_align（PyTorch Viterbi DP）GPU 向量化直接字级对齐；
3. 滑窗分块推理（20s 窗 + 2s 重叠）+ 显存纪律（严守 6GB 显存红线）；
4. 非汉字策略：阿拉伯数字转粤拼音节入对齐 target，英文字词启发式区间插值分配；
5. 入声字（-p/-t/-k）闭气容错 sustain 门控（120ms 容错）+ 谱流瞬态吸附 + 铁律 19 零重叠。
"""

from __future__ import annotations

import os
from typing import List, Dict, Any, Optional, Tuple

try:
    import numpy as np
except ImportError:
    np = None

from .utils.jyutping import to_jyutping, text_to_jyutping_tokens
from .utils.text_utils import is_source_or_credit_line

# 入声字韵尾集合（-p, -t, -k）
ENTERING_TONE_ENDINGS = ("p", "t", "k")

_MIN_WORD_MS = 100
_LINE_GAP_MS = 50
_MIN_CHAR_S = 0.060
_RELEASE_THRESH = 0.020
_MIN_SILENT_MS = 60
_ENTERING_TONE_SILENT_MS = 120  # 入声字闭气截断容错


class CantoneseCTCAligner:
    """粤语 CTC 逐字对齐引擎（单例模式，GPU 向量化 forced_align）。"""

    _instance: Optional["CantoneseCTCAligner"] = None

    def __init__(self, device: str = "cuda") -> None:
        self._device = device
        self._processor = None
        self._model = None
        self._vocab_mapping: Optional["torch.Tensor"] = None
        self._syllable_list: List[str] = []
        self._syllable_to_id: Dict[str, int] = {}

    @classmethod
    def get_instance(cls, device: str = "cuda") -> "CantoneseCTCAligner":
        if cls._instance is None or cls._instance._device != device:
            cls._instance = cls(device=device)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            cls._instance.unload()
            cls._instance = None

    def unload(self) -> None:
        """释放模型与显存。"""
        try:
            import torch
            has_torch = True
        except ImportError:
            has_torch = False
        if self._model is not None:
            del self._model
            self._model = None
        self._processor = None
        self._vocab_mapping = None
        if has_torch and torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _init_model_and_mapping(self) -> None:
        """加载 wav2vec2-large-xlsr-cantonese 模型并构建 729 维粤拼投影映射表。"""
        if self._model is not None and self._vocab_mapping is not None:
            return

        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        model_id = "scottykwok/wav2vec2-large-xlsr-cantonese"
        try:
            self._processor = Wav2Vec2Processor.from_pretrained(model_id, local_files_only=True)
            self._model = Wav2Vec2ForCTC.from_pretrained(model_id, local_files_only=True)
        except Exception:
            self._processor = Wav2Vec2Processor.from_pretrained(model_id)
            self._model = Wav2Vec2ForCTC.from_pretrained(model_id)

        target_device = self._device if (torch.cuda.is_available() and self._device == "cuda") else "cpu"
        self._device = target_device
        self._model = self._model.to(target_device)
        self._model.eval()

        # 收集所有粤拼音节构建全局音节表（729 类）
        from .utils.jyutping import get_all_syllables
        self._syllable_list = get_all_syllables()
        self._syllable_to_id = {s: i + 1 for i, s in enumerate(self._syllable_list)}  # 0 为 blank

        # 构建 vocab -> syllable_id 映射张量
        vocab = self._processor.tokenizer.get_vocab()
        v_model = getattr(self._model.config, "vocab_size", 3555)
        mapping = torch.zeros(v_model, dtype=torch.long, device=self._device)

        pad_id = getattr(self._model.config, "pad_token_id", 3554)
        for tok, idx in vocab.items():
            if idx >= v_model:
                continue
            if idx == pad_id or tok in self._processor.tokenizer.all_special_tokens or tok == "|":
                mapping[idx] = 0
            else:
                jps = text_to_jyutping_tokens(tok)
                if jps and jps[0] in self._syllable_to_id:
                    mapping[idx] = self._syllable_to_id[jps[0]]
                else:
                    mapping[idx] = 0

        self._vocab_mapping = mapping

    def forward_sliding_window(
        self,
        audio_16k: "np.ndarray",
        window_sec: float = 20.0,
        overlap_sec: float = 2.0,
    ) -> "torch.Tensor":
        """滑窗推理（20s+2s 重叠）并按粤拼类合并列，输出 (1, T_total, num_classes) 对数概率。"""
        import torch

        self._init_model_and_mapping()
        assert self._model is not None
        assert self._processor is not None
        assert self._vocab_mapping is not None

        sr = 16000
        win_samples = int(window_sec * sr)
        overlap_samples = int(overlap_sec * sr)
        hop_samples = win_samples - overlap_samples
        total_samples = len(audio_16k)

        K = len(self._syllable_list)
        mapping = self._vocab_mapping
        V_model = len(mapping)

        # 音频长度短于单窗，单步前向
        if total_samples <= win_samples:
            inputs = self._processor(audio_16k, sampling_rate=sr, return_tensors="pt").input_values.to(self._device)
            with torch.no_grad():
                logits = self._model(inputs).logits  # (1, T, V)
            probs = torch.softmax(logits[:, :, :V_model], dim=-1)
            T_frames = probs.shape[1]
            grouped_probs = torch.zeros(1, T_frames, K + 1, device=self._device)
            mapping_exp = mapping.unsqueeze(0).unsqueeze(0).expand(1, T_frames, -1)
            grouped_probs.scatter_add_(2, mapping_exp, probs)
            return torch.log(grouped_probs.clamp(min=1e-12))

        # 多窗拼接
        window_log_probs: List["torch.Tensor"] = []
        overlap_frames = int(overlap_sec * 50)  # wav2vec2 50fps (20ms/frame)

        st = 0
        while st < total_samples:
            ed = min(st + win_samples, total_samples)
            chunk = audio_16k[st:ed]
            if len(chunk) < 1600:  # 忽略极短尾巴
                break

            inputs = self._processor(chunk, sampling_rate=sr, return_tensors="pt").input_values.to(self._device)
            with torch.no_grad():
                logits = self._model(inputs).logits
            probs = torch.softmax(logits[:, :, :V_model], dim=-1)
            t_len = probs.shape[1]
            grouped = torch.zeros(1, t_len, K + 1, device=self._device)
            m_exp = mapping.unsqueeze(0).unsqueeze(0).expand(1, t_len, -1)
            grouped.scatter_add_(2, m_exp, probs)
            log_p = torch.log(grouped.clamp(min=1e-12))

            if not window_log_probs:
                window_log_probs.append(log_p)
            else:
                # 拼接重叠区：前一窗尾与当前窗头做线性平滑 crossfade
                prev_lp = window_log_probs[-1]
                ov_f = min(overlap_frames, min(prev_lp.shape[1], log_p.shape[1]))
                if ov_f > 0:
                    weight = torch.linspace(0.0, 1.0, ov_f, device=self._device).unsqueeze(0).unsqueeze(-1)
                    # 线性插值概率空间
                    p_prev = torch.exp(prev_lp[:, -ov_f:, :])
                    p_curr = torch.exp(log_p[:, :ov_f, :])
                    p_blend = p_prev * (1.0 - weight) + p_curr * weight
                    blended_lp = torch.log(p_blend.clamp(min=1e-12))

                    window_log_probs[-1] = prev_lp[:, :-ov_f, :]
                    window_log_probs.append(blended_lp)
                    window_log_probs.append(log_p[:, ov_f:, :])
                else:
                    window_log_probs.append(log_p)

            st += hop_samples
            if ed >= total_samples:
                break

        full_log_probs = torch.cat(window_log_probs, dim=1)
        return full_log_probs

    def align_cantonese_lines(
        self,
        audio_16k: "np.ndarray",
        clean_lines: List[str],
        hints: Optional[List[Tuple[float, str]]] = None,
        v_mono_44k: Optional["np.ndarray"] = None,
        sr_44k: int = 44100,
    ) -> Dict[str, Any]:
        """全流程粤语逐字声学对齐：CTC 强制对齐 + 物理门禁精修。"""
        import torch
        import torchaudio.functional as F
        from .acoustic_aligner import (
            AcousticAligner,
            compute_rms_timeline,
            resolve_inter_phrase_boundaries,
            find_rms_offset,
        )

        try:
            total_audio_s = len(audio_16k) / 16000.0
            if v_mono_44k is None:
                v_mono_44k = audio_16k
                sr_44k = 16000

            # 1. 前向推理获取全局无调粤拼后验帧序列
            log_probs = self.forward_sliding_window(audio_16k)
            t_total_frames = log_probs.shape[1]

            # 2. 构建 Target 序列与行内 Token 元数据映射
            target_syllable_ids: List[int] = []
            target_token_meta: List[Dict[str, Any]] = []  # 记录 target_idx 对应的 (line_idx, token_idx)
            line_parsed_tokens: List[List[Dict[str, Any]]] = []

            for line_idx, text in enumerate(clean_lines):
                tokens = to_jyutping(text)
                line_parsed_tokens.append(tokens)
                for tok_idx, tok in enumerate(tokens):
                    jp = tok.get("jyutping")
                    if jp and jp in self._syllable_to_id:
                        sid = self._syllable_to_id[jp]
                        target_idx = len(target_syllable_ids)
                        target_syllable_ids.append(sid)
                        target_token_meta.append({
                            "line_idx": line_idx,
                            "tok_idx": tok_idx,
                            "char": tok["char"],
                            "syllable": jp,
                            "is_entering": jp.endswith(ENTERING_TONE_ENDINGS),
                        })
                        tok["target_idx"] = target_idx
                    else:
                        tok["target_idx"] = None

            if not target_syllable_ids:
                raise ValueError("粤语对齐拦截：提供的歌词未解析出任何有效粤拼音节！")

            # 3. 执行 torchaudio.functional.forced_align
            targets = torch.tensor([target_syllable_ids], dtype=torch.int32, device=self._device)
            input_lengths = torch.tensor([t_total_frames], dtype=torch.int32, device=self._device)
            target_lengths = torch.tensor([len(target_syllable_ids)], dtype=torch.int32, device=self._device)

            aligned_tokens, scores = F.forced_align(log_probs, targets, input_lengths, target_lengths, blank=0)
            spans = F.merge_tokens(aligned_tokens[0], scores[0], blank=0)

            # 4. 将 spans 时间戳回填到行与字结构中
            raw_aligned_lines: List[Dict[str, Any]] = []
            for line_idx, text in enumerate(clean_lines):
                toks = line_parsed_tokens[line_idx]
                line_words: List[Dict[str, Any]] = []

                for tok in toks:
                    t_idx = tok.get("target_idx")
                    if t_idx is not None and t_idx < len(spans):
                        span = spans[t_idx]
                        st_s = span.start * 0.020
                        ed_s = span.end * 0.020
                        # 若跨度过窄，按单字最小保护
                        if ed_s <= st_s:
                            ed_s = st_s + _MIN_CHAR_S
                        line_words.append({
                            "char": tok["char"],
                            "start": st_s,
                            "end": ed_s,
                            "type": tok.get("type", "hanzi"),
                            "is_entering": tok.get("jyutping", "").endswith(ENTERING_TONE_ENDINGS),
                            "score": float(span.score),
                        })
                    else:
                        # 英文单词或标点暂无 CTC 锚，先保留占位
                        line_words.append({
                            "char": tok["char"],
                            "start": None,
                            "end": None,
                            "type": tok.get("type", "unknown"),
                            "is_entering": False,
                            "score": 0.0,
                        })

                raw_aligned_lines.append({
                    "text": text,
                    "words": line_words,
                })

            # 5. 英文/非汉字片段启发式区间分配（保证 Enhanced LRC / SPL 流光不破行）
            for line_idx, line in enumerate(raw_aligned_lines):
                words = line["words"]
                aligned_indices = [i for i, w in enumerate(words) if w["start"] is not None]

                if not aligned_indices:
                    # 纯英文/未对齐行：结合提示轴或相邻行估算，绝不硬编码 0.0s
                    if hints and line_idx < len(hints):
                        line_st = hints[line_idx][0]
                        if line_idx + 1 < len(hints):
                            line_ed = max(line_st + 1.0, min(line_st + 3.5, hints[line_idx + 1][0] - 0.05))
                        else:
                            line_ed = line_st + 3.0
                    else:
                        prev_ed = None
                        for pi in range(line_idx - 1, -1, -1):
                            p_al = [w for w in raw_aligned_lines[pi]["words"] if w["end"] is not None]
                            if p_al:
                                prev_ed = p_al[-1]["end"]
                                break
                        next_st = None
                        for ni in range(line_idx + 1, len(raw_aligned_lines)):
                            n_al = [w for w in raw_aligned_lines[ni]["words"] if w["start"] is not None]
                            if n_al:
                                next_st = n_al[0]["start"]
                                break

                        if prev_ed is not None and next_st is not None:
                            if next_st > prev_ed + 0.1:
                                line_st = prev_ed + 0.05
                                line_ed = min(line_st + 3.5, next_st - 0.05)
                            else:
                                line_st = prev_ed + 0.05
                                line_ed = line_st + 2.5
                        elif prev_ed is not None:
                            line_st = prev_ed + 0.05
                            line_ed = line_st + 3.0
                        elif next_st is not None:
                            line_st = max(0.0, next_st - 3.5)
                            line_ed = next_st - 0.05
                        else:
                            line_st = 0.0
                            line_ed = 3.0

                    step = max(0.060, (line_ed - line_st) / max(1, len(words)))
                    for idx, w in enumerate(words):
                        w["start"] = line_st + idx * step
                        w["end"] = line_st + (idx + 1) * step
                    continue

                first_al = aligned_indices[0]
                last_al = aligned_indices[-1]

                # 5.1 行首未对齐单词（如 Baby 放在句首）
                if first_al > 0:
                    prev_end = None
                    if line_idx > 0:
                        p_al = [w for w in raw_aligned_lines[line_idx - 1]["words"] if w["end"] is not None]
                        if p_al:
                            prev_end = p_al[-1]["end"]
                    min_head = (prev_end + 0.05) if prev_end is not None else 0.0
                    head_dur = first_al * 0.35
                    head_st = max(min_head, words[first_al]["start"] - head_dur)
                    step = max(0.060, (words[first_al]["start"] - head_st) / first_al)
                    for i in range(first_al):
                        words[i]["start"] = head_st + i * step
                        words[i]["end"] = head_st + (i + 1) * step

                # 5.2 行中未对齐单词（如 hold 住）
                for k in range(len(aligned_indices) - 1):
                    idx1 = aligned_indices[k]
                    idx2 = aligned_indices[k + 1]
                    gap_count = idx2 - idx1 - 1
                    if gap_count > 0:
                        gap_st = words[idx1]["end"]
                        gap_ed = words[idx2]["start"]
                        min_needed = gap_count * 0.060
                        if gap_ed < gap_st + min_needed:
                            gap_ed = gap_st + min_needed
                            words[idx2]["start"] = gap_ed
                            if words[idx2]["end"] <= gap_ed:
                                words[idx2]["end"] = gap_ed + _MIN_CHAR_S
                        step = (gap_ed - gap_st) / gap_count
                        for j in range(1, gap_count + 1):
                            words[idx1 + j]["start"] = gap_st + (j - 1) * step
                            words[idx1 + j]["end"] = gap_st + j * step

                # 5.3 行尾未对齐单词
                if last_al < len(words) - 1:
                    tail_count = len(words) - 1 - last_al
                    tail_st = words[last_al]["end"]
                    step = 0.30
                    for j in range(1, tail_count + 1):
                        words[last_al + j]["start"] = tail_st + (j - 1) * step
                        words[last_al + j]["end"] = tail_st + j * step

            # 6. 第 3 层物理门禁精修（语言无关门禁体系完整闭环）
            hop_times, hop_rms = compute_rms_timeline(v_mono_44k, sr_44k)
            output_lines: List[Dict[str, Any]] = []

            for i, line in enumerate(raw_aligned_lines):
                text = line["text"]
                words = [w for w in line["words"] if w.get("char") and w.get("char").strip()]
                if not words:
                    continue

                raw_st = words[0]["start"]
                raw_ed = words[-1]["end"]

                # 6.1 结合提示轴（若存在）进行边界保护
                if hints and i < len(hints):
                    hint_st = hints[i][0]
                    # CTC 行锚与提示行轴在 ±0.9s 带内融合
                    if abs(raw_st - hint_st) <= 0.90:
                        raw_st = 0.6 * raw_st + 0.4 * hint_st
                        words[0]["start"] = raw_st

                # 6.2 首字物理起振精修（反向扫描相对阈值）
                prev_end_s = output_lines[-1]["end"] / 1000.0 if output_lines else 0.0
                cal_st = AcousticAligner.find_onset_backward(
                    v_mono_44k, sr_44k, words[0]["end"], words[0]["start"], prev_line_end_s=prev_end_s
                )
                words[0]["start"] = max(prev_end_s + 0.05, cal_st)

                # 6.3 行内单调推进（60ms 最小字距；字 end = 下一字 start）
                for ci in range(len(words) - 1):
                    if words[ci + 1]["start"] < words[ci]["start"] + _MIN_CHAR_S:
                        words[ci + 1]["start"] = words[ci]["start"] + _MIN_CHAR_S
                    words[ci]["end"] = words[ci + 1]["start"]

                # 6.4 末字 sustain 衰减门控（入声字 -p/-t/-k 闭气容错）
                is_entering = words[-1].get("is_entering", False)
                silent_tolerance = _ENTERING_TONE_SILENT_MS if is_entering else _MIN_SILENT_MS

                max_tail = max(words[-1]["start"] + 0.15, raw_ed + 0.5)
                actual_offset = find_rms_offset(
                    v_mono_44k, sr_44k,
                    t_search_st=words[-1]["start"] + 0.05,
                    t_search_max=max_tail,
                    thresh=_RELEASE_THRESH,
                    min_silent_ms=silent_tolerance,
                )
                words[-1]["end"] = max(words[-1]["start"] + _MIN_WORD_MS / 1000.0, actual_offset)

                line_start_s = words[0]["start"]
                line_end_s = words[-1]["end"]

                output_lines.append({
                    "start": int(line_start_s * 1000),
                    "end": int(line_end_s * 1000),
                    "duration": max(_MIN_WORD_MS, int((line_end_s - line_start_s) * 1000)),
                    "text": text,
                    "words": [{
                        "text": w["char"],
                        "start": int(w["start"] * 1000),
                        "end": int(w["end"] * 1000),
                        "duration": max(_MIN_WORD_MS // 2, int((w["end"] - w["start"]) * 1000)),
                    } for w in words],
                })

            # 7. 终局行间零重叠（铁律 19）与谷底锁定校准
            output_lines.sort(key=lambda l: l["start"])
            for i in range(len(output_lines) - 1):
                cur_l = output_lines[i]
                nxt_l = output_lines[i + 1]

                # 换气谷底校准
                cur_end_s = cur_l["end"] / 1000.0
                nxt_start_s = nxt_l["start"] / 1000.0
                if nxt_start_s > cur_end_s:
                    v_end, v_start = resolve_inter_phrase_boundaries(
                        hop_times, hop_rms, raw_end_prev=cur_end_s, raw_start_next=nxt_start_s,
                        min_gap_ms=_LINE_GAP_MS,
                    )
                    new_cur_end = int(v_end * 1000)
                    new_nxt_start = int(v_start * 1000)

                    if new_cur_end < cur_l["end"]:
                        cur_l["end"] = new_cur_end
                        cur_l["duration"] = max(100, cur_l["end"] - cur_l["start"])
                        ws = cur_l.get("words") or []
                        if ws and ws[-1]["end"] > new_cur_end:
                            s0 = min(ws[0]["start"], max(new_cur_end - 100, 0))
                            e0 = ws[-1]["end"]
                            span = max(1, e0 - s0)
                            target = max(1, new_cur_end - s0)
                            for w in ws:
                                w["start"] = s0 + (w["start"] - s0) * target // span
                                w["end"] = min(new_cur_end, s0 + (w["end"] - s0) * target // span)
                                if w["end"] <= w["start"]:
                                    w["end"] = w["start"] + max(50, _MIN_WORD_MS // 2)
                                w["duration"] = max(_MIN_WORD_MS // 2, w["end"] - w["start"])

                    if new_nxt_start > nxt_l["start"]:
                        nxt_l["start"] = new_nxt_start
                        nxt_l["duration"] = max(100, nxt_l["end"] - nxt_l["start"])
                        ws = nxt_l.get("words") or []
                        if ws and ws[0]["start"] < new_nxt_start:
                            s0 = ws[0]["start"]
                            e0 = max(ws[-1]["end"], s0 + 1)
                            target_st = new_nxt_start
                            target_ed = max(nxt_l["end"], target_st + 100)
                            target_span = max(1, target_ed - target_st)
                            orig_span = max(1, e0 - s0)
                            for w in ws:
                                w["start"] = target_st + (w["start"] - s0) * target_span // orig_span
                                w["end"] = target_st + (w["end"] - s0) * target_span // orig_span
                                if w["end"] <= w["start"]:
                                    w["end"] = w["start"] + max(50, _MIN_WORD_MS // 2)
                                w["duration"] = max(_MIN_WORD_MS // 2, w["end"] - w["start"])

                # 铁律 19：零重叠硬防御
                max_cur_end = nxt_l["start"] - _LINE_GAP_MS
                if cur_l["end"] > max_cur_end:
                    cur_l["end"] = max_cur_end
                    cur_l["duration"] = max(100, cur_l["end"] - cur_l["start"])
                    ws = cur_l.get("words") or []
                    if ws and ws[-1]["end"] > max_cur_end:
                        s0 = min(ws[0]["start"], max(max_cur_end - 100, 0))
                        e0 = ws[-1]["end"]
                        span = max(1, e0 - s0)
                        target = max(1, max_cur_end - s0)
                        for w in ws:
                            w["start"] = s0 + (w["start"] - s0) * target // span
                            w["end"] = min(max_cur_end, s0 + (w["end"] - s0) * target // span)
                            if w["end"] <= w["start"]:
                                w["end"] = w["start"] + max(50, _MIN_WORD_MS // 2)
                            w["duration"] = max(_MIN_WORD_MS // 2, w["end"] - w["start"])

            # 8. 终局字级完整性防御：确保行内每个字 end > start 且单调
            for ln in output_lines:
                ws = ln.get("words") or []
                for k in range(len(ws)):
                    if ws[k]["end"] <= ws[k]["start"]:
                        ws[k]["end"] = ws[k]["start"] + max(50, _MIN_WORD_MS // 2)
                    ws[k]["duration"] = ws[k]["end"] - ws[k]["start"]
                    if k + 1 < len(ws) and ws[k + 1]["start"] < ws[k]["end"]:
                        ws[k + 1]["start"] = ws[k]["end"]
                        if ws[k + 1]["end"] <= ws[k + 1]["start"]:
                            ws[k + 1]["end"] = ws[k + 1]["start"] + max(50, _MIN_WORD_MS // 2)
                        ws[k + 1]["duration"] = ws[k + 1]["end"] - ws[k + 1]["start"]
                if ws:
                    ln["start"] = ws[0]["start"]
                    ln["end"] = ws[-1]["end"]
                    ln["duration"] = max(100, ln["end"] - ln["start"])

            msg = (f"[CantoneseCTC] 粤语逐字声学对齐完成：{len(output_lines)} 行"
                   f"（torchaudio forced_align，入声闭气门控，行间 ≥{_LINE_GAP_MS}ms）")
            try:
                print(f"✅ {msg}")
            except Exception:
                print(f"[OK] {msg}")

            return {
                "source": "cantonese_ctc_alignment",
                "lyric_type": "verbatim",
                "synthesized_words": False,
                "time_precision": 3,
                "lines": output_lines,
            }
        finally:
            self.unload()
