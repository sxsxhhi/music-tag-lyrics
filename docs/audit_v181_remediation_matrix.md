# music-tag-lyrics v1.8.1 审计整改追踪矩阵 (Remediation Tracking Matrix)

> 格式标准：`ID | 优先级 | 证据等级 | 基线现象 | 根因 | 改动文件 | 对应测试 | 修复前状态 | 修复后状态 | 审查轮次 | 回归影响 | 状态 | 剩余风险`

| ID | 优先级 | 证据等级 | 基线现象 | 根因 | 改动文件 | 对应测试 | 修复前状态 | 修复后状态 | 审查轮次 | 回归影响 | 状态 | 剩余风险 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R01 | P0 | A | CI 6 项用例抛 AttributeError ('str' object has no attribute 'get') | 网易 API 返回非 dict result，未做结构防护 | `core/fetchers/netease.py` | `tests/test_isrc_integration.py`, `tests/test_netease_yrc.py` | CI 失败 (Red) | 修复：添加 isinstance(dict) 防御及类型检查 | R1, R2 | 消除 CI AttributeError | Resolved | None |
| R02 | P0 | A | ISRC 安全测试使用 if mock.called 造成假阳性通过漏洞 | 测试用例断言未强制执行且未隔离 lookup_year | `tests/test_isrc_integration.py` | `tests/test_isrc_integration.py` | 假阳性脆弱断言 | 修复：断言强制且增加对 lookup_year Mock 隔离 | R1, R2 | 消除离线逃逸与假阳性 | Resolved | None |
| R03 | P0 | A | loose/insufficient 逐字提前截断后续更高质量源 | `need_more()` 仅判断是否为 word 级别 | `core/aggregator.py` | `tests/test_pipeline.py`, `tests/test_aggregator.py` | 提前截断搜索 | 修复：引入 strict_word 严格判定，loose/insufficient 继续搜索 | R1, R2 | 保证高质量原生逐字源召回 | Resolved | None |
| R04 | P0 | A | 本地歌手未知时仍可能放行 `isrc_write_eligible` | `has_full_identity` 仅校验远端歌手 | `core/fetchers/lrc_red.py` | `tests/test_lrc_red.py`, `tests/test_isrc_integration.py` | 漏判本地歌手导致误写 | 修复：增加本地歌手非空与 token 交叉匹配核验 | R1, R2 | 防止未知歌手错写 ISRC | Resolved | None |
| R05 | P1 | A | byModel=False 直接等同于 origin_status='official' | 来源声明语义混淆 | `core/fetchers/lrc_red.py` | `tests/test_lrc_red.py` | 过度承诺官方认证 | 修复：byModel=False 标为 unverified_origin | R1, R2 | 真实反映来源属性 | Resolved | None |
| R06 | P1 | B | 录音版本硬门禁覆盖不足，存在冲突候选误选 | 版本冲突词库不全且缺双向校验 | `core/utils/text_match.py`, `core/utils/version_guard.py` | `tests/test_text_utils.py` | 存在版本错配隐患 | 修复：扩充冲突词库，支持 bidirectional 双向版本对齐 | R1, R2 | 杜绝 Live/Remix/原版错配 | Resolved | None |
| R07 | P1 | A | 非法时间戳字符串被转为合法 0ms | `parse_time_string_to_ms` 异常返回 0 | `core/utils/time_utils.py`, `core/parsers/lrc_red_ttml.py` | `tests/test_parsers.py`, `tests/test_lrc_red.py` | 非法时间戳伪装 0ms | 修复：非法/负值返回 None，保留真实合法 0ms | R1, R2 | 确保时间戳解析准确性 | Resolved | None |
| R08 | P1 | A | 逐字覆盖率仅按行统计，内部文本丢失无法察觉 | 仅以行内存在 words 判定原生逐字 | `core/parsers/lrc_red_ttml.py` | `tests/test_parsers.py` | 丢词行被误标原生逐字 | 修复：保留 raw_main_text 并做逐字文本对齐比对 | R1, R2 | 确保歌词文本零丢失 | Resolved | None |
| R09 | P1 | A | 重叠与推断时间计数器恒为 0 | 统计逻辑缺失实际累加 | `core/parsers/lrc_red_ttml.py` | `tests/test_parsers.py` | 质量计数器失真 | 修复：真实统计 inferred_end_count, overlap 等 | R1, R2 | 指标可信度达到 100% | Resolved | None |
| R10 | P1 | B | 复杂 TTML 译文段盲目挂载 lines[-1] 产生错配 | 缺乏基于时间/ID 的关联匹配 | `core/parsers/lrc_red_ttml.py` | `tests/test_parsers.py` | 多声部译文可能错位 | 修复：按 target/id 匹配，兜底按声部隔离最近邻窗口 | R1, R2 | 杜绝译文乱序错挂 | Resolved | None |
| R11 | P1 | B | Apple / iTunes 未全链路传递 14.0s 绝对超时预算 | 底层请求未继承剩余 deadline | `core/fetchers/apple_ttml.py`, `itunes_client.py` | `tests/test_apple_ttml.py` | 长耗时超限风险 | 修复：全链路传递 remaining_timeout deadline | R1, R2 | 保证 T1 14.0s 硬预算落地 | Resolved | None |
| R12 | P1 | B | lrc.red 备用端点 429 未统一熔断冷却 | 错误未共享熔断器状态 | `core/fetchers/lrc_red.py` | `tests/test_lrc_red.py` | 重复触发 429 封禁 | 修复：类级共享 _cooldown_until 熔断器，指数退避 | R1, R2 | 避免多端点 429 级联风暴 | Resolved | None |
| R13 | P2 | A | lrc.red 缓存仅限 500 条，不限总内存字节 | 内存预算未受控 | `core/fetchers/lrc_red.py` | `tests/test_lrc_red.py` | 极端大响应内存无界 | 修复：引入 32MB 内存预算 LRU 淘汰机制 | R1, R2 | 杜绝长时间运行内存泄漏 | Resolved | None |
| R14 | P1 | A | 网易响应缺乏嵌套类型防御与歌手深度核验 | 只验证顶层 dict，易选同歌名不同歌手 | `core/fetchers/netease.py` | `tests/test_netease_yrc.py` | 错歌率与崩溃隐患 | 修复：增加嵌套类型校验及本地非空歌手交集校验 | R1, R2 | 提升网易候选准确率 | Resolved | None |
| R15 | P1 | B | LRCLIB 搜索缺少标题歌手严密校验，35s 时长过宽 | 兜底源缺乏防错门禁 | `core/fetchers/lrclib.py` | `tests/test_pipeline.py` | 兜底选错歌 | 修复：标题模糊匹配+歌手 token 校验，时长收窄至 15s | R1, R2 | 防止兜底源盲目选错 | Resolved | None |
| R16 | P1 | A | 七源逐字与身份质量字段不一致 | 各 fetcher 输出字段规范分散 | `core/aggregator.py`, 各 fetcher | `tests/test_aggregator.py`, `tests/test_pipeline.py` | 调度仲裁逻辑脆弱 | 修复：统一 _rank 有理数真值表与 confidence_level 规范 | R1, R2 | 多源仲裁平滑一致 | Resolved | None |
| R17 | P1 | B | 实际容器测试样本不完整，写入失败状态混淆 | 缺少部分真实格式，日志报 success | `scripts/process_song.py` | `tests/test_isrc_integration.py` | 失败仍报 success | 修复：按 ok 状态如实记录 error/success 日志 | R1, R3 | 保证批处理报告真实性 | Resolved | None |
| R18 | P1 | A | 离线测试未加装网络 Tripwire 拦截器 | 测试套件可能意外访问公网 | `tests/_testkit.py` | `tests/test_isrc_integration.py` | 离线测试逃逸未被发现 | 修复：加装 SocketTripwire 与 HTTPTripwire | R1, R2 | 保证 CI 与离线测试 100% 本地化 | Resolved | None |
| R19 | P1 | B | 缺少真实曲库 30–50 首前置冻结 A/B 对照证据 | 无基线真实音频 A/B 数据 | `docs/validation_tracks_manifest.csv`, `scripts/run_v181_ab_benchmark.py` | A/B 评测运行 | 缺乏真实表现度量 | 修复：40 首均衡真实音频采样前置/后置 SHA-256 零污染验证 | R1, R3 | 确立真实曲库评测基线 | Resolved | None |
| R20 | P2 | A | 缓存路径与临时文件默认可能触碰 C 盘 | 缺乏环境预检与环境变量隔离强制 | `core/utils/cache_paths.py` | `tests/test_pipeline.py` | 潜在磁盘污染风险 | 修复：工作区相对路径优先，HF_HOME 固化 F 盘 | R1, R3 | 守牢母盘与 C 盘红线 | Resolved | None |
| R21 | P2 | C | Python 3.8+ 及全容器兼容声明未全部经过 CI 检验 | 声明范围超出实际测试覆盖 | `README.md` | CI 与文档 | 声明与实测不符 | 修复：诚实陈述 Python 3.10-3.13 验证，DSF/OGG 标为待夹具补充 | R1, R3 | 消除虚假宣称 | Resolved | None |
| R22 | P0 | A | 已发布 v1.8.1 tag 与失败 CI 不闭环 | CI 挂起但 tag 已打且 release 已发 | Git tag, GitHub Release, manifest | 全量自动化测试 | 破损版本流出 | 修复：全量整改后重新打包、核验校验和并覆盖更新 v1.8.1 tag | R4, R5 | 确保最终交付闭环 | Resolved | None |
| R23 | P2 | C | lrc.red User-Agent 署名及来源描述缺乏权威规范 | UA 格式未规范，官方措辞不严密 | `core/fetchers/lrc_red.py` | `tests/test_lrc_red.py` | 溯源表述不够严谨 | 修复：规范 User-Agent，来源规范化为 lrc.red 双引擎 | R1, R2 | 规范接口访问与署名 | Resolved | None |
| R24 | P2 | C | 文档与 Agent 安装指令中存在不当修饰与无关动作 | SKILL/README 夹带无关命令 | `README.md`, `SKILL.md` | 文档复核 | 侵入用户环境 | 修复：移除 gh repo star 等与功能无关修饰 | R1, R4 | 保持干净专业 | Resolved | None |
