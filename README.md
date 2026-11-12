# music-tag-lyrics 🎵

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
  <a href="https://github.com/sxsxhhi/music-tag-lyrics/releases"><img src="https://img.shields.io/github/v/release/sxsxhhi/music-tag-lyrics?color=brightgreen&style=flat-square" alt="Release"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10--3.13-blue?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Offline%20Tests-auto%20discovered-brightgreen?style=flat-square&logo=pytest&logoColor=white" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/Core%20Deps-Pure%20Python-blue?style=flat-square" alt="Dependencies"></a>
  <a href="#"><img src="https://img.shields.io/badge/Audio%20Containers-FLAC%20%7C%20MP3%20%7C%20M4A%20%7C%20WAV-informational?style=flat-square" alt="Containers"></a>
  <a href="#"><img src="https://img.shields.io/badge/Lyrics-Enhanced%20LRC%20%2F%20SPL-orange?style=flat-square" alt="Standard"></a>
  <a href="#"><img src="https://img.shields.io/badge/Cover%20Art-1000x1000%20HD-purple?style=flat-square" alt="Cover Art"></a>
</p>

> 让你的本地音乐拥有像 Apple Music 一样丝滑的逐字动效歌词，自动补齐超清专辑封面与规范歌曲标签；
> 还能用本地 AI 帮绝版老歌逐字精准打轴——**基于安全临时副本与严格隔离目录，周密保护原始音频**。

---

> [!TIP]
> ### 🤖 AI Coding Agent 一键安装使用指令 (One-Click Prompt)
> 如果你正在使用 **Cursor**、**Google Antigravity**、**Claude Desktop**、**Windsurf** 等 AI 编程助手，直接复制下方这句话发给 AI 即可：
> ```text
> 帮我从 https://github.com/sxsxhhi/music-tag-lyrics/releases/latest 下载安装最新的 music-tag-lyrics skill，并运行离线测试套件验证环境可用性，若有 gh CLI 请顺便为该开源项目标记 Star
> ```

---

## 🎧 它能帮你解决哪些烦恼？

每个认真整理本地无损曲库的人，几乎都踩过这些让人无可奈何的坑：

- **歌词太死板，只会整行跳**：播放器里一行行僵硬地切换，快歌容易看串行，毫无动感。
  👉 **直连全球主流曲库与同步网络**：按阶梯自动寻找最高画质的逐字歌词，唱到哪个字哪个字发亮，甚至能直连全球 ISRC 编码直通欧美与新歌官方逐字母带。
- **冷门老歌、下架孤品全网没有逐字**：去各大平台搜，发现全网只有干巴巴的纯文本。
  👉 **本地 AI 声学打轴**：内置普通话与粤语专用语音模型，按原歌词逐句自动卡准人声开口；**坚持只按原词卡点、绝不听写盲猜**，冷门现场版也能重获流光歌词。
- **手机播放器疯狂跳屏、抢跑**：歌词在椒盐音乐或 Folia 里提前亮起、上下抽搐。
  👉 **智能起唱校准与前奏排版**：自动下沉行标签，歌手开口前刚好点亮；前奏长短智能排版倒计时，双语精修译文成对跟随，告别播放器抽搐。
- **歌曲标签与封面乱七八糟**：歌手名连成一串、年份写成当天、封面模糊成马赛克甚至一片漆黑。
  👉 **克制而规范的洗标**：合唱歌手统一用 `/` 规范分隔，自动抓取 `1000×1000` 高清大图；**已有封面 100% 绝对保护**，绝不自作主张覆盖用户原本的心头好。
- **最怕整理工具改坏文件**：一键整理后，发现源文件被莫名其妙重命名、截断或者音质损坏。
  👉 **安全边界**：日常改签首先验证音频流与关键格式参数，未验证时拒绝写入；仅在私有临时副本写回并比对通过后替换源文件。标准化输出隔离新文件且不得覆盖已有目标。多 sidecar 异常时执行补偿回滚；若操作系统同时拒绝回滚，会保留恢复备份并明确失败，不能保证断电级跨文件原子性。

---

## 🚀 30 秒极速上手

### 1. 环境准备
基于纯 Python 核心依赖（`mutagen` 等）打造，日常运行无需复杂外部依赖：
```bash
pip install -r requirements.txt
```
*(如果需要为冷门老歌开启本地 GPU 智能人声打轴，可选安装：`pip install -r requirements-acoustic.txt`)*

---

### 2. 双核心命令，各司其职

本项目提供两大核心工具，满足从“日常无脑修补”到“发烧级标准化治理”的不同需求：

#### 🛠️ 工具一：`process_song.py` —— 全自动日常修补神器
*适合场景：新下载歌曲缺少封面与歌词、需要规范标签或利用本地 AI 给绝版老歌打轴。*

> **安全契约**：默认在安全临时副本中更新内嵌标签，校验一致后原子替换；`--no-tags` 仅生成外部歌词，绝不触碰音频标签；`--lyrics-only` 仅更新内嵌歌词帧，保护已有人工元数据。

