# -*- coding: utf-8 -*-
"""中文 wav2vec2 CTC 逐行窗强制对齐（v1.6.0）。

为什么需要
----------
普通话路径原本只有「Whisper 双引擎 + 谱流切分」：逐字由段落摊出来，
行内会「长字吞停顿」（>700ms 的字里含 ≥250ms 静音），行首会跨到上一句尾音。
实测《那么骄傲》1:40「让我睡不着」整行早 1.1s（应为 103.353s）。

本模块与粤语路径同源：**每行独立窗口 + CTC 强制对齐**，从机制上免疫全局漂移。

词表事实（实测 jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn，3503 项）
--------------------------------------------------------------------
· 只有**大写 A–Z**，没有 a–z、没有 0–9、没有任何标点
  → 拉丁字母必须走 `ch.upper()` 回退，否则 "Say goodbye" 整词变零 token；
  → 数字串与标点必然零 token → 必须吸附成间隙（`no_ctc`），不能"跳过算丢词"。
· 交付物里标点是并入前一词的（实测 `宮?` 两字符只对应 1 个 token）
  → **绝不能按字符数推算 token 索引**，必须逐字符建 owner 映射
  （历史参考实现按字数记账，遇到带标点的行会整体错位一格）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .qc import Env, MIN_WORD_MS, LINE_GAP_MS, qc_l1_monotonic

logger = logging.getLogger(__name__)

MODEL_ID = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"

PAD_L_MS = 1200          # 窗口左扩：源行标签是「显示时间」，普遍比真起唱早 0.7–1.1s
PAD_R_MS = 900           # 窗口右扩：防行尾收音被截断
LAST_MAX_MS = 800        # 末字最长（配合 release_end）
MAX_WIN_S = 20.0         # 单窗上限，防 O(T²) 显存爆
HARD_SPAN_MS = 12000     # 单行跨度硬上限（远超正常行）


# ---------------------------------------------------------------------------
# 1) token 构建与回填（红线：只借时间、绝不改词）
# ---------------------------------------------------------------------------
def build_targets(words: Sequence[Dict[str, Any]], vocab: Dict[str, int]) -> Tuple[List[int], List[int]]:
    """逐字符建 token。返回 (ids, owner)，owner[i] = 该 token 所属 word 下标。"""
    ids: List[int] = []
    owner: List[int] = []
    for wi, w in enumerate(words):
        for ch in str(w.get("text") or ""):
            tid = vocab.get(ch)
            if tid is None:
                tid = vocab.get(ch.upper())
            if tid is None:
                tid = vocab.get(ch.lower())
            if tid is None:
                continue                    # 标点 / 数字 / 未覆盖符号 → 不进 CTC
            ids.append(int(tid))
            owner.append(wi)
    return ids, owner


def spans_to_words(
    words: Sequence[Dict[str, Any]],
    starts: Sequence[float],
    owner: Sequence[int],
    line_tag: Optional[int],
    next_tag: Optional[int],
    env: Optional[Env],
    min_word_ms: int = MIN_WORD_MS,
    gap_ms: int = LINE_GAP_MS,
) -> Optional[List[Dict[str, Any]]]:
    """把「token 起点」摊回词级时间轴。只写 start/end，**绝不改 text**。

    返回新 words 列表；全行零 token（无从对齐）时返回 None，由调用方按失败处理。
    """
    if not words:
        return None
    line_text = "".join(str(w.get("text") or "") for w in words)
    out = [dict(w) for w in words]

    tok_first: Dict[int, int] = {}
    for i, wi in enumerate(owner):
        tok_first.setdefault(wi, i)
    tok_wi = sorted(tok_first)
    if not tok_wi:
        return None

    # ① 起点 = 本词首个 token 的起点
    for wi in tok_wi:
        out[wi]["start"] = int(round(float(starts[tok_first[wi]]) * 1000))

    # ② 终点 = 下一个「有 token」词的起点（跨过零 token 词）；末词走能量释放点
    total_ms = int(round(env.duration_s * 1000)) if env is not None else None
    for k, wi in enumerate(tok_wi):
        if k + 1 < len(tok_wi):
            out[wi]["end"] = out[tok_wi[k + 1]]["start"]
        else:
            cap = (int(next_tag) - gap_ms) if next_tag else (total_ms or out[wi]["start"] + LAST_MAX_MS)
            cap = min(cap, out[wi]["start"] + LAST_MAX_MS)
            if env is not None:
                end_s = env.release_end(out[wi]["start"] / 1000.0, max(cap, out[wi]["start"] + min_word_ms) / 1000.0)
                out[wi]["end"] = int(round(end_s * 1000))
            else:
                out[wi]["end"] = max(out[wi]["start"] + min_word_ms, cap)

    # ③ 零 token 词（纯数字 / 未覆盖符号）：吸附在相邻 token 词之间的间隙
    for wi in range(len(out)):
        if wi in tok_first:
            continue
        prev_wi = next((j for j in range(wi - 1, -1, -1) if j in tok_first), None)
        nxt_wi = next((j for j in range(wi + 1, len(out)) if j in tok_first), None)
        st = out[prev_wi]["end"] if prev_wi is not None else int(line_tag or 0)
        ed = out[nxt_wi]["start"] if nxt_wi is not None else st + min_word_ms
        out[wi]["start"], out[wi]["end"] = st, max(ed, st + min_word_ms)
        out[wi]["no_ctc"] = True

    for w in out:
        w["duration"] = int(w["end"]) - int(w["start"])

    # ④ 红线断言（缺一不可）
    if [str(w.get("text") or "") for w in out] != [str(w.get("text") or "") for w in words]:
        raise AssertionError("逐字文本被改动")
    if "".join(str(w.get("text") or "") for w in out) != line_text:
        raise AssertionError("字面恒等式失败")
    return out


# ---------------------------------------------------------------------------
# 2) 模型封装
# ---------------------------------------------------------------------------
class ChineseCTCAligner:
    """中文 CTC 对齐器（单例）。模型一律 local_files_only 加载，缺失即显式阻断。"""

    _instance: Optional["ChineseCTCAligner"] = None

    def __init__(self, device: str = "cuda") -> None:
        self._device = device
        self._model = None
        self._processor = None
        self._vocab: Optional[Dict[str, int]] = None

    @classmethod
    def get_instance(cls, device: str = "cuda") -> "ChineseCTCAligner":
        if cls._instance is None:
            cls._instance = cls(device=device)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        if cls._instance is not None:
            try:
                cls._instance.unload()
            except Exception:
                pass
        cls._instance = None

    def unload(self) -> None:
        self._model = None
        self._processor = None
        self._vocab = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    @property
    def vocab(self) -> Dict[str, int]:
        self._ensure()
        return self._vocab or {}

    def _ensure(self) -> None:
        if self._model is not None and self._vocab is not None:
            return
        try:
            import torch
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        except Exception as e:  # pragma: no cover
            from ..acoustic_aligner import AcousticEnvironmentError
            raise AcousticEnvironmentError(f"中文 CTC 依赖缺失（torch/transformers）：{e}")

        try:
            self._processor = Wav2Vec2Processor.from_pretrained(MODEL_ID, local_files_only=True)
            self._model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID, local_files_only=True).to(self._device).eval()
            self._vocab = dict(self._processor.tokenizer.get_vocab())
        except Exception as e:
            from ..acoustic_aligner import AcousticEnvironmentError
            raise AcousticEnvironmentError(
                f"中文 CTC 模型不可用（{MODEL_ID}）：{e}。"
                "请先按 README「模型准备」下载，或设 MTL_ZH_CTC=0 回退双引擎。"
            )
        logger.debug("[zh-ctc] 模型就绪：词表 %d 项", len(self._vocab))

    def log_probs(self, audio_16k: "np.ndarray") -> "Any":
        """单窗前向，返回 (1, T, V) 对数概率。"""
        import torch

        self._ensure()
        assert self._processor is not None and self._model is not None
        inputs = self._processor(
            np.asarray(audio_16k, dtype=np.float32), sampling_rate=16000, return_tensors="pt"
        ).input_values.to(self._device)
        with torch.no_grad():
            logits = self._model(inputs).logits
        return torch.log_softmax(logits, dim=-1)


# ---------------------------------------------------------------------------
# 3) 逐行窗对齐主流程
# ---------------------------------------------------------------------------
def _window_for_line(
    i: int,
    n_chars: int,
    hints: Optional[Sequence[Tuple[float, str]]],
    total_s: float,
    cursor_s: float,
    env: Optional[Env] = None,
    pad_l_ms: int = PAD_L_MS,
    pad_r_ms: int = PAD_R_MS,
) -> Tuple[float, float]:
    """行窗：有 hints 用 `[tag−左扩, next+右扩]`；无 hints 用包络跳到下一处人声活动。

    v1.6.0 两处修正：
    1. cursor 原先用「上一窗右边界」做下一窗起点，误差逐行累积（实测 26/51 回退）；
       现改为跟随上一行处理后的实际末字。
    2. 纯文本输入没有行轴，行间常有间奏：窗口起点用 `Env.onsets` 跳到
       cursor 之后的第一个起振，避免整窗落在静音区导致 forced_align 乱摊。
    """
    if hints and i < len(hints):
        tag = float(hints[i][0])
        nxt = float(hints[i + 1][0]) if i + 1 < len(hints) else min(total_s, tag + 8.0)
        w0 = max(0.0, tag - pad_l_ms / 1000.0)
        w1 = min(total_s, nxt + pad_r_ms / 1000.0)
    else:
        base = max(0.0, float(cursor_s) - 0.30)
        if env is not None:
            # 跳到 cursor 之后的**第一处人声起振**（搜索窗放宽到 15s，覆盖长间奏）；
            # 否则窗口会落在静音/前奏上，forced_align 只能把音节摊进无声段。
            try:
                cand = [t for t in env.onsets(base + 0.05, min(total_s, base + 15.0)) if t >= base]
                if cand:
                    base = cand[0]
            except Exception:
                pass
        w0 = max(0.0, base - 0.15)
        # 窗宽收紧（实测华语演唱 ≈0.3–0.45s/字）：过宽会把**下一行**的人声也
        # 吞进窗，行内中段音节被摊进行间静音 → 产生整批「吞停顿」假长字。
        w1 = min(total_s, w0 + max(3.0, n_chars * 0.45 + 1.2))
    if w1 - w0 > MAX_WIN_S:
        w1 = w0 + MAX_WIN_S
    if w1 - w0 < 1.0:
        w1 = min(total_s, w0 + 1.0)
    return w0, w1


def align_zh_windowed(
    audio_16k: "np.ndarray",
    clean_lines: Sequence[str],
    hints: Optional[Sequence[Tuple[float, str]]] = None,
    env: Optional[Env] = None,
    device: str = "cuda",
    pad_l_ms: int = PAD_L_MS,
    pad_r_ms: int = PAD_R_MS,
) -> Dict[str, Any]:
    """逐行窗 CTC 对齐。返回与双引擎同构的结果字典（毫秒整数）。

    失败行（零 token / 窗口过窄 / 序列化异常）不静默：交给 `apply_gate` 的
    线性保底，并把行号计入 `fallback_line_idx`。
    """
    import torch
    import torchaudio.functional as F

    from .gate import apply_gate, split_display_units

    aligner = ChineseCTCAligner.get_instance(device=device)
    vocab = aligner.vocab

    audio = np.asarray(audio_16k, dtype=np.float32)
    total_s = len(audio) / 16000.0

    lines: List[Dict[str, Any]] = []
    scores: List[Optional[float]] = []
    anchors: List[Optional[int]] = []
    cursor_s = 0.0

    for i, text in enumerate(clean_lines):
        units = split_display_units(text)
        words = [{"text": u} for u in units] or [{"text": (text or "").strip()}]
        ids, owner = build_targets(words, vocab)

        tag_ms = int(round(float(hints[i][0]) * 1000)) if hints and i < len(hints) else None
        next_ms = int(round(float(hints[i + 1][0]) * 1000)) if hints and i + 1 < len(hints) else None
        n_chars = sum(len(str(w["text"]).replace(" ", "")) for w in words) or 1

        built: Optional[List[Dict[str, Any]]] = None
        score: Optional[float] = None
        local_w0: Optional[float] = None
        if ids:
            w0, w1 = _window_for_line(i, n_chars, hints, total_s, cursor_s, env=env,
                                      pad_l_ms=pad_l_ms, pad_r_ms=pad_r_ms)
            local_w0 = w0
            seg = audio[int(w0 * 16000): int(w1 * 16000)]
            if len(seg) >= 16000 * 0.8:
                try:
                    lp = aligner.log_probs(seg)
                    t_frames = int(lp.shape[1])
                    frame_s = (w1 - w0) / max(1, t_frames)
                    tgt = torch.tensor([ids], dtype=torch.int32, device=device)
                    il = torch.tensor([t_frames], dtype=torch.int32, device=device)
                    tl = torch.tensor([len(ids)], dtype=torch.int32, device=device)
                    aligned, sc = F.forced_align(lp, tgt, il, tl, blank=0)
                    from ..acoustic_canto import reconstruct_target_spans
                    spans = reconstruct_target_spans(aligned[0], sc[0], ids)
                    # reconstruct_target_spans 以 50fps(0.020s) 为标度，按实际帧率校正
                    k = frame_s / 0.02
                    starts = [w0 + float(sp["start"]) * k if sp else w0 for sp in spans]
                    built = spans_to_words(words, starts, owner, tag_ms, next_ms, env)
                    vals = [float(sp["score"]) for sp in spans
                            if sp and sp.get("score") is not None]
                    score = float(np.mean(vals)) if vals else None
                except Exception as e:
                    logger.warning("   ⚠️ [zh-ctc] 行 %d 对齐失败：%s", i + 1, e)
                    built = None
        if not built:
            # 保底：按行区间均分（cursor 模式用本行窗起点），随后由 apply_gate 统一记账
            from .gate import linear_split_words
            st = tag_ms if tag_ms is not None else int((local_w0 if local_w0 is not None else cursor_s) * 1000)
            nd = next_ms if next_ms is not None else st + max(2000, n_chars * 400)
            built = linear_split_words(text, st, max(st + MIN_WORD_MS, nd - LINE_GAP_MS))
            score = None
            if not built:
                continue

        # cursor 跟随**本行处理后的实际末字**（含回退行），防逐行累积漂移
        cursor_s = min(total_s, int(built[-1]["end"]) / 1000.0)

        ln = {
            "start": int(built[0]["start"]),
            "end": int(built[-1]["end"]),
            "duration": max(MIN_WORD_MS, int(built[-1]["end"]) - int(built[0]["start"])),
            "text": text,
            "words": built,
        }
        # 行跨度异常：给回退闸一个明确落点
        if ln["duration"] > HARD_SPAN_MS:
            ln["_span_anomaly"] = True
        lines.append(ln)
        scores.append(score)
        anchors.append(tag_ms)

    result: Dict[str, Any] = {
        "source": "demucs_gpu_alignment",
        "lyric_type": "verbatim",
        "synthesized_words": False,
        "time_precision": 3,
        "lines": lines,
        "align_engine": "wav2vec2_zh_windowed",
    }

    # 逐行闸（回退 → 旧值优先；本路径无旧值，故走线性保底）
    gate = apply_gate(
        lines,
        env=env,
        scores=scores,
        anchors=anchors,
        old_words=[[] for _ in lines],
        next_starts=[(anchors[i + 1] if i + 1 < len(anchors) else None) for i in range(len(lines))],
    )
    result["gate_reverts"] = gate["reverts"]

    # 全局单调：L1 兜底（含 ≥50ms 字宽）
    qc_l1_monotonic(result)

    from ..acoustic_quality import finalize_word_source_flags
    finalize_word_source_flags(
        result,
        fallback_lines=gate["fallback_lines"],
        engine_scored=True,
    )
    msg = f"[zh-ctc] 逐行窗对齐完成：{len(lines)} 行，回退 {len(gate['reverts'])} 行"
    try:
        print(f"   🎯 {msg}")
    except UnicodeEncodeError:
        print(f"   {msg}")
    return result


# ---------------------------------------------------------------------------
# 4) zh 出货验货（P0-4）：用粤语 CTC 模型给 zh 跨度打分
# ---------------------------------------------------------------------------
def verify_zh_delivery(
    data: Dict[str, Any],
    audio_path: str,
    device: str = "cuda",
    max_lines: int = 60,
) -> Optional[float]:
    """横向比较口径：用**粤语** CTC 模型给普通话逐字跨度打分，返回中位数。

    · 仅用于「原判 yue、熔断后由 zh 出货」这一条链路，绝不跨模型设绝对阈值；
    · 实测参考值：《那么骄傲》whisper 跨度 −15~−17，逐行 CTC −2.6~−5.1。
    """
    try:
        import torch
        import torchaudio.functional as F
        from ..acoustic_aligner import AcousticAligner
        from ..acoustic_canto import CantoneseCTCAligner, reconstruct_target_spans
        from ..utils.jyutping import to_jyutping
    except Exception as e:      # pragma: no cover
        logger.warning("   ⚠️ [zh-gate] 依赖缺失，跳过验货：%s", e)
        return None

    try:
        v_mono, sr = AcousticAligner.extract_vocals_mono(audio_path, device=device)
        import torchaudio
        v16 = torchaudio.functional.resample(
            torch.from_numpy(v_mono.astype(np.float32)).unsqueeze(0), sr, 16000
        ).squeeze(0).numpy()
    except Exception as e:
        logger.warning("   ⚠️ [zh-gate] 人声不可用，跳过验货：%s", e)
        return None

    canto = CantoneseCTCAligner.get_instance(device=device)
    try:
        canto._init_model_and_mapping()
    except Exception as e:
        logger.warning("   ⚠️ [zh-gate] 粤语模型不可用，跳过验货：%s", e)
        return None

    per_line: List[float] = []
    total_s = len(v16) / 16000.0
    for ln in (data.get("lines") or [])[:max_lines]:
        text = (ln.get("text") or "").strip()
        ws = [w for w in (ln.get("words") or []) if w.get("text")]
        if not text or len(ws) < 2:
            continue
        toks = [t for t in to_jyutping(text) if t.get("jyutping") in canto._syllable_to_id]
        if len(toks) < 2:
            continue
        t0 = max(0.0, ws[0]["start"] / 1000.0 - 0.30)
        t1 = min(total_s, ws[-1]["end"] / 1000.0 + 0.30)
        if t1 - t0 < 0.6:
            continue
        seg = v16[int(t0 * 16000): int(t1 * 16000)]
        try:
            lp = canto.forward_sliding_window(seg)
            frame_s = (t1 - t0) / max(1, int(lp.shape[1]))
            ids = [canto._syllable_to_id[t["jyutping"]] for t in toks]
            tgt = torch.tensor([ids], dtype=torch.int32, device=device)
            il = torch.tensor([int(lp.shape[1])], dtype=torch.int32, device=device)
            tl = torch.tensor([len(ids)], dtype=torch.int32, device=device)
            aligned, sc = F.forced_align(lp, tgt, il, tl, blank=0)
            spans = reconstruct_target_spans(aligned[0], sc[0], ids)
            vals = [float(s["score"]) for s in spans if s and s.get("score") is not None]
            if vals:
                per_line.append(float(np.mean(vals)))
        except Exception:
            continue

    if len(per_line) < 3:
        return None
    med = float(np.median(per_line))
    msg = f"[zh-gate] 粤语模型给 zh 跨度打分中位数 = {med:.2f}（{len(per_line)} 行）"
    try:
        print(f"   🔎 {msg}")
    except UnicodeEncodeError:
        print(f"   {msg}")
    return med


__all__ = [
    "ChineseCTCAligner",
    "MODEL_ID",
    "align_zh_windowed",
    "build_targets",
    "spans_to_words",
    "verify_zh_delivery",
]
