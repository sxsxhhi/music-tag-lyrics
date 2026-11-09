# -*- coding: utf-8 -*-
"""CanonicalLyrics 统一中间表示与审计数据模型 (v1.8.2)。

满足《歌曲检测与修补标准》及 v1.8.2 实施方案要求：
1. 细粒度 LineRole 分类与语义角色标记；
2. 保持真实演唱字块 words[] 绝对不可变契约；
3. 结构化审计问题与修补动作记账；
4. 严格解耦显示用 prelude 与真实演唱时序。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class LineRole(str, Enum):
    """歌词行语义角色分类。"""
    TITLE = "TITLE"                          # 歌曲标题行
    LYRICIST = "LYRICIST"                    # 作词署名
    COMPOSER = "COMPOSER"                    # 作曲署名
    PRODUCTION_CREDIT = "PRODUCTION_CREDIT"  # 制作名单（编曲/混音/母带/制作人/鸣谢等）
    PERFORMER_CUE = "PERFORMER_CUE"          # 演唱角色提示（男：/女：/合：/歌手名：）
    SINGING = "SINGING"                      # 真实演唱主行
    SPOKEN_CONTENT = "SPOKEN_CONTENT"        # 真实念白/对白
    SAMPLE_EXPLANATION = "SAMPLE_EXPLANATION"# 采样/引用来源说明
    TRANSLATION = "TRANSLATION"              # 独立译文行
    PLACEHOLDER = "PLACEHOLDER"              # 纯音乐/伴奏等占位
    UNKNOWN = "UNKNOWN"                      # 歧义/表意不明（保留并人工复核）


class IssueSeverity(str, Enum):
    """审计事件严重等级。"""
    INFO = "INFO"
    WARNING = "WARNING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ERROR = "ERROR"


@dataclass
class AuditIssue:
    """结构化审计问题。"""
    code: str
    severity: IssueSeverity
    line_id: Optional[str] = None
    source_index: Optional[int] = None
    evidence: str = ""
    action: str = ""
    blocking: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "line_id": self.line_id,
            "source_index": self.source_index,
            "evidence": self.evidence,
            "action": self.action,
            "blocking": self.blocking,
        }


@dataclass
class CanonicalWord:
    """逐字演唱字块（严格保持声学时间轴）。"""
    text: str
    start_ms: int
    end_ms: int
    join: Optional[str] = None
    is_pause: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "text": self.text,
            "start": self.start_ms,
            "end": self.end_ms,
        }
        if self.join is not None:
            d["join"] = self.join
        if self.is_pause:
            d["is_pause"] = True
        return d


@dataclass
class CanonicalLine:
    """标准歌词逻辑行。"""
    stable_id: str
    source_index: int
    role: LineRole
    start_ms: int
    end_ms: int
    text: str
    words: List[CanonicalWord] = field(default_factory=list)
    translation: Optional[str] = None
    translation_source: Optional[str] = None
    speaker_prefix: Optional[str] = None
    standalone_role_text: Optional[str] = None
    synthetic: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_singing(self) -> bool:
        """是否属于真实演唱或真实对白。"""
        return (self.role == LineRole.SINGING or
                (self.role == LineRole.SPOKEN_CONTENT and not self.metadata.get("original_singer")))


@dataclass
class PreludePlan:
    """前奏 A/B 排版方案。"""
    mode: str  # "A", "B", "NONE"
    t_sing_ms: int
    title_window: Tuple[int, int]
    lyricist_window: Optional[Tuple[int, int]] = None
    composer_window: Optional[Tuple[int, int]] = None
    display_title: str = ""
    display_lyricist: Optional[str] = None
    display_composer: Optional[str] = None
    issues: List[AuditIssue] = field(default_factory=list)


@dataclass
class CanonicalLyrics:
    """统一规范歌词容器。"""
    song: Dict[str, Any] = field(default_factory=dict)
    prelude: Optional[PreludePlan] = None
    lines: List[CanonicalLine] = field(default_factory=list)
    issues: List[AuditIssue] = field(default_factory=list)
    raw_digest: str = ""

    def add_issue(
        self,
        code: str,
        severity: IssueSeverity,
        line_id: Optional[str] = None,
        source_index: Optional[int] = None,
        evidence: str = "",
        action: str = "",
        blocking: bool = False,
    ) -> None:
        self.issues.append(
            AuditIssue(
                code=code,
                severity=severity,
                line_id=line_id,
                source_index=source_index,
                evidence=evidence,
                action=action,
                blocking=blocking,
            )
        )

    def singing_words_hash(self) -> str:
        """计算全曲真实演唱字块的 SHA-256 指纹，用于校验正文字块绝对不变。"""
        import hashlib
        h = hashlib.sha256()
        for line in self.lines:
            if line.is_singing:
                for w in line.words:
                    h.update(f"{w.text}|{w.start_ms}|{w.end_ms}|{w.join}|{w.is_pause}\n".encode("utf-8"))
        return h.hexdigest()


def word_boundary_space(previous: CanonicalWord, current: CanonicalWord) -> bool:
    """Compat: old TTML parser stores join on previous token; v1.8.2 may
    store it on current. Both 'space' and literal ' ' represent a word gap."""
    return (previous.join in ("space", " ") or current.join in ("space", " ")
            or previous.text.endswith(" ") or current.text.startswith(" "))
