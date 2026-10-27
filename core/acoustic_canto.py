# -*- coding: utf-8 -*-
"""粤语声学对齐引擎 (Cantonese Acoustic Alignment Engine v1.5.0)。

核心技术路线：
1. 粤拼中转桥（Jyutping Bridge）：统一字表与声学模型投影空间至 703 类无调粤拼类别；
2. torchaudio 原生 forced_align（PyTorch Viterbi DP）GPU 向量化直接字级对齐；
3. 滑窗分块推理（20s 窗 + 2s 重叠）+ 显存纪律（严守 6GB 显存红线）；
4. 非汉字策略：阿拉伯数字转粤拼音节入对齐 target，英文字词启发式区间插值分配；
5. 入声字（-p/-t/-k）闭气容错 sustain 门控（120ms 容错）+ 谱流瞬态吸附 + 铁律 19 零重叠。
"""

from __future__ import annotations

import os
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import numpy as np
except ImportError:
    np = None

from .utils.jyutping import to_jyutping, text_to_jyutping_tokens
from .acoustic_aligner import absorb_punctuation_tokens

# 入声字韵尾集合（-p, -t, -k）
ENTERING_TONE_ENDINGS = ("p", "t", "k")

_MIN_WORD_MS = 100
_LINE_GAP_MS = 50
_MIN_CHAR_S = 0.060
_RELEASE_THRESH = 0.020
_MIN_SILENT_MS = 60
_ENTERING_TONE_SILENT_MS = 120  # 入声字闭气截断容错

# v1.5.1-A1: 双侧重同步的前瞻窗口。判定「多余 run」时向前看这么多个 target，
# 若该 run 的音节 id 出现在窗口内，就认定当前 target 缺失而非 run 多余。
# 取 12 是因为一句歌词通常不超过 12 个音节，超过则宁可丢弃该 run（影响更轻）。
_RESYNC_WINDOW_TARGETS = 12