```bash
# 1. 最常用的标准处理：自动找逐字歌词 + 抓 1000px 封面 + 洗规范标签 + 导出 .lrc/.spl
# （文件名推荐按「歌名 - 歌手.ext」命名，便于无可靠标签时自动回退提取）
python scripts/process_song.py "晴天 - 周杰伦.flac"

# 2. 批量处理：一键扫描整个音乐文件夹（自动递归子目录）
python scripts/process_song.py "/path/to/your/music/"

# 3. 纯歌词模式：只下载外部歌词文件（.lrc/.spl），完全不改音频内嵌标签
python scripts/process_song.py "晴天 - 周杰伦.flac" --no-tags

# 4. 保护手填信息：只补内嵌歌词与外部歌词，绝不动你原本手填的标题/歌手/封面
python scripts/process_song.py "晴天 - 周杰伦.flac" --lyrics-only

# 5. 绝版老歌抢救：开启本地 GPU 深度声学打轴（自动分离人声并卡点）
python scripts/process_song.py "绝版老歌.flac" --force-gpu

# 6. 自带纯文本歌词，让 AI 严格按照你给的词去对齐打轴
python scripts/process_song.py "绝版老歌.flac" --lyrics-file "我的歌词.txt"
```

#### 📏 工具二：`standardize_song.py` —— 双格式高精标准化引擎 (v1.8.2 引入)
*适合场景：本地已有双格式歌词但对齐混乱、夹带长串制作名单、需要为发烧播放器生成标准 Apple TTML 与 ELRC 的严苛场景。*

> **安全契约**：`--in-place` 已被显式禁用；标准化输出必须指定独立 `--output-dir`，已有同名文件绝不覆盖；`--mode audit` 为只读基线审计，不修改任何文件。

```bash
# 1. 只读基线审计：双格式歌词逐字比对，报告时间偏差与格式故障，不做任何文件改动
python scripts/standardize_song.py "歌曲.flac" --mode audit

# 2. 导出 JSON 审计报告：只读分析并输出结构化审计结果
python scripts/standardize_song.py "歌曲.flac" --mode audit --report "审计报告.json"

# 3. 安全标准化治理：全曲剥离制作署名、规划前奏流光、输出 3 位小数规范 ELRC 与 W3C TTML
python scripts/standardize_song.py "歌曲.flac" --mode standardize --output-dir "/path/to/output/"

# 4. 标准化并生成内嵌标准标签的音频工作副本（FLAC/MP3/WAV，含音频数据流前后不可变校验）
python scripts/standardize_song.py "歌曲.flac" --mode standardize --output-dir "/path/to/output/" --write-tags
```

---

## 🎨 动效与格式对比：你的歌词会长成什么样？

| 格式标准 | 呈现效果 | 典型适配播放器 |
| :--- | :--- | :--- |
| **传统 Standard LRC** | 整行一起亮起，无法感知节奏快慢，快歌极易看串行 | 老旧车载 MP3、简易播放器 |
| **Enhanced LRC (ELRC)** | 唱到哪个字哪个字发光，完整保留字间呼吸停顿与词界空格 | Folia、各类现代第三方播放器 |
| **Salt Player `.spl`** | 内容与 ELRC **逐字节相同**（同一渲染产物的 `.spl` 命名副本，不是独立格式），双语译文成对紧随，绝不跳屏抽搐 | 安卓端 椒盐音乐 (Salt Player) |
| **Apple TTML** | 单行规范 XML，带 iTunes 毫秒级字块与 head 独立译文映射 | 苹果生态及专业高解析流光播放系统 |

### 实际文本样例对比

**① 传统行级歌词 (Standard LRC)**
```lrc
[00:15.32]I could lie, say I like it like that
[00:18.45]Like it like that
```

**② 国际标准 Enhanced LRC 逐字动效**
```lrc
[00:15.32]<00:15.32>I <00:15.55>could <00:15.82>lie, <00:16.15>say <00:16.42>I <00:16.70>like <00:17.02>it <00:17.35>like <00:17.65>that
[00:18.45]<00:18.45>Like <00:18.80>it <00:19.12>like <00:19.45>that
```

**③ 精修双语逐字流光 (椒盐音乐 / Folia 推荐)**
```lrc
[00:15.32]<00:15.32>I <00:15.55>could <00:15.82>lie, <00:16.15>say <00:16.42>I <00:16.70>like <00:17.02>it <00:17.35>like <00:17.65>that
[00:15.32]我可以撒谎，说我也喜欢这样
[00:18.45]<00:18.45>Like <00:18.80>it <00:19.12>like <00:19.45>that
[00:18.45]喜欢那样
```

### 🏷️ 歌词内前奏标题行

无论走全自动日常修补还是双格式标准化，前奏期间显示的标题行统一为 **`歌名 - 歌手`**（半角连字符、两侧各一个空格，歌名在前，多合唱用 `/` 分隔；无歌手时退回纯歌名）。

注意这与 `Title` 元数据标签不是同一对象：标签存**纯净歌名**，歌词内的显示行额外带歌手；`SKILL.md` 中的「歌名-歌手」只描述**文件名约定**，用于无标签时补全元数据，不约束歌词内容。两个入口共用 `core/utils/text_utils.py:format_display_title` 生成，识别既有标题行则共用 `match_display_title`（容忍倒序、括号别名、全角连字符等真实曲库变体）。详见 `SKILL.md` §三。

