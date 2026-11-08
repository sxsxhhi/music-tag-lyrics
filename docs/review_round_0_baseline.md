# Round 0 初始复现与基线审查报告 (Review Round 0 - Baseline)

- 审查阶段：R0（准备轮）
- 基线 Commit SHA：`963e733a08437d65f0abac81276d4eff216862b5`
- 分支：`fix/post-v181-audit`
- 审查时间：2026-11-08

---

## 1. 缺陷总览与证据等级定性 (R01–R24)

| 编号 | 优先级 | 等级 | 缺陷简述 | 定位文件 | 证据与复现结论 |
|---|---|---|---|---|---|
| R01 | P0 | A | CI 6 项用例报错 AttributeError (`'str' object has no attribute 'get'`) | `core/fetchers/netease.py`, `tests/test_isrc_integration.py` | 已在 GitHub Actions `42464379731` 确证。网易 API 海外返回非 dict result 时崩溃。 |
| R02 | P0 | A | ISRC 集成测试使用 `if mock_update.called` 导致假阳性通过漏洞 | `tests/test_isrc_integration.py` | 静态代码证实。若前序逻辑提前跳出，断言根本不被触发。 |
| R03 | P0 | A | `loose`/`insufficient` 逐字候选过早截断后续高质量源（如网易/QQ逐字） | `core/aggregator.py` | 静态代码证实。`need_more()` 仅根据 `_is_word_level()` 返回 False，阻塞后续查询。 |
| R04 | P0 | A | 本地歌手未知时仍可能放行 `isrc_write_eligible` | `core/fetchers/lrc_red.py` | 静态代码证实。`has_full_identity` 仅检查远端标题歌手，本地歌手为空时不阻断 ISRC 资格。 |
| R05 | P1 | A | `byModel=False` 直接映射为 `origin_status='official'` | `core/fetchers/lrc_red.py` | 静态代码证实。非模型生成不代表官方发行，存在过度承诺。 |
| R06 | P1 | B | 录音版本硬门禁覆盖不足（Live/Studio/Remix/Cover 等） | `core/utils/text_match.py`, `core/version_guard.py` | 部分版本冲突未实现双向互斥拦截。 |
| R07 | P1 | A | 非法时间戳被静默当作有效 `0ms` | `core/utils/time_utils.py`, `core/parsers/lrc_red_ttml.py` | 静态代码证实。`parse_time_string_to_ms` 异常返回 0，导致损坏时间戳被误标 native word。 |
| R08 | P1 | A | 逐字覆盖率仅按行统计，丢词/跨词文本不保真被隐藏 | `core/parsers/lrc_red_ttml.py` | 静态代码证实。只要行内有 1 个 word 即标 native，缺乏行内原文文本一致性校验。 |
| R09 | P1 | A | `timestamp_repair_count`、`timeline_overlap_count` 统计恒为 0 或失真 | `core/parsers/lrc_red_ttml.py` | 静态代码证实。推断时间未计数，质量指标不可信。 |
| R10 | P1 | B | 复杂 TTML 多 div / 多声部译文盲目附着 `lines[-1]` 导致错配 | `core/parsers/lrc_red_ttml.py` | 静态代码证实。缺乏基于 ID 或时间戳的最佳关联机制。 |
| R11 | P1 | B | Apple / iTunes 链路未全链路共享 14.0s 绝对 deadline | `core/fetchers/apple_ttml.py`, `core/fetchers/itunes_client.py` | 静态代码证实。底层重试与多端点未扣除已逝时间，可能严重超时。 |
| R12 | P1 | B | lrc.red 备用端点 429 未统一熔断冷却 | `core/fetchers/lrc_red.py` | 静态代码证实。一个端点 429 后仍继续请求备用端点。 |
| R13 | P2 | A | lrc.red 缓存只限 500 条条目数，不限总内存字节 | `core/fetchers/lrc_red.py` | 静态代码证实。大 XML 可能造成内存无界膨胀。 |
| R14 | P1 | A | 网易响应嵌套类型缺失类型检查，同歌名错歌风险 | `core/fetchers/netease.py` | 静态代码证实。搜索结果缺严密歌手/版本验证。 |
| R15 | P1 | B | LRCLIB 搜索缺少充分标题/歌手验证，35s 时长容差过宽 | `core/fetchers/lrclib.py` | 静态代码证实。仅有时长与版本粗筛，缺乏艺人交集验证。 |
| R16 | P1 | A | 七源逐字与身份质量字段不一致，缺少统一 Quality 对象 | `core/aggregator.py`, 各 fetchers | 架构定义分散，难以统一仲裁质量等级。 |
| R17 | P1 | B | 实际容器测试样本不完整（缺少真实解码夹具），写入失败状态与日志混淆 | `scripts/process_song.py`, `tests/test_isrc_integration.py` | 标签更新失败仍返回并输出 success。 |
| R18 | P1 | A | 离线测试未加装网络 Tripwire 拦截器 | `tests/_testkit.py` | 导致测试过程中真实外联逃逸。 |
| R19 | P1 | B | 缺少真实曲库 30–50 首前置冻结 A/B 对照证据 | `docs/` | 尚未建立真实样本比对基准数据。 |
| R20 | P2 | A | 缓存路径与临时文件默认依赖 `%LOCALAPPDATA%`，未强制环境变量兜底 | `core/utils/cache_paths.py` | 测试需显式重定向至 F 盘，绝不污染系统盘与母盘。 |
| R21 | P2 | C | Python 3.8+ 及全容器兼容声明未全部经过 CI 检验 | `README.md`, CI 配置 | CI 仅跑 Python 3.13 与 Ubuntu。 |
| R22 | P0 | A | 已发布 v1.8.1 tag 与失败 CI 不闭环 | GitHub Release, git tag | `v1.8.1` tag 指向了 CI 失败的 commit `963e733a`。 |
| R23 | P2 | C | lrc.red User-Agent 署名及来源描述缺乏权威链接与格式化 | `core/fetchers/lrc_red.py` | 需规范 User-Agent 与来源署名展示。 |
| R24 | P2 | C | 文档与 Agent 安装指令中存在不当修饰与无关操作 | `README.md`, `SKILL.md` | 移除无关命令与过度承诺措辞。 |

---

## 2. 差异对比（与 v1.8.0 相比）

- v1.8.0 (`668450e`)：首次引入 lrc.red 与 ISRC 机制，但未考虑海外环境网易结构异常；测试未隔离外部调用；
- v1.8.1 基线 (`963e733`)：试图修复 TTML 跨行与候选消歧，但引入了 CI 失败与未闭环发布。

---

## 3. 本轮复现结论

1. 成功在本地及 CI 记录中复现网易结构脆弱性、离线测试外网穿透缺陷和假阳性断言漏洞；
2. 明确整改路线：必须按 Phase 0 -> Phase 5 与 R1 -> R5 循环严格执行，杜绝降低门禁、SKIP 或编造测试；
3. 建立统一整改追踪矩阵 `docs/audit_v181_remediation_matrix.md`。
