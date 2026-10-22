---
name: music-tag-lyrics
description: 本地音频标签清洗与多源歌词修补（v1.2.5）。调度：酷狗 → AMLL → 网易 → LRCSHARE → LRCLIB → Demucs GPU。生成 Enhanced LRC / ELRC 双语（逐字只打原文、译文行级同轴）。遵守 18 条铁律：Live 互斥、先检后轴、宁缺毋滥。触发：歌词、标签、酷狗、AMLL、LRCSHARE、网易、LRCLIB、ELRC、Demucs、Live、铁律、Enhanced LRC。
---

# music-tag-lyrics: 音乐标签与逐字歌词原生聚合引擎 (v1.2.5)

纯 Python 技能：原地洗标签、多源抓词、同目录写出 `.spl` / `.lrc`。Python 侧需 `mutagen`；`numpy` 仅用于无逐字时的行级合成；Demucs GPU 为可选兜底。

> **不依赖 ffmpeg / Node / 播放器。** 绝不移动、归档或删除音频。`--lyrics-only` 只覆写内嵌歌词帧。

---

## 一、调度与写盘

```mermaid
flowchart TD
  Start[传入音频] --> Read[读标签与时长]
  Read --> Ladder["1 酷狗 → 2 AMLL → 3 网易 → 4 LRCSHARE → 5 LRCLIB"]
  Ladder --> HasMain{有主词 lines?}
  HasMain -->|有| Keep[采用当前源]
  HasMain -->|无| NeedTxt{同目录 txt 或 --lyrics-file?}
  NeedTxt -->|无| Block[红线阻断 不听写]
  NeedTxt -->|有且用户 Y| GPU[6 Demucs GPU 只打原文轴]
  NeedTxt -->|拒绝或非交互| Fail[本首失败 不降级 LRC]
  Keep --> Words{有逐字?}
  Words -->|无| Synth[numpy 按行合成 仅原文 words 非 GPU]
  Words -->|有| Valid[校验后写盘]
  Synth --> Valid
  GPU --> Valid
  Valid --> Out[".spl .lrc 内嵌三位一体"]
```

- 华语：酷狗已命中则不因「缺译文」覆盖。
- 外语：原文与译文必须同一源、同一版。酷狗/AMLL 只有原文时，若网易同时有原文 + 官方 tlyric，**整包换成网易**；禁止把网易 tlyric 挂到酷狗轴上。仍无译文则保持纯原文。
- Demucs **不是第六个抓词 API**：仅全源无 `lines` 后才询问；须本地权威文本；`--non-interactive` 跳过。拒绝 = 整首失败，不是降级逐行 LRC。
- 有行级词、无逐字：默认 numpy 合成，不是每首都上 GPU。

安装与 CLI 见 `README.md`。

---

## 二、18 条避坑铁律

### 1. 乐器长尾奏（Outro）识别防假截断铁律
- **铁律准则**：严禁粗暴以时长差判定残缺。必须以唱词语义完整性、尾段副歌重现度、歌手真实停唱点综合判定，保护自然华彩尾奏。

### 2. 多歌手 `/` 连接符与乐队白名单保护铁律
- **铁律准则**：`BAND_WHITELIST` 锁定乐队名。仅对真正的独立多合唱艺人把 `&` 等改为 `/`。

### 3. 非国语标准 ELRC 动态流光与防跳屏铁律
- **铁律准则**：
  1. 译文副行必须继承对应原唱行的 `[mm:ss.xx]`，紧随其下；
  2. 副行纯文本，严禁 `<>` 逐字；合成或 GPU 打轴都**只给原文写 `words`**；
  3. 无官方译文则保持纯原文（协同 Rule 13）。

### 4. 声学自然度门禁防挤压铁律
- **铁律准则**：人类单字/单词通常 200ms~800ms。严禁 ≤80ms 机器伪时间戳。平滑算法不得压缩真实声学时长。

### 5. 外文经典金曲 Apple Music 官方母带逐字直连铁律
- **铁律准则**：非国语欧美热单优先 AMLL（Apple Music TTML）。

### 6. 三位一体国际标准 Enhanced LRC 与 100% SHA-256 镜像铁律
- **铁律准则**：内嵌 lyrics == 同级 `.spl` == 同级 `.lrc`，统一 Enhanced LRC：`[mm:ss.xx]<mm:ss.xx>w1<t2>...<tend>`。

### 7. 跨平台换行符确定性对齐铁律
- **铁律准则**：歌词文件写入显式 `newline="\n"`，保证 `sha256(embedded) == sha256(.spl) == sha256(.lrc)`。

### 8. 标点符号 0ms 冗余清洗铁律
- **铁律准则**：折叠 0ms 重复标点时间戳，标点依附相邻词。

### 9. Live 演出版与录音室版本强物理互斥防串扰铁律
- **铁律准则**：
  1. 本地 Live：候选必须带 Live/现场，拒绝录音室词；
  2. 本地录音室：拦截含 Live/现场 的候选；
  3. Hash 无果则 KRCS 关键词 + 时长降级；
  4. 版本括号参与匹配，营销垃圾括号清除。

### 10. 全链路 [offset] / adjust 叠加与前奏异常门禁铁律
- **铁律准则**：
  1. 解析行/字时间戳强制 `total_offset = offset + adjust`，`>= 0` 箝位；
  2. LRC 多时间戳行逐个保留，0 丢行；
  3. 常规曲（>90s）首句非报幕起唱 >32s 发出 Intro Gate 警告。