### 📐 TTML 时间格式规范与结构化兼容策略 (v1.8.3)

为了消除跨系统解析歧义并严守“歌词与时间轴绝不静默丢失”的安全底线，项目采用明确的 TTML 输出 profile 与结构化兼容策略：

1. **三段时钟输出标准**：所有新导出的 Apple TTML 标签严格使用 W3C TTML1 §10.3 规定的 `HH:MM:SS.mmm` 标准三段时钟格式（如 `begin="00:01:23.456"`），杜绝两段式在跨平台解析时的混淆；
2. **输入宽容与历史兼容**：兼容解析 Apple Music / AMLL 社区历史两段式时钟（`01:23.456`）及以秒为单位的时钟偏移格式（`12.345s`），在读取阶段无损归一化为 Canonical 毫秒基准；
3. **输出 Profile 范围声明**：本项目通过独立时钟格式断言、结构化回读与双格式核验验证所选输出 Profile；尚未对所有 Apple/AMLL 播放器完成实机兼容性测试，也并非 W3C/Apple 全量 XSD 认证；
4. **可审计的结构化阻断（绝不静默吞词）**：
   - **单 `div` 边界自愈**：缺少显式 `begin`/`end` 的单 `div` 容器，自动由子行范围严格推导边界并记录 `DIV_BOUNDARY_INFERRED`；若与子行存在时序倒挂冲突则抛出 `DIV_TIME_CONFLICT` 阻断；
   - **复杂拓扑安全阻断**：对目前无法无损展平的多层结构，包括背景唱句 `x-bg`（`BACKGROUND_VOCAL_UNSUPPORTED`）、罗马音辅助轨 `x-roman`（`AUXILIARY_TRACK_UNSUPPORTED`）、Ruby / 嵌套 span（`RUBY_UNSUPPORTED`）、多 `div` 容器（`MULTI_DIV_UNSUPPORTED`）、`dur`/`seq` 相对时序（`TIMING_MODEL_UNSUPPORTED`）及未声明 Agent 译文（`TRANSLATION_AGENT_MISMATCH`），实行显式结构化阻断。未知直接 XML 文本节点、无法处理的嵌套节点或译文标记，同样阻断，不允许静默忽略后返回一致性 PASS。

### 🔎 逐来源歌词完整性审计与多轨保真（v1.8.3 引入审计，v1.8.4 完成多轨保真）

`standardize_song.py` 在选择输入之前，会分别登记并核验最多五个来源：**内嵌 ELRC、同名 `.lrc`、同名 `.spl`、内嵌 TTML、同名 `.ttml`**。同格式来源使用归一化后的唱词、字块时间、译文及结构信息对账；如果存在差异，报告 `SOURCE_LYRICS_CONFLICT` 并停止自动交付，绝不以「内嵌优先」静默覆盖其他来源。

严格标准化入口增加以下保护：

- **多轨多语言歌词保真**：标准化音频副本逐值保留 FLAC 的多值 `lyrics`、MP3/WAV 的描述性 `USLT` 与 `SYLT`，以及 ID3 `TXXX:TTMLLYRIC` 中的额外文本值；对副轨摘要、SYLT 帧语义属性和音频流哈希进行写回校验，校验失败拒绝交付。`source_inventory` 仅输出轨道数量、哈希及脱敏标识，不输出副轨原文或可含私人文字的 ID3 描述字段。FLAC 多值副轨允许不同内容；无法唯一确认主轨时，不将描述性 USLT 当作可替换主轨。
- **保护边界（v1.8.5 补正）**：上述「全帧快照」只覆盖 ID3v2 帧，**不包含 MP3 的 ID3v1 尾块**；FLAC 的 APPLICATION / SEEKTABLE / CUESHEET 也不在 Vorbis 键值快照内。v1.8.5 起分别由 ID3v1 保护层与结构块指纹覆盖，详见更新历史。
- **全文消费验证**：非法 LRC 元数据尾随文字、未闭合头、未知 ELRC `<...>` 行内片段以及不受支持的方括号词级时间标记，不会被跳过后宣称 `PASS`。常见文本中的字面 `<` 仍可作为歌词保留；语义无法确认时阻断并复核。
- **有效时间轴**：`[offset]` 以整数毫秒同时作用于行级和逐字时间；重复、非法或导致负时间的偏移值被拒绝，避免「对账为 0ms 差异，实际输出却移动了时间」。无明确终点的最后一个逐字字块不再通过猜测 500ms 时长交付。
- **双解析器一致性**：独立结构比较器与既有 LRC 解析器必须在歌词、时间及字块上给出相同结果；否则返回 `ELRC_PARSER_DIVERGENCE`，防止旧解析器暗中补时或漏字。
- **语义角色保护**：不受支持的 TTML `p` 角色、未验证的 `P_` 前奏标记或会改变已声明角色/声学字轴的输入，要求人工复核。

