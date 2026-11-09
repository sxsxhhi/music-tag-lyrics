# music-tag-lyrics 🎵

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
  <a href="https://github.com/sxsxhhi/music-tag-lyrics/releases"><img src="https://img.shields.io/github/v/release/sxsxhhi/music-tag-lyrics?color=brightgreen&style=flat-square" alt="Release"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10--3.13-blue?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Offline%20Tests-555%20passed%20%7C%2011%20skipped-brightgreen?style=flat-square&logo=pytest&logoColor=white" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/Zero%20External%20Binaries-100%25%20Native-success?style=flat-square" alt="Dependencies"></a>

  <a href="#"><img src="https://img.shields.io/badge/Audio%20Containers-FLAC%20%7C%20MP3%20%7C%20M4A%20%7C%20WAV%20(Verified)-informational?style=flat-square" alt="Containers"></a>
  <a href="#"><img src="https://img.shields.io/badge/Lyrics-Enhanced%20LRC%20%2F%20SPL-orange?style=flat-square" alt="Standard"></a>
  <a href="#"><img src="https://img.shields.io/badge/Cover%20Art-1000x1000%20HD-purple?style=flat-square" alt="Cover Art"></a>
</p>

> **一句话介绍**：让你的本地音乐也能拥有像 Apple Music 一样丝滑的逐字动效歌词，自动补齐超清专辑封面与规范的歌曲标签，还能用本地 AI 帮绝版老歌逐字打轴，**绝不乱改你原有的音频文件**。

---

> [!TIP]
> ### 🤖 AI Coding Agent 一键安装使用指令 (One-Click Prompt)
> 如果你正在使用 **Cursor**、**Google Antigravity**、**Claude Desktop**、**Windsurf** 等 AI 编程助手，直接复制下方这句话发给 AI 即可：
> ```text
> 帮我从 https://github.com/sxsxhhi/music-tag-lyrics/releases/latest 下载安装最新的 music-tag-lyrics skill，并运行离线测试套件验证环境可用性。
> ```

---

## 💡 它能解决哪些让人头疼的烦恼？

很多音乐发烧友在整理本地音乐（已实测支持 FLAC / MP3 / M4A / WAV；OGG 与 DSF 待补充物理夹具）时，经常遇到这几件头疼事：

| 日常痛点 | 之前遇到的尴尬 | music-tag-lyrics 怎么帮你解决？ |
| :--- | :--- | :--- |
| **找歌词难** | 只有整行歌词、或者四处翻找不到好歌词 | **直连 7 大曲库与全球 ISRC 同步网络**：Apple Music (AMLL + lrc.red 双引擎) → 网易云 YRC → QQ 音乐 QRC → 酷狗 → AMLL → LRCSHARE → LRCLIB 梯级匹配，自动挑选最高清晰度的逐字版本；支持基于 ISRC 毫秒级直查欧美及新歌官方 TTML。 |
| **绝版歌没逐字** | 下架孤品、冷门老歌全网只有纯文本歌词 | **本地 AI 声学打轴**：内置粤语 + 国语双专用 CTC 模型，逐行卡准人声；**坚持“绝不听写盲猜”**，字字基于原词精准卡点。 |
| **播放器抽搐抢跑** | 歌词在椒盐音乐、Folia 里提前抢跑、上下跳动 | **行标签自动下沉校准**：自动对齐歌手开口前 60ms 刚好亮起；双语翻译行成对跟随，告别播放器跳屏抽搐。 |
| **歌曲标签乱七八糟** | 歌手名带怪符号、年份变成当天、作词作曲缺失 | **安全规范化清洗**：合唱歌手统一用 `/` 分隔、年份标准化为 4 位、自动补齐作词作曲；**原地更新，绝不重命名或移动音频**。 |
| **封面模糊一片黑** | 导入播放器没有封面，或者只有几百像素缩略图 | **三级高清封面补全**：自动从 iTunes 抓取 `1000x1000` 原图无损写入；已有封面 100% 绝对保护不覆盖。 |
| **生硬机翻毁意境** | 外语歌词被直译大模型机翻成莫名其妙的话 | **坚决拒绝机械机翻**：只采用网易云/官方精修的人工翻译（含与逐字轨同轴的 `ytlrc`），译文时间戳严格继承原唱，听歌赏词两不误。 |

