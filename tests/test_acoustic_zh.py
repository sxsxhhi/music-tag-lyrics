# -*- coding: utf-8 -*-
"""中文 CTC token 映射单测（v1.6.0）——纯函数，不需要模型/GPU。

重点：词表只有大写 A–Z、无数字无标点；标点并入前一词后 `宮?` 两字符只对应
1 个 token，绝不能按字符数推算索引；红线 1（不改词）必须有断言兜底。
"""
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _testkit import main, skip  # noqa: E402
from core.acoustic.qc import Env, MIN_WORD_MS  # noqa: E402
from core.acoustic.zh import build_targets, spans_to_words  # noqa: E402

FAKE_VOCAB = {c: 100 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
FAKE_VOCAB.update({"中": 50, "文": 51, "皇": 52, "宫": 53, "後": 54, "大": 55, "道": 56})


def _mk_words(*texts):
    return [{"text": t} for t in texts]


def test_build_targets_uppercase_fallback():
    # "Say" → S/a/y 全部经 upper 回退命中； owner 逐字符
    ids, owner = build_targets(_mk_words("Say"), FAKE_VOCAB)
    assert ids == [FAKE_VOCAB["S"], FAKE_VOCAB["A"], FAKE_VOCAB["Y"]], ids
    assert owner == [0, 0, 0]


def test_build_targets_digits_and_punct_skipped():
    # "2000"（数字）与"，"（标点）词表均无 → 不产生 token
    ids, owner = build_targets(_mk_words("中", "2000", "，"), FAKE_VOCAB)
    assert ids == [FAKE_VOCAB["中"]]
    assert owner == [0]


def test_build_targets_multi_word_owner():
    ids, owner = build_targets(_mk_words("中", "Say", "文"), FAKE_VOCAB)
    assert owner == [0, 1, 1, 1, 2]
    assert len(ids) == len(owner) == 5


def test_spans_to_words_identity_red_line():
    # 红线 1：文本一字不动、join 恒等
    words = _mk_words("皇", "后", "大", "道")
    starts = [10.0, 10.3, 10.7, 11.0]
    out = spans_to_words(words, starts, [0, 1, 2, 3], 9900, 12500, None)
    assert [w["text"] for w in out] == ["皇", "后", "大", "道"]
    assert "".join(w["text"] for w in out) == "皇后大道"
    assert out[0]["start"] == 10000
    assert out[1]["end"] == out[2]["start"]          # 终点 = 下一词起点
    assert all(w["duration"] >= MIN_WORD_MS for w in out)


def test_spans_to_words_punct_word_gets_no_ctc():
    # '宮?'：两字符只对应 1 个 token；'?‘ 无 token → 吸附为 no_ctc 词
    words = _mk_words("皇", "宮?")
    starts = [10.0, 10.4]
    owner = [0, 1]                                   # 皇→0，宮→1（? 不进 CTC）
    out = spans_to_words(words, starts, owner, 9900, 12500, None)
    assert [w["text"] for w in out] == ["皇", "宮?"]
    assert out[0]["end"] == out[1]["start"]          # 恒等式：终点=下一词起点
    assert "no_ctc" not in out[0] or not out[0].get("no_ctc")
    assert all(w["duration"] >= MIN_WORD_MS for w in out)


def test_spans_to_words_digits_word_absorbed():
    words = _mk_words("中", "2000", "文")
    ids, owner = build_targets(words, FAKE_VOCAB)
    assert owner == [0, 2]                            # 数字词零 token
    starts = [10.0, 10.5]
    out = spans_to_words(words, starts, owner, 9900, 12500, None)
    assert out[1].get("no_ctc") is True
    assert out[1]["start"] == out[0]["end"]           # 吸附在前词终点
    assert "".join(w["text"] for w in out) == "中2000文"


def test_spans_to_words_last_word_release_cap():
    env = Env.from_vocals(__import__("numpy").zeros(44100 * 30, dtype="float32"), 44100)
    words = _mk_words("中", "文")
    out = spans_to_words(words, [10.0, 10.4], [0, 1], 9900, 11500, env)
    # cap = next_tag - GAP = 11450；release_end 不得超过
    assert out[-1]["end"] <= 11450, out[-1]
    assert out[-1]["end"] - out[-1]["start"] >= MIN_WORD_MS


def test_spans_to_words_all_zero_token_returns_none():
    out = spans_to_words(_mk_words("2000", "，"), [], [], 9900, 12500, None)
    assert out is None


def test_spans_to_words_monotonic_inside_line():
    words = _mk_words("中", "文", "皇", "宫")
    starts = [10.0, 10.2, 10.25, 10.3]                # 两个过近的 token
    out = spans_to_words(words, starts, [0, 1, 2, 3], 9900, 12500, None)
    for a, b in zip(out, out[1:]):
        assert b["start"] >= a["end"] - 1 or b["start"] >= a["start"], (a, b)


if __name__ == "__main__":
    sys.exit(main(globals()))