def reconstruct_target_spans(
    aligned_tokens_1d: "torch.Tensor",
    scores_1d: "torch.Tensor",
    target_syllable_ids: List[int]
) -> List[Optional[Dict[str, Any]]]:
    """从帧级路径中严格还原每个 target 音节的时间与分数，支持连续叠字切分（P1-21）。"""
    raw_tokens = aligned_tokens_1d.tolist()
    raw_scores = scores_1d.tolist()
    T = len(raw_tokens)

    # 1. 把帧路径压缩为 run 列表，blank(0) 仅作分隔，不作为有效 run
    runs = []
    f = 0
    while f < T:
        tk = raw_tokens[f]
        if tk == 0:
            f += 1
            continue
        st = f
        while f < T and raw_tokens[f] == tk:
            f += 1
        runs.append((tk, st, f))

    def build_span(s_frame: float, e_frame: float) -> Dict[str, Any]:
        # v7：在唯一出口统一钳位，任何调用路径都不得产出 [0, T] 之外的帧区间。
        # 极值保护：T <= 0 时直接安全返回（该分支实测不可达，纯防御）。
        # v1.5.2-D5：score 用 None 表达「没有分数」，不要用 0.0 —— 0.0 在语义上
        # 是「满分上限」，会让下游误以为这是一个有效的满分，而不是缺分。
        if T <= 0:
            return {"start": 0.0, "end": 0.0, "score": None}
        s_frame = max(0.0, min(float(T - 1), s_frame))
        e_frame = max(s_frame + 1.0, min(float(T), e_frame))
        st_s = s_frame * 0.020
        ed_s = e_frame * 0.020
        s_int, e_int = int(s_frame), max(int(s_frame) + 1, int(e_frame))
        seg_scores = raw_scores[s_int:e_int]
        # 空切片 = 这一组没有可用分数 → None（同样不要用 0.0，见上）。
        mean_sc = float(sum(seg_scores) / len(seg_scores)) if seg_scores else None
        return {"start": st_s, "end": ed_s, "score": mean_sc}

    # 2. target 侧按连续同字分组并逐组映射
    # v1.5.1-A1: 旧版在「本组找不到 run」时 break，把当前 target 之后的**全部**
    # target 一起置 None——一个音节错配就毁掉整首后半段。改为双侧重同步：
    # 每轮先判断「是 target 缺 run」还是「run 多余」，只推进 u 或 ri 之一，
    # 缺失时只跳过本组，后续 target 仍能各自匹配。
    n_targets = len(target_syllable_ids)
    target_spans: List[Optional[Dict[str, Any]]] = []
    u = 0
    ri = 0
    n_missing = 0
    n_stray = 0

    def _lookahead(from_u: int) -> set:
        return set(target_syllable_ids[from_u: from_u + _RESYNC_WINDOW_TARGETS])

    while u < n_targets:
        sid = target_syllable_ids[u]
        k = 1
        while u + k < n_targets and target_syllable_ids[u + k] == sid:
            k += 1

        # (a) 双侧重同步：先把「多余 run」消化掉。
        # 若该 run 的音节 id 出现在后续 target 里，说明它是后面的音节提前出现，
        # 即**当前 target 缺失**，此时 ri 不动；否则它是模型多吐的 run，直接丢弃。
        while ri < len(runs) and runs[ri][0] != sid:
            if runs[ri][0] in _lookahead(u):
                break
            ri += 1
            n_stray += 1

        # (b) 收集本组 run（最多 k 个）
        grp = []
        while ri < len(runs) and runs[ri][0] == sid and len(grp) < k:
            grp.append(runs[ri])
            ri += 1

        if not grp:
            # 当前 target 在帧路径里确实没有跨度：只跳过本组 k 项，绝不 break。
            # 交由下游 5.x 的区间插值兜底，不影响其余 target 的匹配。
            target_spans.extend([None] * k)
            n_missing += k
            u += k
            continue

        if len(grp) == k:
            # 独立 run：1:1 映射，绝对禁止切分
            for _, s, e in grp:
                target_spans.append(build_span(s, e))
        elif len(grp) == 1 and k > 1:
            # 无 blank 导致合并成 1 个 run：此时才允许按 k 等分
            _, s, e = grp[0]
            step = (e - s) / k
            for r in range(k):
                target_spans.append(build_span(s + r * step, s + (r + 1) * step))
        else:
            # run 数与 target 数不一致的退化情形：优先 1:1 用掉，剩余等分末 run
            for r_idx in range(len(grp) - 1):
                target_spans.append(build_span(grp[r_idx][1], grp[r_idx][2]))
            rem_k = k - (len(grp) - 1)
            _, s, e = grp[-1]
            eff_e = min(float(T), max(s + rem_k, e))
            avail = eff_e - s
            if avail < rem_k:
                # 帧数不足：整组 k 个 target 统一置 None，交由下游平滑插值
                if len(grp) > 1:
                    del target_spans[len(target_spans) - (len(grp) - 1):]
                target_spans.extend([None] * k)
            else:
                step = avail / rem_k
                for r in range(rem_k):
                    sub_s = s + r * step
                    sub_e = min(eff_e, s + (r + 1) * step)
                    target_spans.append(build_span(sub_s, sub_e))

        u += k

    # v1.5.1-A1: 长度恒等防御。构造上每次迭代恰好 append k 项、u += k，
    # 所以 len(target_spans) 必等于 n_targets；一旦不等说明上面的分支被改坏了，
    # 宁可补齐/截断并报错，也不能让下游 target_spans[t_idx] 越界。
    if len(target_spans) != n_targets:
        logger.error(
            "   [canto-reconstruct] 长度失配 %d != %d（已安全补齐/截断）",
            len(target_spans), n_targets,
        )
        target_spans = (target_spans + [None] * n_targets)[:n_targets]

    if n_missing or n_stray:
        logger.info(
            "   [canto-reconstruct] target 缺失 %d / 多余 run 丢弃 %d / 共 %d 个 target",
            n_missing, n_stray, n_targets,
        )

    return target_spans


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
        """加载 wav2vec2-large-xlsr-cantonese 模型并构建 703 类粤拼投影映射表。"""
        if self._model is not None and self._vocab_mapping is not None:
            return

        import torch
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        model_id = "scottykwok/wav2vec2-large-xlsr-cantonese"
        try:
            self._processor = Wav2Vec2Processor.from_pretrained(model_id, local_files_only=True)
            self._model = Wav2Vec2ForCTC.from_pretrained(model_id, local_files_only=True)
        except Exception as exc:
            # v1.5.1-C4: 旧版这里**静默联网下载**，与 lang_detect 的「本地缺失即跳过」
            # 不一致，且无任何日志。现在：MTL_OFFLINE=1 时禁止联网并抛环境错；
            # 否则显式告知正在下载。
            if os.environ.get("MTL_OFFLINE", "").strip() in ("1", "true", "yes", "on"):
                raise RuntimeError(
                    f"MTL_OFFLINE=1：本地无 {model_id}，禁止联网下载"
                ) from exc
            logger.warning(
                "   ⚠️ [canto] 本地无 %s（%s），将联网下载（可用 MTL_OFFLINE=1 禁止）",
                model_id, type(exc).__name__,
            )
            self._processor = Wav2Vec2Processor.from_pretrained(model_id)
            self._model = Wav2Vec2ForCTC.from_pretrained(model_id)

        target_device = self._device if (torch.cuda.is_available() and self._device == "cuda") else "cpu"
        self._device = target_device
        self._model = self._model.to(target_device)
        self._model.eval()

        # 收集所有粤拼音节构建全局音节表（实测 703 类）
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
        # v1.5.1-A5: 旧版用 assert 做运行期校验，`python -O` 下会被整体移除，
        # 之后 self._model(...) 会以 AttributeError 的形式崩在更远的地方。
        if self._model is None or self._processor is None or self._vocab_mapping is None:
            raise RuntimeError("粤语 CTC 模型/投影映射未就绪（_init_model_and_mapping 未生效）")

        sr = 16000
        win_samples = int(window_sec * sr)
        overlap_samples = int(overlap_sec * sr)
        hop_samples = win_samples - overlap_samples
        # v1.5.1-A5: hop<=0 会让下面的 while st < total_samples 永远不前进（死循环），
        # 参数当前未对外暴露，属理论路径，但守卫成本为零。
        if hop_samples <= 0:
            raise ValueError(
                f"滑窗参数非法：window_sec={window_sec} overlap_sec={overlap_sec} "
                f"-> hop_samples={hop_samples}，必须 > 0"
            )
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
            target_spans = reconstruct_target_spans(aligned_tokens[0], scores[0], target_syllable_ids)

            # 4. 将 target_spans 时间戳回填到行与字结构中（v8：秒级单位契约，禁止二次乘 0.020）
            raw_aligned_lines: List[Dict[str, Any]] = []
            n_unaligned_targets = 0   # v1.5.1-B: 供 unaligned_ratio（分母 = target 音节数）
            for line_idx, text in enumerate(clean_lines):
                toks = line_parsed_tokens[line_idx]
                line_words: List[Dict[str, Any]] = []

                for tok in toks:
                    t_idx = tok.get("target_idx")
                    sp = target_spans[t_idx] if (t_idx is not None and t_idx < len(target_spans)) else None
                    if sp is None:
                        if t_idx is not None:
                            # 只有「本该有跨度的 target 音节」才计入未对齐；
                            # 拉丁词（t_idx=None）从来不是 target，不进分母也不进分子。
                            n_unaligned_targets += 1
                        line_words.append({
                            "char": tok["char"], "start": None, "end": None,
                            "type": tok.get("type", "unknown"), "is_entering": False, "score": None,
                        })
                        continue
                    st_s, ed_s = sp["start"], sp["end"]      # 已是秒，禁止再乘 0.020
                    if ed_s <= st_s:                         # T<=0 或退化零宽兜底
                        ed_s = st_s + _MIN_CHAR_S
                    line_words.append({
                        "char": tok["char"], "start": st_s, "end": ed_s,
                        "type": tok.get("type", "hanzi"),
                        "is_entering": tok.get("jyutping", "").endswith(ENTERING_TONE_ENDINGS),
                        "score": sp["score"],
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
                    # v1.5.1-C6: 行首未对齐字用固定 0.35s/字 编造，属启发式不是声学
                    logger.debug(
                        "   [canto] 行 %d 行首 %d 个未对齐字按 0.35s/字 编造（启发式）",
                        line_idx + 1, first_al,
                    )
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
                    # v1.5.1-C6: 行尾未对齐字按固定 0.30s/字 编造，属启发式不是声学
                    logger.debug(
                        "   [canto] 行 %d 行尾 %d 个未对齐字按 0.30s/字 编造（启发式）",
                        line_idx + 1, tail_count,
                    )
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

                raw_words = [{
                    "text": w["char"],
                    "start": int(w["start"] * 1000),
                    "end": int(w["end"] * 1000),
                    "duration": max(_MIN_WORD_MS // 2, int((w["end"] - w["start"]) * 1000)),
                    "score": w.get("score"),
                } for w in words]
                # P1-25: 标点符号吸收合并至前字（双路径同步实施）
                merged_words = absorb_punctuation_tokens(raw_words, text)
                output_lines.append({
                    "start": int(line_start_s * 1000),
                    "end": int(line_end_s * 1000),
                    "duration": max(_MIN_WORD_MS, int((line_end_s - line_start_s) * 1000)),
                    "text": text,
                    "words": merged_words,
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

            # ── v1.5.1-B: 双层质量门禁 ──────────────────────────────────
            # 旧实现是单点熔断：mean_score < -2.10 就整首丢逐字。-2.10 距唯一粤语
            # 样本（焚情 mean=-1.886）只有 0.214，误杀风险远大于漏放；而且未对齐字
            # 被排除在分母外，伪时间越多反而越不容易触发。现在改为：
            #   · 整曲宽门限（防整体不可用） + 分行严门限（个别差行降级为行级）
            #   · unaligned_ratio 用 n_target 做分母，单独拦「大量音节无跨度」
            #   · 熔断抛 AcousticQualityError（与环境错误严格区分），上层可做 zh 重试
            from .acoustic_quality import (
                load_gate_config, compute_track_stats,
                evaluate_track_gate, evaluate_line_gate,
                AcousticQualityError, AlignStatus,
                finalize_word_source_flags,
            )

            gate_cfg = load_gate_config()

            # B3: 分行门限 —— 说唱桥段不再拖垮整首。
            # 注意顺序：**先统计、后降级**。若先清 words 再算 stats，
            # 被降级的行会把分数一起清掉，warn 模式下只剩 mean=-99，日志毫无信息量。
            demoted_idx = []
            for li, ln in enumerate(output_lines):
                dec = evaluate_line_gate(ln, gate_cfg)
                if dec.demote:
                    demoted_idx.append((li, "; ".join(dec.reasons)))

            hints_span_s = None
            if hints and len(hints) >= 2 and hints[-1][0] > hints[0][0]:
                hints_span_s = hints[-1][0] - hints[0][0]

            stats = compute_track_stats(
                output_lines,
                total_audio_s,
                n_target=len(target_syllable_ids),
                n_target_unaligned=n_unaligned_targets,
                demote_ratio=(len(demoted_idx) / max(1, len(output_lines))),
                n_lines_evaluable=len(output_lines),
                n_lines_demoted=len(demoted_idx),
                hints_span_s=hints_span_s,
            )
            reasons = evaluate_track_gate(stats, gate_cfg)

            logger.info("   [canto-gate] %s", stats.summary())
            if reasons:
                if gate_cfg.enforce:
                    raise AcousticQualityError(reasons, stats, engine="cantonese_ctc")
                logger.warning(
                    "   ⚠️ [canto] 质量门禁命中（warn 模式，不熔断）: %s | %s",
                    ", ".join(reasons), stats.summary(),
                )

            # 真正执行分行降级（清 words，保留行级 start/end）
            for li, why in demoted_idx:
                output_lines[li]["words"] = []
                logger.info("   [canto-gate] 行 %d 降级为行级（%s）", li + 1, why)

            msg = (f"[CantoneseCTC] 粤语逐字声学对齐完成：{len(output_lines)} 行"
                   f"（torchaudio forced_align，入声闭气门控，行间 ≥{_LINE_GAP_MS}ms）")
            try:
                print(f"✅ {msg}")
            except Exception:
                print(f"[OK] {msg}")

            result = {
                "source": "cantonese_ctc_alignment",
                "lyric_type": "verbatim",
                "synthesized_words": False,
                "time_precision": 3,
                "lines": output_lines,
                # v1.5.1-B: 记账字段，供 process_song 做 synthesized_ratio 汇总
                "align_status": AlignStatus.OK.value,
                "quality_stats": stats.summary(),
                "demoted_line_indices": [li for li, _ in demoted_idx],
                "n_target": len(target_syllable_ids),
                "n_target_unaligned": n_unaligned_targets,
                "align_engine": "cantonese_ctc",
            }
            # 粤语 CTC 是「有分域」引擎：整行无分 = 邻字内插，不是 whisper 型无分
            finalize_word_source_flags(
                result,
                demoted_lines=[li for li, _ in demoted_idx],
                engine_scored=True,
            )
            return result
        finally:
            self.unload()