---

## 🚀 快速上手 (Quickstart)

### 1. 安装环境（极速轻量）

只需 Python 3.10–3.13（CI 在 Python 3.13 环境下全量通过），基础功能仅需两个通用依赖：
```bash
pip install -r requirements.txt
```
*(注：核心加解密、XML 解析全部使用 Python 原生实现，体积仅几 MB；如果不玩本地 AI 打轴，连显卡都不需要！)*

> **如果你需要为下架老歌开启本地 GPU 声学打轴（可选）**：
> ```bash
> pip install -r requirements-acoustic.txt
> # 根据你的显卡安装 PyTorch (CUDA):
> # pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
> ```

---

### 2. 日常最常用命令速查 (Cheatsheet)

不管你是处理一首单曲，还是整理几千首歌的音乐库，只需一行命令：

```bash
# 1. 最常用的标准处理：洗标签 + 补封面 + 抓逐字歌词 + 导出 .lrc/.spl
python scripts/process_song.py "周杰伦 - 晴天.flac"

# 2. 批量处理整个音乐文件夹（递归扫描所有子目录）
python scripts/process_song.py "/path/to/your/music/"

# 3. 保护已有信息：只更新歌词，绝不触碰你原本手填的标题/歌手/年份/封面
python scripts/process_song.py "歌曲.flac" --lyrics-only

# 4. 纯歌词零污染模式：只下载歌词文件（.lrc/.spl），完全不修改音频本身
python scripts/process_song.py "歌曲.flac" --no-tags

# 5. 不需要封面：只整理标签和歌词
python scripts/process_song.py "歌曲.flac" --no-cover

# 6. 为绝版老歌开启本地 AI 强制声学打轴（自动人声分离 + 逐行卡点）
python scripts/process_song.py "绝版老歌.flac" --force-gpu

# 7. 自带纯文本歌词，让 AI 按照你的歌词去卡点打轴
python scripts/process_song.py "歌曲.flac" --lyrics-file "我的歌词.txt"

# 8. 允许向音频安全补齐缺失的 ISRC 国际标准录音码（默认只读不改写）
python scripts/process_song.py "歌曲.flac" --write-isrc
```

---

## 🎨 格式效果展台：你的歌词会长成什么样？

本项目同时交付国际通用的 Enhanced LRC 逐字规范与椒盐音乐原生的 SPL 逐字标准，所有格式在音频内嵌和外部文件之间 **100% 达成字节级一致**：

````carousel
```lrc
/* 1. 传统老式行级歌词 (Standard LRC) —— 整行一起亮，容易让人看串行 */
[00:15.32]I could lie, say I like it like that
[00:18.45]Like it like that
[00:20.12]I could lie, say I like it like that
```
<!-- slide -->
```lrc
/* 2. 国际标准 Enhanced LRC 逐字动效 —— 唱到哪个字哪个字发光 */
[00:15.32]<00:15.32>I <00:15.55>could <00:15.82>lie, <00:16.15>say <00:16.42>I <00:16.70>like <00:17.02>it <00:17.35>like <00:17.65>that
[00:18.45]<00:18.45>Like <00:18.80>it <00:19.12>like <00:19.45>that
```
<!-- slide -->
```lrc
/* 3. 官方精修双语逐字流光 (推荐: 椒盐音乐 / Folia 完美兼容，绝不抽搐跳屏) */
[00:15.32]<00:15.32>I <00:15.55>could <00:15.82>lie, <00:16.15>say <00:16.42>I <00:16.70>like <00:17.02>it <00:17.35>like <00:17.65>that
[00:15.32]我可以撒谎，说我也喜欢这样
[00:18.45]<00:18.45>Like <00:18.80>it <00:19.12>like <00:19.45>that
[00:18.45]喜欢那样
```
<!-- slide -->
```spl
/* 4. 椒盐音乐 (Salt Player) 官方 SPL 格式 —— 本工具原生直接支持 */
[00:15.32]<00:15.32>I <00:15.55>could <00:15.82>lie, <00:16.15>say <00:16.42>I <00:16.70>like <00:17.02>it <00:17.35>like <00:17.65>that
[00:18.45]<00:18.45>Like <00:18.80>it <00:19.12>like <00:19.45>that
```
````

