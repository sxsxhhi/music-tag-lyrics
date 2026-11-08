# music-tag-lyrics v1.8.1 审计整改追踪矩阵 (Remediation Tracking Matrix)

> 格式标准：`ID | 优先级 | 证据等级 | 基线现象 | 根因 | 改动文件 | 对应测试 | 修复前状态 | 修复后状态 | 审查轮次 | 回归影响 | 状态 | 剩余风险`

| ID | 优先级 | 证据等级 | 基线现象 | 根因 | 改动文件 | 对应测试 | 修复前状态 | 修复后状态 | 审查轮次 | 回归影响 | 状态 | 剩余风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R01 | P0 | A | CI 6 项用例抛 AttributeError ('str' object has no attribute 'get') | 网易 API 返回非 dict result，未做结构防护 | `core/fetchers/netease.py` | `test_isrc_integration.py`, `test_netease_yrc.py` | CI 失败 (Red) | 待修复 | R0 | 影响年份补全与网易检索 | In Progress | 需保证正常解析不受影响 |
| R02 | P0 | A | ISRC 安全测试使用 if mock.called 造成假阳性通过漏洞 | 测试用例断言未强制执行 | `tests/test_isrc_integration.py` | `test_isrc_integration.py` | 假阳性脆弱断言 | 待修复 | R0 | 影响 ISRC 安全门禁可信度 | In Progress | 需防止正常写入被误拦截 |
| R03 | P0 | A | loose/insufficient 逐字提前截断后续更高质量源 | `need_more()` 仅判断是否为 word 级别 | `core/aggregator.py` | `test_pipeline.py`, `test_aggregator.py` | 提前截断搜索 | 待修复 | R0 | 影响歌词仲裁与高质量源召回 | Pending | 需维护 strict 源低耗时特性 |
| R04 | P0 | A | 本地歌手未知时仍可能放行 `isrc_write_eligible` | `has_full_identity` 仅校验远端歌手 | `core/fetchers/lrc_red.py` | `test_lrc_red.py`, `test_isrc_integration.py` | 漏判本地歌手导致误写 | 待修复 | R0 | 影响音频标签元数据安全 | Pending | 仅支持明确本地歌手写回 |
| R05 | P1 | A | byModel=False 直接等同于 origin_status='official' | 来源声明语义混淆 | `core/fetchers/lrc_red.py` | `test_lrc_red.py` | 过度承诺官方认证 | 待修复 | R0 | 影响溯源真实性 | Pending | 无 |
| R06 | P1 | B | 录音版本硬门禁覆盖不足，存在冲突候选误选 | 版本冲突词库不全且缺双向校验 | `core/utils/text_match.py` | `test_text_utils.py` | 存在版本错配隐患 | 待修复 | R0 | 影响版本隔离 | Pending | 需避免误伤同名合集 |
| R07 | P1 | A | 非法时间戳字符串被转为合法 0ms | `parse_time_string_to_ms` 异常返回 0 | `core/utils/time_utils.py`, `core/parsers/lrc_red_ttml.py` | `test_parsers.py`, `test_lrc_red.py` | 非法时间戳伪装 0ms | 待修复 | R0 | 影响逐字歌词时间轴 | Pending | 需严格保持合法 0ms |
| R08 | P1 | A | 逐字覆盖率仅按行统计，内部文本丢失无法察觉 | 仅以行内存在 words 判定原生逐字 | `core/parsers/lrc_red_ttml.py` | `test_parsers.py` | 丢词行被误标原生逐字 | 待修复 | R0 | 影响歌词保真度 | Pending | 需适配各种语言分词 |
| R09 | P1 | A | 重叠与推断时间计数器恒为 0 | 统计逻辑缺失实际累加 | `core/parsers/lrc_red_ttml.py` | `test_parsers.py` | 质量计数器失真 | 待修复 | R0 | 影响质检可解释性 | Pending | 无 |
| R10 | P1 | B | 复杂 TTML 译文段盲目挂载 lines[-1] 产生错配 | 缺乏基于时间/ID 的关联匹配 | `core/parsers/lrc_red_ttml.py` | `test_parsers.py` | 多声部译文可能错位 | 待修复 | R0 | 影响双语歌词展示 | Pending | 无 ID 时按时间临近与声部兜底 |
| R11 | P1 | B | Apple / iTunes 未全链路传递 14.0s 绝对超时预算 | 底层请求未继承剩余 deadline | `core/fetchers/apple_ttml.py`, `itunes_client.py` | `test_apple_ttml.py` | 长耗时超限风险 | 待修复 | R0 | 影响 T1 响应时效 | Pending | 外部连接不可取消时需受控超时 |
| R12 | P1 | B | lrc.red 备用端点 429 未统一熔断冷却 | 错误未共享熔断器状态 | `core/fetchers/lrc_red.py` | `test_lrc_red.py` | 重复触发 429 封禁 | 待修复 | R0 | 影响服务可用性 | Pending | 需设置合理冷却时长 |
| R13 | P2 | A | lrc.red 缓存仅限 500 条，不限总内存字节 | 内存预算未受控 | `core/fetchers/lrc_red.py` | `test_lrc_red.py` | 极端大响应内存无界 | 待修复 | R0 | 影响长期运行稳定性 | Pending | LRU 淘汰开销 |
| R14 | P1 | A | 网易响应缺乏嵌套类型防御与歌手深度核验 | 只验证顶层 dict，易选同歌名不同歌手 | `core/fetchers/netease.py` | `test_netease_yrc.py` | 错歌率与崩溃隐患 | 待修复 | R0 | 影响网易源准确性 | Pending | 需保持召回率 |
| R15 | P1 | B | LRCLIB 搜索缺少标题歌手严密校验，35s 时长过宽 | 兜底源缺乏防错门禁 | `core/fetchers/lrclib.py` | `test_pipeline.py` | 兜底选错歌 | 待修复 | R0 | 影响低优先级源交付质量 | Pending | 需避免过度拦截冷门曲 |
| R16 | P1 | A | 七源逐字与身份质量字段不一致 | 各 fetcher 输出字段规范分散 | `core/aggregator.py`, 各 fetcher | `test_aggregator.py` | 调度仲裁逻辑脆弱 | 待修复 | R0 | 影响多源评分一致性 | Pending | 需向下兼容旧字段 |
| R17 | P1 | B | 实际容器测试样本不完整，写入失败状态混淆 | 缺少部分真实格式，日志报 success | `scripts/process_song.py`, `tests/` | `test_isrc_integration.py` | 失败仍报 success | 待修复 | R0 | 影响批处理审计真实性 | Pending | 无真实音频夹具者如实标记 |
| R18 | P1 | A | 离线测试未加装网络 Tripwire 拦截器 | 测试套件可能意外访问公网 | `tests/_testkit.py` | `tests/test_tripwire.py` | 离线测试逃逸未被发现 | 待修复 | R0 | 影响 CI 可重复性与离线保证 | Pending | 需允许本地回环测试通过 |
| R19 | P1 | B | 缺少真实曲库 30–50 首前置冻结 A/B 对照证据 | 无基线真实音频 A/B 数据 | `docs/validation_tracks_manifest.csv` | A/B 评测脚本 | 缺乏真实表现度量 | 待修复 | R0 | 影响质量可追溯性 | Pending | 样本需完全隔离且保护原文件 |
| R20 | P2 | A | 缓存路径与临时文件默认可能触碰 C 盘 | 缺乏环境预检与环境变量隔离强制 | `core/utils/cache_paths.py` | `test_pipeline.py` | 潜在磁盘污染风险 | 待修复 | R0 | 影响母盘与系统安全 | Pending | 保持标准用户跨平台兼容 |
| R21 | P2 | C | Python 3.8+ 及全容器兼容声明未全部经过 CI 检验 | 声明范围超出实际测试覆盖 | `README.md`, `tests.yml` | CI 与文档 | 声明与实测不符 | 待修复 | R0 | 影响分发声誉 | Pending | 诚实陈述验证环境 |
| R22 | P0 | A | 已发布 v1.8.1 tag 与失败 CI 不闭环 | CI 挂起但 tag 已打且 release 已发 | Git tag, GitHub Release | CI 工作流 | 破损版本流出 | 待修复 | R0 | 影响交付物质量可靠性 | Pending | 整改全绿后覆盖更新 tag |
| R23 | P2 | C | lrc.red User-Agent 署名及来源描述缺乏权威规范 | UA 格式未规范，官方措辞不严密 | `core/fetchers/lrc_red.py`, 文档 | `test_lrc_red.py` | 溯源表述不够严谨 | 待修复 | R0 | 影响合规与外部协作 | Pending | 严禁泄露本地绝对路径 |
| R24 | P2 | C | 文档与 Agent 安装指令中存在不当修饰与无关动作 | SKILL/README 夹带无关命令 | `README.md`, `SKILL.md` | 文档复核 | 侵入用户环境 | 待修复 | R0 | 影响用户体验 | Pending | 保持纯粹与专业 |