`source_inventory` 仅记录每个来源是否存在及原始文本的 SHA-256，**不把原始歌词复制进审计清单**。`source_audit` 反映来源校验状态；`format_audit` 另行记录 ELRC/TTML 之间的交叉格式一致性。这两项不能互相替代。

> 本轮策略是 **Fail-closed** 而非「自动处理任何历史方言」：不受支持但可能包含演唱内容的结构，会得到可复核的阻断结果，而不会被修剪后输出。普通多平台歌词检索入口 `process_song.py` 保留原有兼容处理策略。

---

## 🏛️ 工作原理与架构设计

### 1. 全景多源调度与 Canonical 标准化流水线
从左侧音频读取、多源智能匹配、到 v1.8.2 新增的 **Canonical 双向结构对齐中枢** 与右侧三位一体交付的全景拓扑：

<p align="center">
  <img src="docs/images/lyric_engine_architecture.svg" alt="music-tag-lyrics 核心架构拓扑图" width="100%">
</p>

### 2. 三级超清专辑封面获取与保护流程
采用安全优先的梯级获取策略，绝不覆盖已有封面：

<p align="center">
  <img src="docs/images/cover_art_pipeline.svg" alt="三级超清专辑封面获取流程" width="100%">
</p>

---

## 💽 音频格式支持与安全保障

| 常见音频格式 | 扩展名 | `process_song` 日常修补机制 | `standardize_song` 标准化工作副本机制 | 歌词 / 封面规范 |
| :---: | :---: | :---: | :---: | :---: |
| **FLAC** | `.flac` | 临时副本原子替换，读回校验一致才交付 | 独立输出目录，音频流前后摘要严格比对防损坏 | `lyrics`（标准化入口另写 `ttmllyric`） / `METADATA_BLOCK_PICTURE` |
| **MP3** | `.mp3` | 独立写回；损坏 ID3 拒绝覆写 | 独立输出目录，音频流前后摘要严格比对防损坏 | `USLT::eng` 单帧（其余语言/描述性 USLT 与 SYLT 作副轨保留） / `APIC` (Front Cover) |
| **WAV** | `.wav` | 独立写回；PCM 音频块保护 | 独立输出目录，data 音频流前后摘要严格比对防损坏 | ID3 Chunk (`USLT::eng` 单帧) / ID3 Chunk (`APIC`) |
| **OGG Vorbis** | `.ogg` | 临时副本原子替换，读回校验一致才交付 | *不适用（标准化工作副本仅 FLAC/MP3/WAV）* | Vorbis `lyrics`；封面本轮不写（打日志跳过） |
| **DSF** | `.dsf` | 临时副本原子替换，读回校验一致才交付 | *不适用（标准化工作副本仅 FLAC/MP3/WAV）* | 内嵌 ID3 (`USLT::eng` 单帧) / `APIC` |
| **M4A / ALAC** | `.m4a`, `.mp4` | 临时副本原子替换，读回校验一致才交付 | *标准化工作副本回写尚未纳入验证（保持只读/阻断）* | `©lyr` / `covr` |

> [!IMPORTANT]
> **绝对不可越过的安全红线**：
> 1. **双入口安全契约明确分离**：
>    - `process_song.py`：默认允许在安全临时副本中更新内嵌标签，校验成功后原子替换；`--no-tags` 纯歌词模式绝不碰音频内嵌标签；`--lyrics-only` 仅更新歌词帧，保护已有人工元数据。
>    - `standardize_song.py`：`--in-place` 原地改写已被显式禁用；必须通过 `--output-dir` 指定独立输出目录，且**绝对拒绝覆盖任何已存在的同名文件**；未验证容器（如 M4A）不执行标准化写回。
> 2. **音频主体与播放语义不变量 (v1.8.4 R12)**：标准化 FLAC/MP3/WAV，以及日常改签 FLAC/MP3/WAV/OGG Vorbis/M4A-MP4 音频/DSF 均需先取得 `VERIFIED` 摘要，否则拒绝覆盖原文件。FLAC 摘要包含 STREAMINFO 与音频帧字节；WAV 包含完整 `fmt ` 与 PCM 数据长度/内容；MP3 验证 MPEG Layer III 帧边界与内容；OGG 校验页 CRC、Vorbis 非评论包及音频 granule；MP4 校验 mdat、音频 track/sample 表与重定位归一化的 chunk offset；DSF 校验 fmt 与 DSD 数据。音频内容或关键播放参数变化一律拒绝提交。`VERIFIED` 代表受支持结构的保守**字节级不变量**，不代表完成音频解码、每帧音频 CRC/完整合法性或播放器实机兼容测试；不受支持的特殊结构安全拒绝。
> 3. **回滚与恢复边界**：常规改签在独立副本上完成，音频摘要与并发源文件哈希复核通过后才替换；标准化输出拒绝覆盖已有目标。多 sidecar 顺序写入如中途失败则尽力逆序还原；若同时发生回滚失败，明确抛出 `SIDECAR_ROLLBACK_INCOMPLETE` 并保留 `.tmp_bak*` 原始备份供手动恢复。多个 `os.replace` 并非断电或进程崩溃安全的真正多文件原子事务。

---

## ⚙️ 进阶配置与声学调优

