# -*- coding: utf-8 -*-
"""真实首唱识别与 A/B 前奏时序排版引擎 (v1.8.2 R3)。

满足《歌曲检测与修补标准》§4 全部要求及方案铁律：
1. 必须在 R2 制作名单清理完成后执行；
2. 真实首唱 T 严格取自首个真实演唱字块的 start_ms；
3. 首句缺字轴时禁止偷偷借用第二句时间替代，标记 FIRST_SING_UNVERIFIED；
4. A/B 模式严格按整数毫秒计算，禁止浮点四舍五入；
5. 词曲缺失分支精确处理，两项皆缺不输出空署名行；
6. 前奏排版为独立显示投影，绝对不修改真实演唱字块的时间轴与内容。
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..models.canonical_lyrics import (
    AuditIssue,
    CanonicalLyrics,
    IssueSeverity,
    LineRole,
    PreludePlan,
)


def find_first_singing_time(canonical: CanonicalLyrics) -> Tuple[Optional[int], Optional[str]]:
    """在清理后的正文中识别首个真实演唱字块的起点 T (毫秒)。
    
    返回 (T, error_code)。
    如果首句是真实演唱但缺少字块时间，返回 (None, "FIRST_SING_UNVERIFIED")。
    """
    for line in canonical.lines:
        # A preserved but unresolved line before the first confirmed singer
        # could itself be an earlier vocal/spoken line. Never leap over it
        # and mislabel the later singer as the first vocal time.
        if line.role == LineRole.UNKNOWN and line.text.strip():
            return None, "FIRST_SING_UNVERIFIED"
        # 排除非演唱行（标题、词曲、制作名单、译文、占位、纯角色独占提示）
        if not line.is_singing:
            continue
        
        # 找到首个具有真实演唱语义的行
        if not line.words:
            # 首个真实演唱句但没有字轴
            return None, "FIRST_SING_UNVERIFIED"
        
        # 从字块中寻找第一个有效（非停顿、文本非空）的字块
        for w in line.words:
            if not w.is_pause and w.text.strip():
                return w.start_ms, None
        
        # 全部为空或纯停顿，不允许当成实际首唱。
        return None, "FIRST_SING_UNVERIFIED"

    return None, "NO_SINGING_LINES"


def plan_prelude_ab(
    canonical: CanonicalLyrics,
    force_title: str = "",
) -> PreludePlan:
    """R3: 执行 A/B 前奏时间排版，生成独立的 PreludePlan 投影。
    
    所有时间计算采用纯整数运算，毫秒向下取整，严禁浮点误差。
    """
    t_val, err = find_first_singing_time(canonical)
    title = force_title or canonical.song.get("title", "") or ""
    lyricist = canonical.song.get("extracted_lyricist")
    composer = canonical.song.get("extracted_composer")

    if err == "FIRST_SING_UNVERIFIED":
        canonical.add_issue(
            code="FIRST_SING_UNVERIFIED",
            severity=IssueSeverity.REVIEW_REQUIRED,
            evidence="First singing line lacks verbatim word timestamps; fallback to second line is forbidden.",
            action="BLOCK_PRELUDE_PLANNING",
            blocking=True,
        )
        plan = PreludePlan(
            mode="NONE",
            t_sing_ms=0,
            title_window=(0, 0),
            display_title=title,
        )
        canonical.prelude = plan
        return plan

    if err == "NO_SINGING_LINES" or t_val is None:
        canonical.add_issue(
            code="NO_SINGING_LINES",
            severity=IssueSeverity.REVIEW_REQUIRED,
            evidence="No valid singing lines found in lyric data.",
            action="BLOCK_PRELUDE_PLANNING",
            blocking=True,
        )
        plan = PreludePlan(
            mode="NONE",
            t_sing_ms=0,
            title_window=(0, 0),
            display_title=title,
        )
        canonical.prelude = plan
        return plan

    T = int(t_val)
    if T <= 0:
        canonical.add_issue(
            code="PRELUDE_NO_ROOM",
            severity=IssueSeverity.REVIEW_REQUIRED,
            evidence=f"First singing starts at {T}ms, no room for prelude display.",
            action="SKIP_PRELUDE",
            blocking=False,
        )
        plan = PreludePlan(
            mode="NONE",
            t_sing_ms=T,
            title_window=(0, 0),
            display_title=title,
        )
        canonical.prelude = plan
        return plan

    # 规范化显示文本格式（全角冒号，无额外空格）
    disp_lyr = f"词：{lyricist}" if lyricist else None
    disp_cmp = f"曲：{composer}" if composer else None

    # A/B 判定边界：T > 20000ms 为 A，T <= 20000ms 为 B
    if T > 20000:
        mode = "A"
        title_window = (1500, 12000)
        if lyricist and composer:
            lyr_window: Optional[Tuple[int, int]] = (12000, 16000)
            cmp_window: Optional[Tuple[int, int]] = (16000, 20000)
        elif lyricist:
            lyr_window = (12000, 20000)
            cmp_window = None
        elif composer:
            lyr_window = None
            cmp_window = (12000, 20000)
        else:
            lyr_window = None
            cmp_window = None
    else:
        mode = "B"
        # 纯整数运算：b60 = (3 * T) // 5, b80 = (4 * T) // 5
        b60 = (3 * T) // 5
        b80 = (4 * T) // 5
        title_window = (0, b60)
        if lyricist and composer:
            lyr_window = (b60, b80)
            cmp_window = (b80, T)
        elif lyricist:
            lyr_window = (b60, T)
            cmp_window = None
        elif composer:
            lyr_window = None
            cmp_window = (b60, T)
        else:
            lyr_window = None
            cmp_window = None

    # At millisecond resolution, unavailable slots cannot be emitted as
    # zero-duration XML/ELRC. Merge the display text without changing singing.
    present = [(title_window, title)]
    if lyr_window and disp_lyr: present.append((lyr_window,disp_lyr))
    if cmp_window and disp_cmp: present.append((cmp_window,disp_cmp))
    if any(w[1] <= w[0] for w,_ in present):
        canonical.add_issue(
            code="PRELUDE_SLOTS_MERGED", severity=IssueSeverity.WARNING,
            evidence="Insufficient integer milliseconds for separate credits",
            action="MERGE_PRELUDE_DISPLAY", blocking=False,
        )
        title_window=(0,T)
        title=" | ".join(x for _,x in present if x)
        lyr_window=cmp_window=None
        disp_lyr=disp_cmp=None

    plan = PreludePlan(
        mode=mode,
        t_sing_ms=T,
        title_window=title_window,
        lyricist_window=lyr_window,
        composer_window=cmp_window,
        display_title=title,
        display_lyricist=disp_lyr,
        display_composer=disp_cmp,
    )
    canonical.prelude = plan
    return plan
