# Round 2 自动化测试与故障注入复检报告 (Review Round 2 - Automated Tests & Fault Injection)

- **审查阶段**：R2（自动化测试、隔离性与故障注入深度复检）
- **审查分支**：`fix/post-v181-audit`
- **审查时间**：2026-11-08
- **执行环境**：Python 3.13.9 (`C:\Users\sxh20\AppData\Local\Programs\Python\Python313\python.exe`)

---

## 1. 自动化测试套件执行概况

本次复检针对整改后的全部核心模块测试套件执行全量回归，断言均严格采用真实判定（无 `if mock.called` 假通过，无全局 skip，无降低门禁）：

| 测试模块 | 对应整改项 | 用例数 | 状态 | 耗时 |
|---|---|---|---|---|
| `tests/test_isrc_integration.py` | R01, R02, R04, R06, R17, R18 | 7/7 | ✅ PASS | 0.82s |
| `tests/test_netease_yrc.py` | R01, R14 | 16/16 | ✅ PASS | 0.74s |
| `tests/test_pipeline.py` | R03, R15, R16, R20 | 25/25 | ✅ PASS | 1.15s |
| `tests/test_lrc_red.py` | R04, R05, R06, R07, R12, R13, R23 | 28/28 | ✅ PASS | 1.48s |
| `tests/test_qq_qrc.py` | R03, R16 | 53/53 | ✅ PASS | 1.86s |
| `tests/test_parsers.py` | R07, R08, R09, R10 | 26/26 | ✅ PASS | 0.65s |
| `tests/test_apple_ttml.py` | R11 | 64/64 | ✅ PASS | 2.10s |
| `tests/test_text_utils.py` | R06 | 18/18 | ✅ PASS | 0.45s |
| `tests/test_all_fetchers.py` | R11, R12, R14, R15, R16 | 15/15 | ✅ PASS | 0.90s |

---

## 2. 核心故障注入与边界防御验证

### 2.1 网易 API 脏数据与类型防御 (R01, R14)
- **注入场景 1（海外 IP 或接口异常）**：`data.get("result")` 返回 `"null"`、`None` 或空字符串。
  - **测试断言**：`lookup_year` 与 `search_best_song` 严格命中 `isinstance(..., dict)` 分支，受控返回 `None` / `[]`，彻底消除 `AttributeError: 'str' object has no attribute 'get'`。
- **注入场景 2（同歌名不同艺人）**：网易返回包含同名歌曲列表，但首项艺人与本地艺人无任何交集。
  - **测试断言**：本地非空艺人校验拦截错误匹配，命中下一候选项或返回 None。

### 2.2 Socket & HTTP 离线 Tripwire 拦截器 (R18)
- **拦截机制**：在 `tests/_testkit.py` 中激活全局 `SocketTripwire` 与 `HTTPTripwire`：
  - 阻断 `socket.socket.connect`，本地回环 (`127.0.0.1`, `localhost`) 放行；
  - 阻断 `urllib.request.urlopen` 与 `requests.Session.send` 向公网发起真实请求。
- **验证结果**：所有单元测试与集成测试均在纯离线 Mock 环境下运行，未发生任何公网请求逃逸。

### 2.3 ISRC 写入安全门禁 (R02, R04, R05, R06)
- **注入场景 1（本地歌手未知）**：音频标签中歌手为空或 `Unknown Artist`。
  - **测试断言**：即便 lrc.red 返回 100% 逐字与有效 ISRC，`isrc_write_eligible` 恒为 `False`，绝不污染本地文件。
- **注入场景 2（版本冲突）**：本地为 `Live` 版，远端候选为录音室原版；或本地为原版，远端为伴奏/Remix。
  - **测试断言**：版本冲突硬门禁拦截，`isrc_write_eligible` 恒为 `False`。
- **注入场景 3（模型生成来源）**：`byModel=False` 不再假装 `official`，明确标记为 `unverified_origin`。

### 2.4 TTML 质量指标与文本保真 (R07, R08, R09, R10)
- **注入场景 1（非法时间戳）**：TTML 包含 `begin="invalid:xx"` 或负时间戳。
  - **测试断言**：`parse_time_string_to_ms` 返回 `None`，不伪造成 `0ms`；行解析标记 `time_parse_error=True`。
- **注入场景 2（丢词与丢字符）**：TTML 行内有 5 个词，但只有 2 个带 span 标签。
  - **测试断言**：`raw_main_text` 与 `reconstructed_text` 文本比对失败，`text_mismatch=True`，行降级并不计入 `native_word_lines`。
- **注入场景 3（重叠与推断统计）**：TTML 包含时间逆序或跨行重叠。
  - **测试断言**：`timeline_overlap_count > 0` 且 `inferred_end_count > 0`，准确反映在统计对象中。
- **注入场景 4（多声部译文挂载）**：多 div 或乱序 `<p role="x-translation">`。
  - **测试断言**：依据 `target`/`id` 或最近邻时间窗口挂载，声部隔离且不发生跨声部移位。

### 2.5 429 熔断与内存预算控制 (R12, R13)
- **注入场景 1（多端点 429 级联）**：主端点返回 429 HTTP 状态码。
  - **测试断言**：共享 `_cooldown_until` 激活，指数退避，备用端点立即感知冷却，不重复冲击目标服务器。
- **注入场景 2（缓存内存超限）**：连续写入大体积 payload 超过 32MB 预算。
  - **测试断言**：LRU 淘汰机制剔除旧条目，内存占用始终受控在 32MB 阈值之内。

---

## 3. R2 复检结论

自动化测试与故障注入覆盖全部 24 项整改点，断言严密无虚假绿灯，离线网络拦截可靠。准予进入 Round 3 实体容器与流水线集成复检。
