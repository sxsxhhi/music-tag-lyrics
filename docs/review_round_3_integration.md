# Round 3 系统集成与容器矩阵复检报告 (Review Round 3 - System Integration & Containers)

- **审查阶段**：R3（流水线端到端集成、多容器格式矩阵与零污染安全复检）
- **审查分支**：`fix/post-v181-audit`
- **审查时间**：2026-11-08
- **执行环境**：Windows 11 / Python 3.13.9

---

## 1. 实体容器支持真实性矩阵 (R17, R21)

根据用户对本次整改实事求是、严禁作假的原则，对各大音频容器的实体支持情况做客观声明与区分：

| 容器格式 | 扩展名 | 本地实体测试样本 | 元数据读写引擎 | 逐字/双语歌词内嵌 | 状态声明 |
|---|---|---|---|---|---|
| **FLAC** | `.flac` | ✅ 38 首真实音频 (`.test-work/fixtures/ab_sample/`) | `mutagen.flac.FLAC` | ✅ 完整支持 (`LYRICS`, VorbisComment) | **已实测验证 (Verified)** |
| **MP3** | `.mp3` | ✅ 1 首真实音频 (Behind - 黄淑惠.mp3) | `mutagen.mp3.MP3` | ✅ 完整支持 (`USLT` 单帧, ID3v2.3/2.4) | **已实测验证 (Verified)** |
| **M4A / AAC** | `.m4a` | ✅ 实体音频 (`tests/fixtures/sample.m4a`) | `mutagen.mp4.MP4` | ✅ 完整支持 (`©lyr`, Apple QuickTime Atoms) | **已实测验证 (Verified)** |
| **WAV** | `.wav` | ✅ 1 首真实音频 (My Heart Will Go On.wav) | `mutagen.wave.WAVE` | ✅ 完整支持 (ID3 chunk / RIFF) | **已实测验证 (Verified)** |
| **DSF** | `.dsf` | ❌ 本地暂无实体可解码样本 | `mutagen.dsf.DSF` | ⚠️ 依赖 mutagen，待夹具补齐 | **待实体夹具补充验证 (Pending Physical Fixture)** |
| **OGG / Vorbis** | `.ogg` | ❌ 本地暂无实体可解码样本 | `mutagen.oggvorbis.OggVorbis` | ⚠️ 依赖 mutagen，待夹具补齐 | **待实体夹具补充验证 (Pending Physical Fixture)** |

> **审计承诺**：不采用空字节或破损文件伪造测试通过，文档及矩阵如实标示 DSF 与 OGG 为待实体夹具补充验证，对用户与社区保持透明度与真实度。

---

## 2. 流水线核心特性端到端复验

### 2.1 调度与质量仲裁 (R03, R16)
- **调度优先序**：
  1. `Apple TTML` (am-lyrics + lrc.red 双引擎)
  2. `NetEase` (逐字 YRC)
  3. `QQ Music` (逐字 QRC)
  4. `Kugou` (逐字 KRC)
  5. `AMLL` (ncm 逐字)
  6. `LRCSHARE`
  7. `LRCLIB` (行级兜底)
- **仲裁逻辑验证**：
  - strict 原生逐字直接采纳并提前截断；
  - loose / insufficient 逐字不提前截断，保留继续向下游高严格度源检索机会；
  - 外语曲目严格校验双语翻译有效性，无译文不提前截断。

### 2.2 双语流光歌词格式输出
- **Enhanced LRC (`.lrc`)**：严格遵循时间戳与逐字打轴规范，保留作词、作曲、专辑信息及翻译附着行。
- **SPL 逐字歌词 (`.spl`)**：为播放器及第三方工具输出结构化逐字流光歌词。

### 2.3 零污染与只读隔离机制 (R19, R20)
- **曲库源隔离**：`F:\TOOLs\AI's DATA\ABC\Music\待修补\My music` 源音频在处理前采样 SHA-256，全部测试在复制隔离目录 `.test-work/fixtures/ab_sample/` 内进行，输出定向至 `.test-work/out/ab_eval/`。
- **只读旗标**：`--no-tags` (代码内 `update_tags=False`) 确保即使在处理过程中也绝对不对输入音频做任何在位修改。
- **母盘保护红线**：严禁触碰 `F:\TOOLs\AI's DATA\ABC\Music\只音\` 母盘文件。

---

## 3. R3 复检结论

流水线集成与多容器读写表现稳定，零污染隔离机制经过物理路径与哈希验证完全有效，准予进入 Round 4 发布包与交付物校验。