<details>
<summary><b>点击展开：环境变量与高级参数配置手册 (Advanced Settings)</b></summary>

<br>

如果需要对声学对齐模型与网络出口进行细致控制，可以通过以下环境变量覆盖默认行为：

| 环境变量 | 默认值 | 作用说明 |
| :--- | :---: | :--- |
| `MTL_LINE_AXIS` | `calibrate` | `calibrate` 自动将行标签下沉到开口前 60ms 刚好亮起；`keep` 保持原显示时间 |
| `MTL_ZH_CTC` | `1` | `1` 启用普通话专用 wav2vec2 CTC 逐行切窗对齐通道；`0` 回退至通用双引擎 |
| `MTL_ALIGN_MODE` | `windowed` | `windowed` 启用逐行切窗卡点（根治时间漂移）；`global` 回退为全曲单次对齐 |
| `MTL_GATE_MODE` | `strict` | `strict` 质量不达标时自动熔断重试；`warn` 仅告警；`off` 关闭门禁 |
| `MTL_ZH_GATE` | `on` | `on` 粤语歌熔断转普通话时进行横向验货打分，低于阈值拒绝盲目交付 |
| `MTL_CACHE_DIR` | 空 | 缓存根目录；默认按五级回退（Skill `.cache/` → 用户系统缓存 → 系统临时目录） |
| `MTL_STRICT_STORAGE` | `0` | `1` 严格限定仅使用 `MTL_CACHE_DIR`，不可写时立即报错，禁止使用系统临时盘 |
| `MTL_LRC_RED_CONTACT` | 空 | lrc.red 条款要求在请求 UA 中附带联系邮箱；未配置时将自动优雅跳过该网络以保合规 |

> ⚠️ **`MTL_DEVICE` / `MTL_DEMUCS` / `MTL_QC_L3` 当前未实现**（代码无读取点，历史文档遗留）：日常声学打轴固定使用 CUDA（`scripts/process_song.py` 内显式 `device="cuda"`），无可用 GPU 时抛 `AcousticEnvironmentError` 并拒绝伪逐字，**不会**静默降级到 CPU。请勿依赖这三个变量。

</details>

---

## 🧪 离线测试与工程验证

本项目配备了严谨的离线工程测试套件，涵盖各种冷门编码、极端时间戳、双声部念白与边界容器：

```bash
# 1. 开发仓库：自动发现全部 tests/test_*.py，含仓库专用发布测试
python -B tests/run_all.py

# 2. 分发 ZIP：在新解压目录执行同一命令，套件数依分发清单而定
python -B tests/run_all.py
# 请以当前解释器的实际 PASS / SKIP / FAIL 统计为准。

# 3. 快速运行指定模块测试
python -B tests/run_all.py -k line_axis
python -B tests/run_all.py -k canto
```

---

## 📜 版本更新历史 (Changelog)

### v1.8.5 同版本修订：歌词内前奏标题行（版本号不变）

- 统一两个入口的前奏标题行格式为 `歌名 - 歌手`，由 `core/utils/text_utils.py:format_display_title()` 单一出处产生；标准化路径不再只写纯歌名，清理掉带歌手的标题行后必须补回歌手。
- 标题行识别改由 `match_display_title()` 归一化处理，容忍倒序、括号别名/括注、全角连字符与多名分隔符；制作名单行与普通唱句不得误判为标题。
- R1 新增 `TITLE_DISPLAY_CONTRACT` 断言；新增 `tests/test_title_display_contract.py` 跨入口契约测试。
- 前奏标题与词曲显示行统一为**字符级逐字平均分配**（`make_char_words()` 单一算法，在各自前奏窗口内均分，含空格与连字符均占时间片）；铁律 20 仅保护正文演唱字轴，前奏显示投影不受其约束。
- 日常入口 `to_spl` 的**既有标题行去重**改用同一 `match_display_title()`：此前用 `" - "` 空格归一化比较，倒序、全角连字符、括号别名等真实变体会被判为非标题而**重复合成标题行**。
- `make_char_words()` 对非正窗口/空文本改为返回空列表（fail-closed），不再产出可被误用的 0ms 单字块；两个渲染器的非法字块报错补上行号与区间，便于把 `CANONICAL_RENDER_FAILED` 直接定位到源数据。
- 明确标准化入口的**输入契约**：输入必须是源歌词，对自身产物重复执行会因前奏显示行自带真实字轴而触发 `CREDIT_LIKE_SUNG_TEXT_RETAINED` 阻断（fail-closed，禁止硬删绕过）。
- 规格详情见 `SKILL.md` §三 与 §十一。本次为**同版本内容修订**，`core/version.py` 保持 `1.8.5`，发布包、清单与 Release 资产同步重建。

### v1.8.5 独立复核修正（R2，已按最小范围合入本仓）

