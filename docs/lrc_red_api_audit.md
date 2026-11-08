# lrc.red API 审计与实测证据报告 (Phase 0)

> **审计时间**：2026-11-08  
> **执行环境**：Python 3.13 (Windows 11)  
> **审计对象**：`https://lrc.red` 全球歌词开放同步网络  
> **合规标识**：`User-Agent: AntigravityLyricsTool/1.8.0 (contact: sxhxu@qq.com)`

---

## 1. 接口探测结果汇总

对 `lrc.red` 方案中规划的 5 个端点进行实际网络连通与数据结构探测（测试音轨：`GBARL1401524`《Uptown Funk》）：

| 接口端点 | HTTP 状态 | 响应体积 | 数据格式 | 状态与用途 |
| :--- | :---: | :---: | :---: | :--- |
| `GET /s/{isrc}.song.json` | **200 OK** | ~147 KB | JSON | **首选直查接口**。顶层包含完整元数据、`lyrics` 解析数组及 `source`（原始 TTML 文本）。 |
| `GET /s/{isrc}.json` | **200 OK** | ~925 B | JSON | **轻量元数据接口**。包含标题、歌手、专辑、发行年份、音轨时长（秒）、文件链接等。 |
| `GET /s/{isrc}.ttml` | **200 OK** | ~37 KB | XML/TTML | **纯歌词文本接口**。交付未经包装的原生 Apple TTML 源码。 |
| `GET /match.json?track=...&artist=...` | **200 OK** | ~3.2 KB | JSON | **结构化元数据匹配**。返回 `{"hits": [...]}` 候选列表。 |
| `GET /search.json?q=...` | **200 OK** | ~3.7 KB | JSON | **全局文本模糊搜索**。返回 `{"hits": [...]}` 候选列表。 |

---

## 2. 关键发现与暗坑排雷（必须遵守的规则）

### 2.1 候选匹配排序陷阱（严禁盲目取首条）
- **现象**：请求 `/match.json?track=Uptown+Funk&artist=Bruno+Mars` 时，首条候选为 `Berklee Bruno Mars Ensemble` 的学生串烧版（时长 230.274s），而非 Mark Ronson 的官方原声（时长 270.613s）。而在 `/search.json?q=Uptown+Funk+Bruno+Mars` 中，官方原版位列第一。
- **治理规则**：无论 `/match.json` 还是 `/search.json`，**绝对严禁直接采纳首条候选**！必须对所有候选进行音轨时长过滤（`abs(cand_duration - track_duration) <= 5000ms`）与标题/歌手严格交集校验。

### 2.2 TTML 头部缺少 `<amll:meta>` 标签
- **现象**：AMLL 静态镜像的 TTML 会在 `<head>` 中嵌入 `<amll:meta key="musicName" ...>`，原 `core/parsers/ttml_parser.py` 强依赖此标签抽取标题歌手。但 `lrc.red` 的原生 TTML `<head>` 仅有 `<sourceMetadata>` 与 `<songwriters>`，无 `<amll:meta>`。
- **治理规则**：若直接传给旧 `parse_ttml()`，解析出来的 `ti` / `ar` 为空，会触发 `apple_ttml.py` 标题门禁误杀。因此必须通过专用的 `core/parsers/lrc_red_ttml.py` 或由 `LrcRedFetcher` 将 API 返回的结构化元数据补充写入解析结果的 `tags` 中。

### 2.3 时间戳格式兼容性
- **现象**：`lrc.red` TTML 的起止时间格式为纯秒数浮点串（如 `begin="16.906"`），而非 `00:16.906`。
- **校验结果**：经代码核验，`core/utils/time_utils.py` 中的 `parse_time_string_to_ms` 已原生支持浮点秒（`"16.906"` -> `16906ms`），无需额外打补丁。

### 2.4 模型生成标记（`byModel`）
- **现象**：探测的官方原生音轨未包含 `byModel` 标记。
- **治理规则**：按方案要求，若字段缺失归为 `unknown`；仅当明确标记 `byModel: true` 时判定为模型逐字，不作为 T1 官方逐字直接交付。

---

## 3. 超时与重试安全预算

```text
单次网络请求超时：3.0 秒
LrcRedFetcher 单曲内部总预算：5.0 秒
T1 (AppleTTMLFetcher) 全链路总预算：14.0 秒
批量请求速率上限：1 req/s
响应体积上限：JSON 1 MiB / TTML 2 MiB
```
