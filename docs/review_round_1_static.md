# Round 1 静态代码与架构复检报告 (Review Round 1 - Static Analysis)

- **审查阶段**：R1（静态代码审计）
- **审查分支**：`fix/post-v181-audit`
- **审查基准提交**：`9e3d79f`
- **审查时间**：2026-11-08

---

## 1. 静态复检范围与核查清单

本轮针对 Phase 1 至 Phase 4 落地代码开展全面的静态代码走查与边界审计：

### 1.1 网易 API 防御与类型加固 (R01, R14)
- **文件**：`core/fetchers/netease.py`
- **走查结论**：
  1. `search_best_song` 中对 `data.get("result")`、`songs`、`album` 严格实施 `isinstance(dict)` 和 `isinstance(list)` 防御；
  2. `lookup_year` 强化了对 `album` 缺失或非 dict 的受控退出；
  3. `search_best_song` 新增对本地非空艺人的交集核验：当本地提供明确艺人时，候选艺人列表若无交集或候选艺人为空时直接剔除，杜绝同歌名错歌问题。
- **状态**：✅ **通过 (Pass)**

### 1.2 ISRC 资格安全与艺人/版本对称门禁 (R04, R05, R06)
- **文件**：`core/fetchers/lrc_red.py`, `core/utils/text_match.py`, `core/utils/version_guard.py`
- **走查结论**：
  1. `_validate_and_build_candidate` 明确要求本地艺人非空且非泛称，且必须与远端艺人具有非空交集（`local_art & remote_art`），本地歌手未知时绝不赋予 `isrc_write_eligible`；
  2. 修复 `byModel=False` 语义混淆：改判为 `unverified_origin`，严禁自我声称为 `official`；
  3. 扩充 `VERSION_KEYWORDS` 与 `VERSION_EXCLUSION_TOKENS`（包含 instrumental, 伴奏, cover, 翻唱, acoustic 等），并在 `isrc_write_eligible` 中实施严格的双向版本全集对称匹配（`local_ver_all == remote_ver_all`），Remaster/Live 与原版之间禁止写回 ISRC。
- **状态**：✅ **通过 (Pass)**

### 1.3 调度真值表与 need_more 决策升级 (R03, R16)
- **文件**：`core/aggregator.py`
- **走查结论**：
  1. `_rank` 确立基于 strict / loose / insufficient 的精细等级真值表，保持基准整型 3/2/1/0 向后兼容的同时支持有理数级浮点仲裁；
  2. `need_more` 彻底修复：华语仅在获取 strict 原生逐字时提前停止，遇到 loose 或 insufficient 逐字时保持搜索，允许后续优质源（如 QQ strict / 网易 strict）顶替；外语曲目要求 strict 且包含有效译文才提前终止。
- **状态**：✅ **通过 (Pass)**

### 1.4 TTML 严格解析、文本保真与真实指标 (R07, R08, R09, R10)
- **文件**：`core/utils/time_utils.py`, `core/parsers/lrc_red_ttml.py`
- **走查结论**：
  1. `parse_time_string_to_ms` 异常或负时间戳返回 `None` 而不是伪造成合法 `0ms`；
  2. `parse_lrc_red_ttml` 保留 `raw_main_text` 并与 `reconstructed_text` 执行去空字符比对，发现丢词即标记 `text_mismatch=True` 并计入 `invalid_word_lines`；
  3. 真实累加 `inferred_end_count`、`timestamp_repair_count`、`timeline_overlap_count`，杜绝恒为 0；
  4. 独立译文段（`<p role="x-translation">`）支持显式 `target` / `id` 关联与声部隔离最近邻附着，解耦了对 `lines[-1]` 的盲目绑定。
- **状态**：✅ **通过 (Pass)**

### 1.5 网络预算、429 退避冷却与缓存控制 (R11, R12, R13, R15)
- **文件**：`core/fetchers/apple_ttml.py`, `core/fetchers/itunes_client.py`, `core/fetchers/lrc_red.py`, `core/fetchers/lrclib.py`
- **走查结论**：
  1. T1 14.0s 绝对超时预算全链路注入 `ITunesClient` 与底层 HTTP 请求；
  2. lrc.red 引入共享 `_cooldown_until` 熔断器，429 后激活指数退避冷却；
  3. `_raw_cache` 引入 32MB 内存预算与 entry count 双重控制的 LRU 淘汰机制；
  4. LRCLIB 搜索增加标题模糊匹配与艺人交集过滤，时长容差从 35s 收窄至 15s。
- **状态**：✅ **通过 (Pass)**

---

## 2. 静态复检发现的潜在隐患与即时自愈记录

1. **隐患 1**：`core/fetchers/itunes_client.py` 曾出现残留代码段 `def _request` 导致的语法错误。
   - **自愈**：已在提交 `9e3d79f` 前完成清洗，彻底消除语法冲突。
2. **隐患 2**：`core/fetchers/lrclib.py` 曾从错误的模块尝试导入 `is_title_fuzzy_match`。
   - **自愈**：已正确定位至 `core.utils.version_guard` 完成导入。

---

## 3. R1 复检结论

静态代码审计与数据流完整性核验全部合格，无未捕获的全局异常或未隔离逻辑，准予进入 Round 2 自动化测试与故障注入深度复检。