- **HTTP 入口遗漏补齐**：实际用于 Apple TTML、iTunes、lrc.red 的 `http_get_text_ex` 现在默认最多读取 2 MiB，连 HTTP 429/4xx 错误响应也限制读取且明确返回 `error='oversized'`；小型错误响应保留原先的状态码、正文和退避语义。共享二进制下载函数 `http_get_bytes` 默认上限为 16 MiB，调用方仍可指定更严格的额度。
- **ID3v1 读取失败 Fail-Closed**：区分「成功确认无 ID3v1」与 `stat/open/seek/read` 异常及短读；异常不再伪装成无标签。额外独立校验原始与暂存尾块，常规改签预检失败时返回失败且不替换源文件。
- **FLAC 指纹缺失 Fail-Closed**：标准化及日常改签中，源扩展元数据指纹为 `None` 或读取抛错时直接阻断；合法空指纹 `{}` 仍可保存。
- **自包含回归**：新增 `tests/test_v185_r2_failclosed.py`，覆盖 HTTP 成功/错误响应大小限制、ID3v1 异常短读与旁路模拟、FLAC 正反路径。本节改动来自外部独立复核，已按最小范围合入本仓并纳入 Windows 阻塞门禁；正式发布仍须经 Git 索引构建、私有词表扫描与云端 CI 验证，未经批准不得推送 GitHub。

### v1.8.5 (2026-11-11) —— 元数据保真收口、校验契约补强与发布可复现

- **MP3 ID3v1 逐字节保真（安全修复）**：`mutagen` 的 `ID3.save(v1=…)` 没有「保持不变」语义（`0`=删除、`1`=更新、`2`=创建），默认 `v1=1` 会在仅改歌词时把既有 ID3v1 的 title/artist/album/year 从 ID3v2 重写、**无条件清空 comment**，非 Latin-1 文本还会降级成 `???`；而音频流摘要刻意排除 ID3v1，改动完全不可见。v1.8.5 改为「剥离 → 保存 → 原样附加」：写入前切下末尾 128 字节 `TAG` 块、保存后原封附回，日常与标准化两条路径 + `safe_save_audio_tags` 第三道闸共同保证字节不变；凭空创建/重复写入/摘要不符分别以 `ID3V1_CREATED` / `ID3V1_DUPLICATED` / `ID3V1_CHANGED` 阻断；
- **ID3v1 可核验**：`read_container_tags` 新增 `id3v1_present` 与 `id3v1_sha256`，标准化报告 `readback_tags` 新增 `id3v1_preserved`，保留与否可被外部复算；
- **FLAC 扩展元数据块指纹**：对 APPLICATION / SEEKTABLE / CUESHEET / 未知块建立 `(type, sha256(payload))` 多重集快照；允许块顺序与 PADDING 变化，禁止载荷与数量变化。这类块此前既不在音频流摘要内、也不在 Vorbis 键值闸门内；
- **回滚不完整必须可见**：标准化在报告写入失败后逐项验收清理结果，残留产物写入 `residual_outputs` 并返回 `SIDECAR_ROLLBACK_INCOMPLETE`，不再以 `BLOCKED` + 空 `actions_taken` 掩盖残留；
- **抓取层响应上限**：`http_get_text` / `http_get_json` / `http_post_json` / `http_post_json_body` 统一引入 `max_bytes`（默认 2 MiB，超限返回 `None`），实现为 `read(max_bytes + 1)`；AMLL 全量索引单独放宽到 32 MiB；
- **发布可复现**：正式包成员字节改从 **git 索引 blob** 读取，新增 `.gitattributes`（`* text=auto eol=lf`），同一 commit 在不同平台/检出状态产出同一个 ZIP；CI 新增 `release-repro` job 校验「重建结果 == 已提交 manifest」；
- **依赖与文档**：`mutagen>=1.47.0,<2`；容器矩阵按实测修正（M4A 已在公开 CI 覆盖、OGG 有夹具但无测试）；`SKILL.md` 移除硬编码套件数，改以 `run_all.py` 实际输出为准；`test_f16` 更名并标明「摘要排除 ID3v1 ≠ ID3v1 受保护」。

