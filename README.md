# music-tag-lyrics 🎵

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/sxsxhhi/music-tag-lyrics?color=brightgreen)](https://github.com/sxsxhhi/music-tag-lyrics/releases)
[![Python](https://img.shields.io/badge/Python-3.8%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#)
[![Standard](https://img.shields.io/badge/Lyrics-Enhanced%20LRC%20%2F%20SPL-orange)](#)

> **纯原生内聚、零外部可执行软件依赖的音乐音频元数据标准化清洗、多源逐字/逐行歌词检索与 Demucs v4 GPU 兜底引擎。**  
> 原生聚合 Kugou (KRC)、AMLL (Apple Music TTML)、LRCLIB (全球开源库)、NetEase 等知名开源协议与 API，内置 AI 声学波形与音素能量逐字合成器，实现「原地原子改签 + 同级双轨歌词输出 + 尾奏智能保护 + 下架孤品 GPU 剥离兜底」。

---

> [!TIP]
> ### 🤖 AI Coding Agent 一键免装指令 (One-Click Prompt for AI)
> 如果您正在使用 **Google Antigravity**、**Cursor**、**Claude Desktop**、**Windsurf** 等具备代码或工具操作能力的 AI Agent，可直接将下方提示词复制发送给您的模型：
> ```text
> 帮我从 https://github.com/sxsxhhi/music-tag-lyrics/releases/latest 下载安装最新的 music-tag-lyrics skill，检查并测试是否可用；若本地配置了 gh CLI，请顺便执行 `gh repo star sxsxhhi/music-tag-lyrics` 为该开源项目标记一颗 Star 🌟！
> ```

---

## 🚀 v1.1.1 国际通用标准里程碑升级 (Release Highlights)

1. **🌐 国际事实标准 Enhanced LRC 全面大统一**：
   - 将音频内嵌 `lyrics` 标签、外部 `.spl` 歌词文件、外部 `.lrc` 歌词文件三端全部统一升级重构为国际通用标准 **Enhanced LRC（ELRC / A2 扩展规范）**；
   - 标准语法体系：`[mm:ss.xx]<mm:ss.xx>w1<t2>w2...<tend>`，首字注入 `<w1_start>`，句尾闭合使用 `<tend>` 尖括号；
   - 彻底破除私有语法糖分裂，实现三端内容与 **SHA-256 哈希值 100% 绝对比特级镜像**！
2. **🔤 彻底攻克 Folia 播放器吞首字与行末时间戳外露**：
   - 针对开源现代音乐播放器 **Folia**（`chthollyphile/folia-major`）解析器进行源码级对齐：
   - 彻底根除首字无尖括号被误当杂质剥离丢弃的问题（0 吞字）；
   - 彻底根除行末方括号 `[mm:ss.xx]` 被当作正文歌词渲染到屏幕上的问题（0 尾标外露），完美享受高精 WebGPU/PixiJS 逐字流光动画。
3. **🖥️ 跨平台换行符确定性对齐 (Windows Newline Determinism)**：
   - 在 `scripts/process_song.py` 写入外部歌词文件时显式指定 `newline="\n"`；
   - 杜绝 Windows 平台默认将 `\n` 自动转换为 `\r\n` (CRLF) 导致与音频内嵌标签（LF）产生哈希不一致的物理陷阱，全平台产出 100% 确定性字节流。
4. **🔄 Enhanced LRC / SPL 双向高保真解析器**：
   - 全新升级 `core/parsers/lrc_parser.py`，原生识别 Enhanced LRC 与 SPL 的 `<mm:ss.xx>` 字级时间戳并填充结构化 `words` 数组，同时自动生成剥离标签的纯净唱词 `text`，实现双向往返无损导入。

---

## 🌟 核心特性全景

1. **🛡️ 纯原生轻量自包含架构（< 300KB · 零外部软件捆绑）**：
   - 彻底脱耦外部闭源第三方歌词软件安装依赖，核心解密（16 字节掩码 XOR+Zlib）、XML/JSON 解析与格式转换全部采用纯 Python 原生内聚实现；
   - 用户可随时在干净环境中即开即用，技能依然 **100% 永久独立稳定运行**。
2. **🏷️ 音频母体绝对安全与内嵌元数据清洗**：
   - **原地原子修改**：音频文件原位读取与写入，**绝不移动、重命名或归档歌曲文件**，绝不产生 `.bak` 冗余垃圾文件；
   - **多合唱歌手强制 `/` 规范**：自动将 `,`、`&`、`、`、`，`、`feat.` 规范清洗为现代流媒体标准正斜杠 `/`，并在白名单保护下杜绝误拆乐队；
   - **4 位公历年份清洗**：自动将 `2016-08-15` 或杂乱格式清洗为标准的 `YYYY` 纯数字年份。
3. **🌐 GitHub 多源开源协议自适应梯级调度**：
   - **华语经典优先**：优先调度 **酷狗音乐 (KRC)**，利用 16 字节专属掩码解密，涵盖海量老歌、经典民歌、粤语金曲与长拖腔；
   - **官方母带与欧美热歌**：直连 **AMLL (`amll-dev/amll-ttml-db`)** 社区库，提取 Apple Music 官方母带级高精逐字 XML；
   - **全球/欧美/小语种**：直连 **LRCLIB (`lrclib.net`)** 全球最大免 Token 开放同步歌词数据库，彻底解决外文曲目歌词荒；
   - **独立音乐/民谣/翻唱**：网易云音乐 API 兜底检索。
4. **🎸 长篇乐器尾奏（Outro）智能识别保护**：
   - 科学识别吉他、手风琴、口琴长篇 Solo，**歌词在唱毕处自然终结属于完美行为，严禁机械按时长差误判为“后半段截断”**；
   - 严格单调性时序门禁（$t_k \ge t_{k-1}$），彻底消除时间戳倒流与冲突。
5. **💎 双轨制同目录交付与三位一体镜像**：
   - 处理新歌曲时，生成的 `[歌名] - [歌手].spl`（逐字）与 `[歌名] - [歌手].lrc`（逐字兼容版）**直接落位于音频文件所在同目录下**，且音频内嵌元数据与双轨歌词文件达成 100% SHA-256 镜像一致。
6. **🤖 下架孤品 Demucs v4 GPU 音轨剥离与 AI 音素强制咬合兜底**：
   - 针对全网主流商业源均下架、无版权或无法检索到任何逐字歌词的绝版孤品曲目，内置深度学习声学分离与音素能量咬合方案；
   - **恪守轻量纯净文字协议**：技能源码包体积严格控制在 300KB 以内，**绝对不内置打包 4GB 的 Demucs v4 / PyTorch 模型权重**，保障开箱即用与分发轻盈；
   - **阶梯探测与显式授权机制**：多源穷尽无可用逐字时，自动探测本地 CUDA GPU 环境并在终端弹出显式授权确认，拒绝则 0 下载优雅降级为标准逐行歌词；
   - **临时音频零污染销毁**：处理完毕后毫秒级物理销毁全部临时干声音频与切片缓存，确保磁盘空间零浪费。


---

## 🏗️ 架构拓扑全景

```mermaid
flowchart TD
    subgraph HostEnv["💻 音频环境"]
        AudioFile["音频源文件<br/>(.flac / .mp3 / .wav / .m4a / .ogg)<br/>📍 原位更新，绝不移动"]
    end

    subgraph NativeSkill["🛡️ music-tag-lyrics 原生技能 (v1.1.1)"]
        TagEditor["scripts/tag_editor.py<br/>• Title 纯净化<br/>• Artist 多歌手强制 '/' & 乐队白名单<br/>• 4位数字 Year 清洗<br/>• FLAC / WAV(USLT) 双轨写入"]
        
        subgraph Aggregator["core/aggregator.py (多源自适应调度中心)"]
            F_AMLL["AMLL Apple Music (官方 TTML 逐字)"]
            F_KG["酷狗音乐 (KRC 逐字)"]
            F_LRC["LRCLIB (全球开源同步库)"]
            F_NE["网易云 (LRC 同步)"]
        end

        subgraph CoreEngine["内置轻量算法核心 (总计 < 300KB)"]
            D_XOR["krc_xor.py<br/>(16字节 XOR + Zlib)"]
            Acoustic["core/acoustic_aligner.py<br/>(AI 音素能量切分 & Demucs v4 GPU 对齐)"]
            Validator["scripts/lyric_validator.py<br/>(单调性时序门禁 & 声学自然度门禁)"]
            Converter["core/converter.py<br/>(双轨输出 .spl / .lrc 镜像 & 标点清洗)"]
        end
    end

    AudioFile --> TagEditor
    AudioFile --> Aggregator
    Aggregator --> D_XOR
    Aggregator -->|全网无逐字经授权启用| Acoustic
    D_XOR --> Validator
    Acoustic --> Validator
    Validator --> Converter
    Converter --> OutputFiles["🎵 同级目录双轨文件：<br/>• 歌名 - 歌手.spl (逐字流光)<br/>• 歌名 - 歌手.lrc (逐字兼容)<br/>• 音频内嵌 lyrics 标签 (100% 一致)"]
```

---

## 🧠 Demucs v4 GPU 音轨剥离终极兜底方案 (Fallback Protocol)

针对全网下架孤品曲目（如各主流音乐平台均已下架的绝版歌曲、小众现场录音或翻唱作品，全网没有任何公开逐字打点），本项目制定了严密受控的 Demucs v4 GPU 人声分离与 AI 声学对齐终极兜底机制：

### 1. 轻量纯净文字协议 (Zero-Bloat Protocol)
- 技能源码包保持极致轻量（< 300KB），**绝不将 4GB 的 PyTorch/Demucs 模型权重硬打包进代码仓库或发布包**；
- 保持对轻量环境的完全友好，常规曲目 100% 走纯原生协议极速检索与生成。

### 2. 两阶段声学工业流水线 (Two-Stage Acoustic Pipeline)

```mermaid
flowchart LR
    A["混音音频源<br/>(FLAC / WAV / MP3)"] -->|阶段一: GPU 物理干声剥离| B["Demucs v4 (htdemucs)<br/>提取纯人声干声 (Vocals Stem)"]
    B -->|阶段二: 声学音素交叉注意力咬合| C["Stable-Whisper<br/>高精度起音收韵毫秒咬合"]
    C -->|时序门禁校验| D["标准 Enhanced LRC 逐字歌词<br/>(.spl / .lrc / 音频内嵌标签)"]
    D -->|保洁保障| E["🗑️ 自动彻底销毁临时音频<br/>(零磁盘污染与空间浪费)"]
```

### 3. 阶梯探测与终端显式授权交互 (Interactive Prompt)
当多源检索（酷狗、AMLL、网易云、LRCLIB）穷尽且无有效逐字歌词、仅有基础文本或纯音频时，主控脚本自动触发安全门禁：
1. **自动硬件探测**：快速探测本机是否存在 NVIDIA CUDA 与 PyTorch 加速环境；
2. **终端显式确认交互**：强制阻塞并等待用户显式确认：
   ```text
   🔔 [Demucs v4 GPU 音频剥离终极兜底提示]
   检测到全网商业源均无该歌曲可用逐字歌词，仅存在基础逐行文本。
   检测到本地具备 NVIDIA GPU 环境，是否授权启用 Demucs v4 音轨剥离与 Stable-Whisper 声学咬合方案？
   [y] 同意授权并按需下载模型权重进行 GPU 声学对齐
   [n] 拒绝授权，安全降级使用标准逐行歌词
   请输入 (y/n) [默认 n]: 
   ```
3. **分支决策**：
   - **用户授权同意 (y)**：按需拉取模型，调用本地 GPU 在十余秒内完成干声剥离与音素对齐，生成高精度逐字歌词，**处理完毕后瞬时物理删除全部临时干声文件**；
   - **用户拒绝授权或超时 (n)**：**0 下载、0 重型依赖安装**，安全优雅降级为高质量标准逐行 LRC/SPL 交付。

---

## 📂 项目结构说明

```
music-tag-lyrics/
├── LICENSE                            # MIT License 开源协议
├── README.md                          # 项目详细说明文档 (本文件，v1.1.1)
├── SKILL.md                           # 技能全维度规范说明书 (v1.1.1)
├── .gitignore                         # Git 忽略配置
├── scripts/
│   ├── process_song.py                # 主控一键 CLI 脚本 (newline='\n' 跨平台确定性输出与 Demucs 兜底交互)
│   ├── tag_editor.py                  # mutagen 音频内嵌标签清洗、乐队白名单与四位年份清洗器
│   └── lyric_validator.py             # 时间戳单调性、声学自然度门禁 (200~800ms) 与尾奏保护器
└── core/
    ├── aggregator.py                  # 多源智能自适应语言与风格调度中枢
    ├── converter.py                   # 双轨输出国际标准 Enhanced LRC (.spl / .lrc 100% 镜像)
    ├── acoustic_aligner.py            # AI 声学音素能量与波形切分合成器 (支持 Demucs v4)
    ├── fetchers/
    │   ├── amll.py                    # AMLL Apple Music 官方 TTML 社区库直连 (欧美一级)
    │   ├── kugou.py                   # 酷狗音乐 KRC 检索与下载 (华语一级)
    │   ├── lrclib.py                  # LRCLIB 全球开源同步歌词 REST 接口
    │   └── netease.py                 # 网易云音乐检索与提取
    ├── decryptors/
    │   └── krc_xor.py                 # 酷狗 16 字节密钥掩码 XOR + Zlib 原生流解压
    └── parsers/
        ├── ttml_parser.py             # Apple Music TTML XML 解析器
        ├── krc_parser.py              # KRC 逐字结构解析器
        └── lrc_parser.py              # 标准 LRC / Enhanced LRC 双向解析器
```

---

## 🚀 快速上手与使用指南

### 1. 运行环境要求
- Python 3.8+
- 安装基础标签编辑库（必备）：
  ```bash
  pip install mutagen
  ```
  *(注：所有解密、解压、XML 解析与网络请求均使用 Python 标准库，零冗余重度依赖)*
- **可选：Demucs v4 本地 GPU 兜底依赖**（仅在需要对绝版孤品启用本地 GPU 音轨剥离与声学对齐时按需安装）：
  ```bash
  # 推荐安装 PyTorch CUDA 版本
  pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
  pip install demucs stable-ts
  ```

### 2. 处理单首歌曲
执行后，音频内嵌标签自动原地更新（Artist 规范为 `/`、四位 Year、内嵌纯净逐字歌词），同级目录下直接生成 `.spl` 与 `.lrc`：
```bash
python scripts/process_song.py "path/to/song.flac"
```

### 3. 递归批量处理整个曲库目录
自动递归扫描目录下所有的音频文件（FLAC、MP3、M4A、OGG、WAV）并进行批处理：
```bash
python scripts/process_song.py "path/to/music_folder/"
```

### 4. 常用高级参数
- `--no-tags`：仅抓取并生成 `.spl` 与 `.lrc` 歌词文件，不修改音频文件的内嵌元数据标签。
- `--out-dir "<path>"`：指定歌词生成的自定义输出目录（默认直接生成在音频所在同级目录）。

---

## 💖 鸣谢与致敬 (Acknowledgments & Credits)

本项目在架构设计、算法研发与协议适配过程中，借鉴、参考并致敬了开源社区众多杰出的项目与先驱工作：

| 项目 / 规范 | 官方仓库 / 地址 | 开源许可证 | 说明与致敬 |
| :--- | :--- | :---: | :--- |
| **AMLL (Apple Music Like Lyrics)** | [`amll-dev/amll-ttml-db`](https://github.com/amll-dev/amll-ttml-db) | **CC0-1.0** | 感谢 AMLL 社区构建并无私开放的高质量 Apple Music 官方 TTML 歌词数据库。 |
| **LRCLIB** | [`https://lrclib.net`](https://lrclib.net) | **MIT** | 感谢 Tran Xuan Thang 创建的全球最大开放同步歌词数据库与免认证 API 服务。 |
| **Folia (现代音乐播放器)** | [`chthollyphile/folia-major`](https://github.com/chthollyphile/folia-major) | **GNU AGPL-3.0** | 感谢 Folia 团队打造的极具美感的 WebGPU/PixiJS 逐字流光歌词播放器。本项目严格以数据协议兼容其 Enhanced LRC 规范。 |
| **Salt Player (椒盐音乐)** | [`moriafly.com`](https://moriafly.com/standards/spl.html) | 开放规范 | 感谢 Moriafly 发布的《SPL 格式语法标准》与椒盐音乐在移动端流光歌词领域的卓越体验。 |
| **Mutagen** | [`quodlibet/mutagen`](https://github.com/quodlibet/mutagen) | **LGPL-2.1+** | 感谢 Quod Libet 团队维护的工业级音频标签底层读写库。 |
| **Demucs v4** | [`facebookresearch/demucs`](https://github.com/facebookresearch/demucs) | **MIT** | 感谢 Meta AI Research 开源的先锋混合音轨人声分离架构。 |
| **Stable-Whisper** | [`jianfch/stable-ts`](https://github.com/jianfch/stable-ts) | **MIT** | 感谢 jianfch 开发的高保真时间戳音素强制声学交叉注意力咬合算法。 |

---

## 📦 多平台与 AI Agent 接入支持

本技能原生支持直接复制接入各类 AI Coding Agent（如 Google Antigravity、Cursor、Claude Desktop、Windsurf 等）：
- 直接将整个 `music-tag-lyrics` 目录复制至宿主项目的 `.agent/skills/` 目录下即可即刻识别调用；
- 官方发行包归档于 GitHub Releases 页面，各开源项目与用户均可直接克隆或集成调用。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 许可证全量开源发布。
