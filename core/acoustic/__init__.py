# -*- coding: utf-8 -*-
"""声学对齐子包（v1.6.0）。

设计约束：
  · **延迟导入**（PEP 562 `__getattr__`）—— 包导入本身零开销，也不触发
    `python -m core.acoustic.qc` 时的重复导入告警；
  · `qc` / `gate` 仅依赖 numpy，可在无 GPU、无模型的 CI 上跑静态质检；
  · `zh` 仅在被显式取用时才加载。
"""

_LAZY = {
    "Env": ("core.acoustic.qc", "Env"),
    "qc_l1_monotonic": ("core.acoustic.qc", "qc_l1_monotonic"),
    "qc_l2_rhythm": ("core.acoustic.qc", "qc_l2_rhythm"),
    "qc_l3_recheck": ("core.acoustic.qc", "qc_l3_recheck"),
    "judge_line": ("core.acoustic.gate", "judge_line"),
    "apply_gate": ("core.acoustic.gate", "apply_gate"),
}

__all__ = list(_LAZY.keys())


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'core.acoustic' has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0])
    attr = getattr(mod, target[1])
    globals()[name] = attr          # 缓存，后续访问不再走 __getattr__
    return attr


def __dir__():
    return sorted(list(globals().keys()) + __all__)