### v1.8.4 (2026-11-11) —— 多轨多语言歌词保真加固与工程治理
- **多轨多语言歌词写回保真**：全面支持保留 FLAC/OGG 多值 `lyrics`、MP3/WAV 额外 `USLT`/`SYLT`、M4A `©lyr` 副轨与 ID3 多值 `TXXX:TTMLLYRIC`；标准主歌词写入规范的 `USLT::eng`（无描述），非英文（如 `USLT::chi`）或带描述的歌词作为受保护副轨完整保留，杜绝误判误删；
- **全格式音频与结构不变量严格校验**：改签前后对 FLAC/WAV 核心播放参数、MP3 帧流、OGG Vorbis CRC/音频包、M4A/MP4 `mdat` 与 `moov/mvhd` 电影级时间头（含 timescale/duration）、DSF DSD 流进行写前写后不变性校验；FLAC 图片采用全结构序列化保护（涵盖尺寸、色彩、描述等），WAV 保护全部非 ID3 RIFF 扩展块（如 `fact`、`LIST`），音频主体及结构未通过校验时安全阻断并回滚；
- **来源盘点与物理回读双重对账**：`source_inventory` 全面枚举并记录额外轨道类型、语言/描述、哈希与字节数（不暴露歌词明文）；容器写回后物理回读强制校验副轨摘要，若发生篡改或丢失立即抛出异常并原子回滚；
- **未授权元数据全量不可变门禁**：写回前后对 FLAC Vorbis 注释全键值与 MP3/WAV ID3 全帧快照，严格保护非歌词元数据（含 `PRIV`、`COMM`、未授权 `TCOM`/`TEXT`、自定义 Vorbis 标签等），任何意外遗漏或篡改立即阻断；
- **多文件协同落盘补偿回滚**：`atomic_write_many` 引入预备份与逆序故障回滚；若回滚再次发生异常，自动保留 `.tmp_bak*` 物理备份并传播 `SIDECAR_ROLLBACK_INCOMPLETE`，改签操作强制前置 `VERIFIED` 状态校验；
- **多轨冲突 Fail-Closed 门禁**：对同标识冲突的副轨或相互竞争的未描述主歌词帧实行阻断（`REVIEW_REQUIRED` / `SOURCE_LYRICS_CONFLICT`），杜绝歧义交付；
- **Windows CI 核心门禁升级**：将 R9 输入完整性回归、v1.8.4 多轨保真回归、发布门禁及 R12/R13 安全测试套件全部纳入 Windows 核心阻塞流水线；55 套离线测试套件全部通过（801 PASS / 11 SKIP / 0 FAIL）。
- **R12 独立回归与并发防护**：source SHA-256 复核防范外部并发改写（并发变动即拒绝替换）；新增 R12 独立回归与合成 OGG/M4A/DSF 样本，修订两项以「无音频的 ID3-only 伪 MP3」为前提的历史测试。

### v1.8.3 (2026-11-10) —— TTML 时间规范、结构化兼容与 CI 自动化升级
- **R9 独立输入完整性加固**：将所有嵌入式与 sidecar 歌词来源纳入独立有效性核验与冲突仲裁；严格消费 ELRC 行内与元数据文本；将 `[offset]` 作用于比较器有效时间轴；为实际标准化和独立比较器增加词块差异门禁，防止音频时间推断及未知 TTML 语义角色被错误降级。新增完整来源对账与随机化跨格式回归测试；
- **跨版本 Unicode 字符兼容 (Python 3.10/3.13)**：统一 CJK 扩展 I 字符判定逻辑，解决 Python 3.10 旧内置 Unicode 库对新扩展表意文字的识别差异；
- **独立复审增量加固（第六轮修复）**：解决容器直接非空白文字（`body.text`/`div.text`/`p.tail`）静默丢弃问题，`span.tail` 未打轴文字禁止伪造声学字轴时间；收紧 WAV 验证，校验 RIFF 严格尾部边界与 `fmt ` 核心参数（采样率、声道、块对齐与字节率），支持标准 PCM 与 `WAVE_FORMAT_EXTENSIBLE`；解决非阻断 `DIV_BOUNDARY_INFERRED` 在实际 CLI 被误判问题；收紧 FLAC/MP3 音频数据区间有效性与连续帧边界验收，合成测试改用包含真实音频帧的 FLAC；
- **TTML 时间输出三段式标准化**：输出严格遵循 W3C `HH:MM:SS.mmm` 时钟格式，消除两段式歧义；输入兼容历史 `MM:SS.mmm` 与秒级偏移；
- **div 边界推导与结构化阻断**：无声明边界的单 `div` 自动推导子行起止并记录 `DIV_BOUNDARY_INFERRED`；对 `x-bg`、`x-roman`、Ruby、多 `div` 及相对时序模型实施严格结构化阻断，杜绝静默吞词；
- **大文件流式哈希与音频流不可变验证**：替换全量读入为分块流式哈希；FLAC/MP3/WAV 容器写回前后执行容器级音频帧数据流摘要比对，杜绝音频数据意外篡改；
- **GitHub CI 自动发现与 Windows 硬门禁**：Linux CI 矩阵扩展至 Python 3.10 与 3.13 并改为自动发现全部测试套件；Windows 烟测全面升级为核心阻塞门禁（`continue-on-error: false`）；
- **文档与安全契约规范化**：分离 `process_song` 与 `standardize_song` 的安全契约，纠正文件名后备约定示例，禁用 `--in-place` 声明与清除超额表述。

### v1.8.2 (2026-11-10) —— 双格式标准化引擎与 Canonical 全面升级
- **双格式标准化中枢**：构建独立的 Canonical Lyrics 标准数据模型与双向结构对齐审计器，提供行级/字级 ≤ 1ms 毫秒级一致性保障；
- **全曲制作名单识别**：前奏、正文与结尾制作署名全方位识别剥离并提取词曲，唱词与念白绝对保留；
- **智能前奏倒计时排版**：精准定位首唱时间 T，按规则调度前奏呈现，真实演唱字块时间绝对不变；
- **容器安全回写与读回双校验**：FLAC/MP3/WAV 容器读写严格隔离，损坏 ID3 拒绝覆写，已有元数据全方位保护；
- **全量测试扩充**：离线测试套件扩充至 45 套件 653 用例全绿通过，脱敏扫描 0 泄漏。

<details>
<summary><b>📜 点击查看往期版本更新历史 (v1.1.1 ~ v1.8.1)</b></summary>

<br>