---

## 🏗️ 架构拓扑：它是如何稳定工作的？

### 1. 全景调度与双引擎声学打轴流程

从左侧音频读取、中间多源检索、右侧声学对齐到最终交付的全链路示意图：

<p align="center">
  <img src="docs/images/lyric_engine_architecture.svg" alt="全景调度与双引擎声学打轴架构" width="100%">
</p>


### 2. 三级超清专辑封面自动匹配

<p align="center">
  <img src="docs/images/cover_art_pipeline.svg" alt="三级超清专辑封面自动获取流程" width="100%">
</p>

---

## 💽 音频格式与标签支持对照表

| 常见音频格式 | 文件后缀 | 歌词内嵌位置 | 封面内嵌位置 | 安全保障机制 |
| :---: | :---: | :---: | :---: | :---: |
| **FLAC** | `.flac` | `LYRICS` 标签 | `METADATA_BLOCK_PICTURE` | 写入临时副本，确认成功后原子替换 |
| **MP3** | `.mp3` | `USLT` 单帧（防冲突） | `APIC` (Front Cover) | 写入临时副本，确认成功后原子替换 |
| **M4A / ALAC** | `.m4a`, `.mp4` | `©lyr` 标签 | `covr` 标签 | 写入临时副本，确认成功后原子替换 |
| **WAV** | `.wav` | ID3 Chunk (`USLT`) | ID3 Chunk (`APIC`) | 写入临时副本，确认成功后原子替换 |
| **OGG / Opus** | `.ogg` | `LYRICS` 标签 | *(保护既有标签不乱改)* | 写入临时副本，确认成功后原子替换 <sup>[1]</sup> |
| **DSF** (DSD) | `.dsf` | ID3v2.4 (`USLT`) | ID3v2.4 (`APIC`) | 写入临时副本，确认成功后原子替换 <sup>[1]</sup> |

> <sup>[1]</sup> 实测环境已具备 FLAC / MP3 / M4A / WAV 物理真机解码夹具与回归校验；OGG / DSF 格式已完成 Mutagen 标准标签协议与原子写隔离验证，实体音频端到端解码待补充真机实体夹具（实事求是，绝无虚假 Mock）。

---

## 🧪 自动化测试（开发者专区）

本项目配备了极其严苛的工程测试用例网络，涵盖各种奇怪歌词格式、字符集、连读念白与边界条件：

```bash
# 1. Git 完整开发仓库：运行全部 38 个离线测试套件（566 项用例：555 通过 / 11 条件跳过，0 失败）
python tests/run_all.py

# 2. 分发安装包（已按分发视图剔除构建/同步等仅仓库侧工具）：运行 33 个离线测试套件（529 项用例：518~528 通过 / 1~11 条件跳过，0 失败）
python tests/run_all.py

# 3. 快速运行指定模块测试
python tests/run_all.py -k line_axis
python tests/run_all.py -k canto
```

**验证范围（如实声明）**

- **双层测试基线说明**：
  - **Git 完整开发仓库**：包含全部 38 个离线测试套件（共 566 项用例，本地实测基线为 555 PASS / 11 SKIP / 0 FAIL）；
  - **Release / Skill 分发包**：为保持分发包轻量且避免未分发的开发工具在包内引发假故障，分发视图依据 `scripts/_dist_view.py` 剔除了仅用于本地构建、镜像同步及 Git 钩子的开发侧工具及对应 5 个专用测试套件（`test_release_build`, `test_release_verify`, `test_sync_copies`, `test_prepush_hook`, `test_benchmark_contract`），包内包含 33 个套件共 529 项用例（在含完整 torch 容器中实测 528 PASS / 1 SKIP；在纯 CPU 环境实测 518 PASS / 11 SKIP / 0 FAIL）；