### 11. 【核心红线】绝不语音听写，必须先检后轴铁律
- **铁律准则**：**严禁 ASR 盲目听写。**
  1. Demucs 前必须已有权威逐行文本（同目录 txt 或 `--lyrics-file`）；
  2. Whisper 只做 Forced Alignment，不得改词；
  3. 无文本立即阻断退出。拒绝授权或 `--non-interactive` = 本首失败。

### 12. 双端声学能量门禁与间隙防拉伸铁律
- **铁律准则**：短窗隔离 ≥2.5s 间奏；行首 15ms 逆向 Onset；行末 20ms 正向 Offset（RMS < 0.015）；字间停顿 ≥300ms 插入闭合标签。

### 13. 【核心红线】绝不机械机翻铁律
- **铁律准则**：**禁止机翻补译文。** 只采纳官方/人工审校文本（网易 `tlyric` 等）。无官方译文则纯原文。译文必须与原文同一源同一版。

### 14. 外文词界空格与 `\xa0` 消融铁律
- **铁律准则**：西文词间保留 ASCII 空格；全流程 `replace('\xa0', ' ')`；标点紧随词末。

### 15. 【核心红线】酷狗 KRC 语义翻译与音译防混淆硬门禁
- **铁律准则**：仅 `type == 1` 且通过汉字检测才算译文；丢弃 `type: 0` 罗马音/假名。

### 16. 【核心红线】纯音乐占位防篡改与有效行数门禁
- **铁律准则**：含「纯音乐 / 伴奏 / Instrumental / No Lyrics」等占位则拒绝；常规声乐（>60s 且原词 ≥10 行）不得被 ≤6 行覆盖；译文行数低于 `max(4, 0.3 × 原词行数)` 熔断。

### 17. 【核心红线】物理时长严密邻近与版本防串扰门禁
- **铁律准则**：分源容差**不可统一**。LRCSHARE 末行 ±5s；酷狗元数据 ±10s、解析末行 ±8s；网易/LRCLIB 搜索元数据 ±35s。偏差 >8s 不得仅因「含译文」加分；>15s 剔除。

### 18. 【核心红线】黄金基准时间轴锚定与动态微调门禁
- **铁律准则**：
  1. 主行 start/end/words 不可被译文改写；
  2. aggregator 译文挂载窗口为 **1500ms 最近邻**（LRCSHARE 内部仍 2000ms，不要混用）；
  3. 外语缺译文时用同一源整包替换，禁止跨源把 tlyric 挂到酷狗轴上。

---

## 三、音频内嵌元数据治理

| 元数据字段 | 标准化治理规范 | 严禁项与过滤规则 | 治理执行范例 |
| :--- | :--- | :--- | :--- |
| **Title（标题）** | 纯净歌曲原名，保留官方副标题括号 | 剔除 `[FLAC-无损]`、`【HQ高品质】`、宣发尾缀 | `Closer [简明宣发版...]` → `Closer` |
| **Artist（艺术家）** | 多合唱强制 `/` | 严禁误拆白名单乐队 | `徐良 & 小凌` → `徐良/小凌`；`伍佰 & China Blue` 原样 |
| **Album（专辑）** | 发行母带所属专辑 | 严禁拼盘/热歌榜名 | 归档至发行专辑 |
| **Year（年份）** | 4 位公历 `YYYY` | 严禁月日或空白充数 | `2016-08-01` → `2016` |
| **Lyricist / Composer** | 真名，`/` 分隔 | 已有则白名单保护 | `作词 : 竹君` |
| **Lyrics（内嵌歌词）** | Enhanced LRC，与 `.spl`/`.lrc` 镜像 | 译文不得独立时间戳插队 | FLAC/WAV/MP3/M4A/OGG/DSF |

---

## 四、Demucs v4 GPU 兜底

技能包不内置 4GB 模型。仅当梯子无 `lines` 时进入。

**阻断（无本地权威文本）：**

```text
🛑 [Demucs v4 绝对红线阻断 (v1.2.5)]
未能从商业曲库检索到《歌曲 - 歌手》的有效歌词，且本地未提供纯文本歌词。
⚠️ 核心铁律：【绝不语音听写】！请在歌曲目录放置《歌曲 - 歌手.txt》后再重试。
```

**授权（已有文本）：**

```text
🔔 [Demucs v4 GPU 音频剥离与声学打轴授权提示 (v1.2.5)]
已定位到权威歌词文本（共 X 行）。
是否授权按需使用本地 GPU 调用 Demucs v4 剥离纯人声并进行双端能量门禁逐字声学咬合？
[Y: 授权同意运行 GPU 打轴 / N: 拒绝授权并跳过]
```

Y：干声分离后 Forced Alignment；外语先配对剥离译文，只对齐原文，再把译文挂回 `translation`。N 或 `--non-interactive`：本首失败，0 下载。

---

## 五、模块入口

- CLI：`scripts/process_song.py`
- 标签：`scripts/tag_editor.py`
- 调度：`core/aggregator.py`（酷狗 → AMLL → 网易 → LRCSHARE → LRCLIB）
- GPU / 合成：`core/acoustic_aligner.py`（GPU 仅失败分支；numpy 合成 ≠ GPU）
- 解析：`core/parsers/`；抓取：`core/fetchers/`
- 共享：`core/utils/`（http / time_utils / version_guard / text_utils / io_safe）
- 离线测：`tests/test_parsers.py`、`test_pipeline.py`、`test_m1_safety.py`

仓库：`https://github.com/sxsxhhi/music-tag-lyrics`