| 版本 | 发布日期 | 亮点概述 |
| :---: | :---: | :--- |
| **v1.8.1** | `2026-11-08` | **lrc.red 全球开放同步网络接入健壮化 · 缓存串歌与负缓存锁死根治 · ISRC 双向仲裁与原子安全门禁**<br/>• 修复 H02 多层扩展名漏检，补齐路径全等检测；<br/>• 落实 lrc.red API 条款契约；导出规范注入回链署名；<br/>• 改造缓存五级回退候选链，瘦身公开分发包。 |
| **v1.8.0** | `2026-11-07` | **lrc.red 全球 ISRC 逐字网络接入 · Apple TTML 双引擎调度**<br/>• 引入全球歌词开放同步网络，支持根据本地 ISRC 直查 Apple Music 官方 TTML 逐字流光歌词；<br/>• 实现专属 TTML 命名空间与逐字结构解析器。 |
| **v1.7.0** | `2026-11-04` | **QQ 音乐 QRC 逐字通道 · 七源调度 · need_more 判据修复**<br/>• 移植 QQ 非标准位序 3DES 解密与 XML 逐字解析；<br/>• 调度优化为 Apple → 网易 → QQ → 酷狗 → AMLL → LRCSHARE → LRCLIB。 |
| **v1.6.1** | `2026-10-28` | **网易云 YRC 逐字通道 · 同轴译文 ytlrc**<br/>• 网易云升级为原生逐字通道；新增位置切片解析；<br/>• 自动截除尾部铺排冗余制作名单。 |
| **v1.6.0** | `2026-10-27` | **国语专用 CTC 逐行窗 · 行轴校准下沉 (−60ms)**<br/>• 引入中文专用 `wav2vec2-chinese` CTC 引擎，消灭长字吞停顿；<br/>• 行标签语义下沉至开口前 60ms，解决播放器跳屏。 |
| **v1.5.2** | `2026-10-27` | 规范 Unicode 16.0 扩展 A~I 生僻字识别，新增一键离线测试 runner。 |
| **v1.5.1** | `2026-10-26` | 落地双层声学质量门禁体系，完善粤语熔断重试机制。 |
| **v1.5.0** | `2026-10-26` | 引入粤语专用 `wav2vec2-cantonese` CTC 对齐引擎，内置 703 类无调粤拼字典。 |
| **v1.4.0** | `2026-10-25` | 原生收编 Demucs v4 音轨分离混合流水线，确立单曲单帧 USLT 规范。 |
| **v1.3.5** | `2026-10-25` | 完善网易云与 LRCLIB 专辑互斥门禁与短专辑名防穿透。 |
| **v1.3.4** | `2026-10-24` | 封面下载 UA 补全防 403，底层单测补齐。 |
| **v1.3.3** | `2026-10-24` | 内部版本互斥表、元数据正则与原子写统一收敛。 |
| **v1.3.0** | `2026-10-24` | 引入 Apple Music TTML 4-origin 镜像容灾机制。 |
| **v1.2.8** | `2026-10-23` | 标题窗口、署名声部分离、五源统一时长门禁。 |
| **v1.2.7** | `2026-10-23` | 尾奏保护、单帧歌词写入、年份补充。 |
| **v1.2.5** | `2026-10-22` | 有行无字先走 GPU 打轴，无主词则全球搜文本后再对齐。 |
| **v1.2.3** | `2026-10-21` | 接入 LRCSHARE 云端歌词、三级译文级联、黄金基线时间轴锚定。 |
| **v1.2.1** | `2026-10-21` | 确立 ELRC 双语翻译规范与词界空格治理。 |
| **v1.2.0** | `2026-10-20` | 引入 Demucs v4 声学对齐、双端能量门禁与换气间隙标记。 |
| **v1.1.1** | `2026-10-19` | 首发上线——多源逐字歌词检索、标签清洗与标准 Enhanced LRC 引擎。 |

</details>

---

## 💖 鸣谢与致敬 (Acknowledgments)

本项目在开发过程中，深受开源社区先驱项目的启发与帮助，特此致敬：
* **[AMLL (Apple Music Like Lyrics)](https://github.com/Steve-xmh/amll-ttml-db)**：感谢社区开放的高质量 Apple Music 官方 TTML 歌词数据库；
* **[Salt Player (椒盐音乐)](https://salt.moriafly.com/)**：感谢 Moriafly 发布的《SPL 格式语法标准》与在移动端极具美感的流光歌词体验；
* **[Folia](https://github.com/Folia-Music/Folia)**：感谢 Folia 现代音乐播放器对 Enhanced LRC 标准的优雅呈现；
* **[lrc.red](https://lrc.red)** 与 **[LRCLIB](https://lrclib.net/)**：感谢提供开放免认证的全球歌词检索与 ISRC 同步服务；
* **[Demucs v4](https://github.com/facebookresearch/demucs)**：感谢 Meta AI Research 提供的优秀人声分离架构；
* **[Mutagen](https://github.com/quodlibet/mutagen)**：感谢稳定可靠的底层音频元数据读写基石。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 许可证开源发布，代码与歌词处理逻辑完全透明、自由开放。