- 上方数字是环境实测基线，Windows / GPU / 声学对齐（Demucs、Whisper、Wav2Vec2）属于维护者本地验证范围，公开 CI 未独立核验，相关用例在 CI 中以条件跳过呈现；
- 缓存默认优先落在 Skill 目录 `.cache/`，该目录不可写时会按五级回退继续尝试系统缓存与 TEMP 并告警；`MTL_STRICT_STORAGE=1` 只约束**应用缓存目录**，不等于「整个进程不写系统盘」；
- 容器矩阵中标注 `Verified in public CI` 的项有公开夹具支撑；标注 `Verified locally only` / `Not verified` 的项不作为公开保证。

---

<details>
<summary><b>⚙️ 点击展开：进阶环境变量与声学质量门禁配置 (Advanced Settings)</b></summary>

<br>

如果你需要对声学对齐模型进行极细致的自定义控制，可以通过以下环境变量覆盖默认行为：

| 环境变量 | 默认值 | 作用说明 |
| :--- | :---: | :--- |
| `MTL_LINE_AXIS` | `calibrate` | `calibrate` 自动将行标签下沉到开口前 60ms；`keep` 保持原歌词显示时间 |
| `MTL_ZH_CTC` | `1` | `1` 启用中文专用 wav2vec2 CTC 逐行窗通道；`0` 回落至 Whisper 双引擎 |
| `MTL_ALIGN_MODE` | `windowed` | `windowed` 启用逐行切窗对齐（消除漂移）；`global` 回退至全曲单次对齐 |
| `MTL_GATE_MODE` | `strict` | `strict` 质量不合格触发熔断重试；`warn` 仅打印警告；`off` 关闭门禁 |
| `MTL_DEVICE` | `auto` | `auto` 自动检测 CUDA；`cpu` 强制走 CPU 计算模式 |
| `MTL_DEMUCS` | `on` | `off` 可在 CPU 模式下跳过人声分离，直接对混音对齐以节省时间 |
| `MTL_ZH_GATE` | `on` | `on` 粤语歌熔断转普通话时必须通过横向打分验货，低于 `-8.0` 则拒绝出货 |
| `MTL_CACHE_DIR` | 空 | 缓存目录；为空时按五级回退（Skill `.cache/` → 系统缓存 → TEMP） |
| `MTL_STRICT_STORAGE` | `0` | `1` 时只接受已存在、绝对、可写的 `MTL_CACHE_DIR`，缺失/相对/不可写即报错，不回退 |
| `MTL_LRC_RED_CONTACT` | 空 | lrc.red 官方条款要求 UA 内含联系邮箱；未配置或邮箱格式非法时将自动优雅跳过该源以避免违规，建议填入合规联系邮箱（如 user@example.com） |

</details>

---

## 🙏 数据来源与署名

