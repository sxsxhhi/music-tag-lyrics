# music-tag-lyrics 🎵

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square" alt="License: MIT"></a>
  <a href="https://github.com/sxsxhhi/music-tag-lyrics/releases"><img src="https://img.shields.io/github/v/release/sxsxhhi/music-tag-lyrics?color=brightgreen&style=flat-square" alt="Release"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.8%2B-blue?style=flat-square&logo=python&logoColor=white" alt="Python"></a>
  <a href="tests/"><img src="https://img.shields.io/badge/Offline%20Tests-298%20passing-brightgreen?style=flat-square&logo=pytest&logoColor=white" alt="Tests"></a>
  <a href="#"><img src="https://img.shields.io/badge/Zero%20External%20Binaries-100%25%20Native-success?style=flat-square" alt="Dependencies"></a>
  <a href="#"><img src="https://img.shields.io/badge/Audio%20Containers-FLAC%20%7C%20MP3%20%7C%20M4A%20%7C%20WAV%20%7C%20OGG%20%7C%20DSF-informational?style=flat-square" alt="Containers"></a>
  <a href="#"><img src="https://img.shields.io/badge/Lyrics-Enhanced%20LRC%20%2F%20SPL-orange?style=flat-square" alt="Standard"></a>
  <a href="#"><img src="https://img.shields.io/badge/Cover%20Art-1000x1000%20HD-purple?style=flat-square" alt="Cover Art"></a>
  <a href="https://github.com/sxsxhhi/music-tag-lyrics"><img src="https://img.shields.io/github/stars/sxsxhhi/music-tag-lyrics?style=social" alt="GitHub Stars"></a>
</p>

> **一句话介绍**：让你的本地音乐也能拥有像 Apple Music 一样丝滑的逐字动效歌词，自动补齐超清专辑封面与规范的歌曲标签，还能用本地 AI 帮绝版老歌逐字打轴，**绝不乱改你原有的音频文件**。

---

> [!TIP]
> ### 🤖 AI Coding Agent 一键安装使用指令 (One-Click Prompt)
> 如果你正在使用 **Cursor**、**Google Antigravity**、**Claude Desktop**、**Windsurf** 等 AI 编程助手，直接复制下方这句话发给 AI 即可：
> ```text
> 帮我从 https://github.com/sxsxhhi/music-tag-lyrics/releases/latest 下载安装最新的 music-tag-lyrics skill，并检查测试是否可用；顺便执行 `gh repo star sxsxhhi/music-tag-lyrics` 为项目点个 Star 🌟！
> ```

---

## 💡 它能解决哪些让人头疼的烦恼？

很多音乐发烧友在整理本地音乐（FLAC/MP3/M4A/WAV/DSF）时，经常遇到这几件头疼事：

| 日常痛点 | 之前遇到的尴尬 | music-tag-lyrics 怎么帮你解决？ |
| :--- | :--- | :--- |
| **找歌词难** | 只有整行歌词、或者四处翻找不到好歌词 | **直连 6 大曲库**：优先从 Apple Music (am-lyrics)、酷狗、AMLL、网易云 YRC 等梯级匹配，自动挑选最高清晰度的逐字版本；网易只有逐字没有行级歌词时也能用。 |
| **绝版歌没逐字** | 下架孤品、冷门老歌全网只有纯文本歌词 | **本地 AI 声学打轴**：内置粤语 + 国语双专用 CTC 模型，逐行卡准人声；**坚持“绝不听写盲猜”**，字字基于原词精准卡点。 |
| **播放器抽搐抢跑** | 歌词在椒盐音乐、Folia 里提前抢跑、上下跳动 | **行标签自动下沉校准**：自动对齐歌手开口前 60ms 刚好亮起；双语翻译行成对跟随，告别播放器跳屏抽搐。 |
| **歌曲标签乱七八糟** | 歌手名带怪符号、年份变成当天、作词作曲缺失 | **安全规范化清洗**：合唱歌手统一用 `/` 分隔、年份标准化为 4 位、自动补齐作词作曲；**原地更新，绝不重命名或移动音频**。 |
| **封面模糊一片黑** | 导入播放器没有封面，或者只有几百像素缩略图 | **三级高清封面补全**：自动从 iTunes 抓取 `1000x1000` 原图无损写入；已有封面 100% 绝对保护不覆盖。 |
| **生硬机翻毁意境** | 外语歌词被直译大模型机翻成莫名其妙的话 | **坚决拒绝机械机翻**：只采用网易云/官方精修的人工翻译（含与逐字轨同轴的 `ytlrc`），译文时间戳严格继承原唱，听歌赏词两不误。 |