歌词数据来自 [lrc.red](https://lrc.red)。按其 API 服务条款（Terms of Use）要求：
1. **User-Agent 身份与联系邮箱**：请求 UA 需包含应用标识并提供有效联系邮箱（本工具通过环境变量 `MTL_LRC_RED_CONTACT` 显式注入，源码不内置任何私人邮箱；若未配置或邮箱格式不合规，引擎将自动优雅旁路并打印日志，绝不发送不合规的匿名/假联系方式请求）；
2. **清晰署名与回链**：展示与导出来自 lrc.red 的歌词时必须明确署名并回链。在导出 Enhanced LRC (`.lrc`) 及椒盐音乐 (`.spl`) 文件时，标准头部元数据已自动注入 `[re:lrc.red (https://lrc.red)]` 署名回链标签，候选结果元数据亦在 `provenance.attribution` 中完整挂载回链。

---

## 📜 版本更新历史 (Changelog)

| 版本 | 发布日期 | 亮点概述 |
| :---: | :---: | :--- |
| **v1.8.1** | `2026-11-08` | **lrc.red 全球开放同步网络接入健壮化 · 缓存串歌与负缓存锁死根治 · ISRC 双向仲裁与原子安全门禁 · 缓存五级回退与分发瘦身 · 38 套件 566 用例全绿**<br/>• 修复 H02 私有指纹 segment 规则对带扩展名文件名的匹配漏检，补齐路径全等与 stem 全等检测；<br/>• 落实 H18 lrc.red 官方条款契约：`MTL_LRC_RED_CONTACT` 严格校验邮箱格式，缺省或非法时优雅跳过该源；导出 LRC/SPL 规范注入 `[re:lrc.red (https://lrc.red)]` 署名回链；<br/>• 改造 `core/utils/cache_paths.py` 五级候选链：默认优先使用 skill 根目录 `.cache/`（跟随工作区所在卷）；该目录不可写时按五级回退继续尝试系统缓存与 TEMP 并告警；如需严格限定单一目录须显式启用 `MTL_STRICT_STORAGE=1`；<br/>• 抽离 `scripts/_dist_view.py` 统一定义分发视图与排除规则，公开分发包剔除本机私有开发脚本、过程审计报告与版权夹具，包体积显著瘦身且工作树 sha256 零不一致；<br/>• 打包脚本与 `core.version` 单一信源全面对齐，Manifest 排除自身打包消除自指死锁；<br/>• 修复 lrc.red 缓存解耦：拆分远端原始记录与本地单曲仲裁，彻底杜绝缓存串歌污染与负缓存死锁；<br/>• 统一直查与搜索版本互斥（Live/Remix/Instrumental 严禁串歌）与时长门禁（±3000ms）；<br/>• ISRC 写回音频标签五重门禁保护（时长非空/差值≤3000ms/strict凭证/已有ISRC不覆盖）；<br/>• 全量离线自动化测试扩展至 38 套件 566 用例（555 通过 / 11 条件跳过 / 0 失败；分发包内为 33 套件 529 用例）。 |

| **v1.8.0** | `2026-11-07` | **lrc.red 全球 ISRC 逐字网络接入 · Apple TTML 双引擎调度 · 366 项测试全绿**<br/>• 引入 lrc.red 全球歌词开放同步网络，作为 Apple TTML 的原生双引擎补充；<br/>• 支持基于本地 ISRC 编码直查 Apple Music TTML 逐字流光歌词；<br/>• 实现 lrc.red 专属 TTML 命名空间与逐字结构解析器。 |
| **v1.7.0** | `2026-11-04` | **QQ 音乐 QRC 逐字通道 · 七源调度 · need_more 判据修复 · 353 项测试全绿**<br/>• 新增 QQ 音乐 QRC 逐字源：移植 QQ 非标准位序的 3DES 解密（标准 DES 库解不出来），解析 XML 包装的 `字(起,长)` 格式；<br/>• 调度顺序改为 **Apple → 网易 → QQ → 酷狗 → AMLL → LRCSHARE → LRCLIB**；<br/>• 修复 `need_more`：从「有候选即停」改为「非逐字就继续」，否则网易行级会锁死 QQ 逐字；<br/>• QQ 只交逐字，行级候选直接放弃；不消费未验证的 trans/roma 字段；<br/>• 新增 `tests/test_qq_qrc.py`（53 条），套件总数 21。 |
| **v1.6.1** | `2026-10-28` | **网易云 YRC 逐字通道 · 同轴译文 ytlrc · 298 项测试全绿**<br/>• 网易云从「只能出整行」升级为可出逐字（`yv=1`）；「只有逐字、没有行级歌词」的曲目从六源无候选变为直接拿到逐字；<br/>• 新增位置切片解析，歌词里的 `(FR)` 之类括号不再截断文本、不再触发整行降级；<br/>• 自动识别并截除尾部等距铺排的制作名单（原本会冲出曲末 23 秒被门禁整包丢弃）；<br/>• 无 YRC 的曲目行为与升级前逐字节一致；六源优先级与调度顺序不变。 |
| **v1.6.0** | `2026-10-27` | **国语专用 CTC 逐行窗 · 行轴校准下沉 (−60ms) · 软硬双闸门 · 271 项测试全绿**<br/>• 引入中文专用 `wav2vec2-chinese` CTC 引擎，彻底消灭「长字吞停顿」；<br/>• 行标签语义从显示轴下沉至歌手起唱点前 60ms，彻底解决播放器抽搐跳屏问题；<br/>• 软硬双阈值字宽闸门（放行 1400~1537ms 真实长音，拦截假字并回退）；<br/>• GitHub Actions CI 离线自动化测试修复，19 套件全绿。 |
| **v1.5.2** | `2026-10-27` | 规范 Unicode 16.0 扩展 A~I 生僻字识别，修正 `gpu_whisper` 声学记账，新增 `tests/run_all.py` 一键离线测试 runner。 |
| **v1.5.1** | `2026-10-26` | 落地双层声学质量门禁体系（整曲宽门限 + 分行严门限），完善粤语熔断自动重试机制，修复 34 项工业级边界缺陷。 |
| **v1.5.0** | `2026-10-26` | 引入粤语专用 `wav2vec2-cantonese` CTC 逐字对齐引擎，内置 703 类无调粤拼字典，提供 `--lang auto/zh/yue` 语种智能路由。 |
| **v1.4.0** | `2026-10-25` | 原生收编 Demucs v4 音轨分离与声学对齐混合流水线，确立单曲 `USLT` 标签单帧防冲突规范。 |
| **v1.3.5** | `2026-10-25` | 推广网易云与 LRCLIB 的专辑／Remix 门禁与短专辑名防穿透，修复 README 表格语法截断（+2 单测）。 |
| **v1.3.4** | `2026-10-24` | 封面下载 UA 补全防 403、底层工具单测补齐、重构遗留死导入清理。 |
| **v1.3.3** | `2026-10-24` | 内部去重收敛——版本互斥表、元数据正则、译文挂载、HTTP 出口与原子写统一。 |
| **v1.3.2** | `2026-10-24` | 测试缓存隔离、批量限流扩容、CLI 退出码修正与专辑封面自动修补的工程加固。 |
| **v1.3.0** | `2026-10-24` | 引入 Apple Music TTML 4-origin 镜像容灾机制，确立单调等级调度（D6）防低质量顶替。 |
| **v1.2.8** | `2026-10-23` | 标题窗口、署名声部分离、五源统一时长门禁与时间精度自适应。 |
| **v1.2.7** | `2026-10-23` | 尾奏不误拒、单帧歌词写入、补充年份与酷狗丢弃日志。 |
| **v1.2.6** | `2026-10-22` | 元数据修补与 Live 全局互斥，收录 v1.2.5 热修补。 |
| **v1.2.5** | `2026-10-22` | 有行无字先走 GPU 打轴，无主词则全球搜文本后再对齐。 |
| **v1.2.3** | `2026-10-21` | 接入 LRCSHARE 云端歌词、三级译文级联、黄金基线时间轴锚定与 18 条铁律。 |
| **v1.2.1** | `2026-10-21` | 确立 ELRC 双语翻译规范与词界空格治理。 |
| **v1.2.0** | `2026-10-20` | 引入 Demucs v4 声学对齐、双端能量门禁与换气间隙标记，确立「绝不语音听写」红线。 |
| **v1.1.3** | `2026-10-20` | 工业级架构重构——DRY 全量去重，系统性根治 8 项核心隐患。 |
| **v1.1.2** | `2026-10-20` | Live 现场版强物理互斥门禁，支持下架歌曲的逐字歌词生成。 |
| **v1.1.1** | `2026-10-19` | 首发——纯原生多源逐字歌词检索、标签清洗与标准 Enhanced LRC 引擎。 |

---

## 💖 鸣谢与致敬 (Acknowledgments)

本项目在开发过程中，深受开源社区先驱项目的启发与帮助，特此致敬：
* **AMLL (Apple Music Like Lyrics)**：感谢社区开放的高质量 Apple Music 官方 TTML 歌词数据库；
* **Salt Player (椒盐音乐)**：感谢 Moriafly 发布的《SPL 格式语法标准》与在移动端极具美感的流光歌词体验；
* **Folia**：感谢 Folia 现代音乐播放器对 Enhanced LRC 标准的优雅呈现；
* **LRCLIB / LRCSHARE**：感谢提供开放免认证的全球歌词检索 API 服务；
* **Demucs v4**：感谢 Meta AI Research 提供的优秀人声分离架构；
* **Mutagen**：感谢稳定可靠的音频元数据底层读写库。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 许可证开源发布，代码与歌词处理逻辑完全透明、自由开放。