---

## 🚀 快速上手 (Quickstart)

### 1. 安装环境（极速轻量）

只需 Python 3.8+，基础功能仅需两个通用依赖：
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
python scripts/process_song.py "D:/我的音乐/"

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

### 1. 全景调度与双引擎声学打轴流程 (v1.6.1)

从左侧音频读取、中间多源检索、右侧声学对齐到最终交付的全链路示意图：

<p align="center">
  <img src="docs/images/lyric_engine_architecture.svg" alt="v1.6.1 全景调度与双引擎声学打轴架构" width="100%">
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
| **OGG / Opus** | `.ogg` | `LYRICS` 标签 | *(保护既有标签不乱改)* | 写入临时副本，确认成功后原子替换 |
| **DSF** (DSD) | `.dsf` | ID3v2.4 (`USLT`) | ID3v2.4 (`APIC`) | 写入临时副本，确认成功后原子替换 |

---

## 🧪 自动化测试（开发者专区）

本项目配备了极其严苛的工程测试用例网络，涵盖各种奇怪歌词格式、字符集、连读念白与边界条件：

```bash
# 一键运行全部 20 个离线测试套件（298 项用例全部离线通过）
python tests/run_all.py

# 快速运行指定模块测试
python tests/run_all.py -k line_axis
python tests/run_all.py -k canto
```

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

</details>

---

## 📜 版本更新历史 (Changelog)

| 版本 | 发布日期 | 亮点概述 |
| :---: | :---: | :--- |
| **v1.6.1** | `2026-10-28` | **网易云 YRC 逐字通道 · 同轴译文 ytlrc · 298 项测试全绿**<br/>• 网易云从「只能出整行」升级为可出逐字（`yv=1`）；「只有逐字、没有行级歌词」的曲目从六源无候选变为直接拿到逐字；<br/>• 新增位置切片解析，歌词里的 `(FR)` 之类括号不再截断文本、不再触发整行降级；<br/>• 自动识别并截除尾部等距铺排的制作名单（原本会冲出曲末 23 秒被门禁整包丢弃）；<br/>• 无 YRC 的曲目行为与升级前逐字节一致；六源优先级与调度顺序不变。 |
| **v1.6.0** | `2026-10-27` | **国语专用 CTC 逐行窗 · 行轴校准下沉 (−60ms) · 软硬双闸门 · 271 项测试全绿**<br/>• 引入中文专用 `wav2vec2-chinese` CTC 引擎，彻底消灭「长字吞停顿」；<br/>• 行标签语义从显示轴下沉至歌手起唱点前 60ms，彻底解决播放器抽搐跳屏问题；<br/>• 软硬双阈值字宽闸门（放行 1400~1537ms 真实长音，拦截假字并回退）；<br/>• GitHub Actions CI 离线自动化测试修复，19 套件全绿。 |
| **v1.5.2** | `2026-10-27` | 规范 Unicode 16.0 扩展 A~I 生僻字识别，修正 `gpu_whisper` 声学记账，新增 `tests/run_all.py` 一键离线测试 runner。 |
| **v1.5.1** | `2026-10-26` | 落地双层声学质量门禁体系（整曲宽门限 + 分行严门限），完善粤语熔断自动重试机制，修复 34 项工业级边界缺陷。 |
| **v1.5.0** | `2026-10-26` | 引入粤语专用 `wav2vec2-cantonese` CTC 逐字对齐引擎，内置 703 类无调粤拼字典，提供 `--lang auto/zh/yue` 语种智能路由。 |
| **v1.4.0** | `2026-10-25` | 原生收编 Demucs v4 音轨分离与声学对齐混合流水线，确立单曲 `USLT` 标签单帧防冲突规范。 |
| **v1.3.0** | `2026-10-24` | 引入 Apple Music TTML 4-origin 镜像容灾机制，确立单调等级调度（D6）防低质量顶替。 |

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
