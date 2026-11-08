# music-tag-lyrics v1.8.1 审计整改初始基线 (Baseline Snapshot)

- 记录时间：2026-11-08
- 开发仓库：`F:\TOOLs\AI's DATA\ABC\Music\Work\music-tag-lyrics\`
- 审计基线 SHA：`963e733a08437d65f0abac81276d4eff216862b5`
- 当前标签：`v1.8.1`（指向 `963e733a08437d65f0abac81276d4eff216862b5`）
- 比较基准：`668450e3f3e717f7f5a54d8e8359f65b6db8ac01` (v1.8.0)
- 修复分支：`fix/post-v181-audit`

---

## 1. Git 状态与历史

```text
Commit SHA: 963e733a08437d65f0abac81276d4eff216862b5
Commit Log (Last 5):
963e733 fix(v1.8.1): TTML 跨行角色与缺 end 容错 · M06 冲突等分候选消歧安全 · CI 全量 24 套件 385 tests
da1b3b1 feat(v1.8.1): lrc.red 全球网络健壮化与 ISRC 安全治理 (382 tests passing)
668450e feat(v1.8.0): 集成 lrc.red 全球 ISRC 逐字歌词网络与跨格式元数据治理
0e042ab feat(v1.7.0): QQ 音乐 QRC 逐字通道 · 七源调度 · need_more 判据修复
0157b9f docs: add environment isolation and storage discipline (never encroach on C drive)
```

工作树初始状态干净（无未提交更改）。

---

## 2. GitHub Actions CI 失败堆栈记录

- CI 工作流运行：`42464379731` (<https://github.com/sxsxhhi/music-tag-lyrics/actions/runs/42464379731>)
- 运行环境：`ubuntu-latest` / Python 3.13
- 失败步骤：第 65 步 `ISRC integration & container safety` (`python tests/test_isrc_integration.py`)
- 失败用例：
  1. `test_i01_local_duration_missing_no_isrc_write`
  2. `test_i02_remote_duration_missing_no_isrc_write`
  3. `test_i03_body_dur_only_no_isrc_write`
  4. `test_i04_existing_isrc_never_overwritten`
  5. `test_i05_no_tags_and_lyrics_only_never_write_isrc`
  6. `test_f03_no_tags_mode_preserves_audio_hash`
- 关键报错堆栈：
  ```text
  AttributeError: 'str' object has no attribute 'get'
  at core/fetchers/netease.py:65 in search_best_song:
      songs = data.get("result", {}).get("songs", [])
  at core/fetchers/netease.py:107 in lookup_year:
      song = cls.search_best_song(title, artist, duration_ms)
  at scripts/process_song.py:581 in process_single_song:
      looked = album_year or NetEaseFetcher.lookup_year(cleaned_title, normalized_artist, duration_ms)
  ```

### 根因定性
1. **外部结构防御缺失（生产代码缺陷）**：网易 API 在海外 IP 或受限场景下，返回的 JSON `result` 为字符串（如错误说明），`data.get("result", {})` 取出字符串后调用 `.get("songs", [])` 引发 `AttributeError`。
2. **测试离线隔离缺失（测试代码缺陷）**：`tests/test_isrc_integration.py` 在执行 `process_single_song()` 时，未对 `NetEaseFetcher.lookup_year` 与 `fetch_cover_image` 进行 Mock，导致离线集成测试穿透至公网。
3. **断言虚假通过风险（测试代码缺陷）**：`if mock_update.called: assert ...` 当未触发写入时根本不执行断言，存在假阳性漏洞。
4. **缺乏全局离线拦截屏障**：测试执行器未安装 Socket/HTTP Tripwire，公网调用逃逸无法被自动捕获。

---

## 3. 本地环境基线与测试结果

- 本地系统：Windows 11 (pwsh)
- 运行解释器：`C:\Users\sxh20\AppData\Local\Programs\Python\Python313\python.exe` (Python 3.13.12)
- 声学环境（备用）：`F:\TOOLs\AI's DATA\ABC\Music\Work\AI_Audio_Env\Scripts\python.exe`
- 本地 `python tests/run_all.py -v` 初始执行结果：
  - 套件数：24 个套件
  - 用例数：385 passed, 11 skipped, 0 failed (38.5s)
  - 11 skipped 均为声学/CUDA/粤语模型缺失用例，符合设计预期。
  - 本地能“通过”系因境内 IP 网易响应正常且被 `if mock_update.called` 掩盖。

---

## 4. 安全红线与隔离设置

- 唯一开发与测试目录：`F:\TOOLs\AI's DATA\ABC\Music\Work\music-tag-lyrics\`
- 母盘目录 `F:\TOOLs\AI's DATA\ABC\Music\只音\` 绝对受保护，严禁读取、修改与写入。
- 建立测试隔离沙箱：`.test-work\`（已加入 `.gitignore`）：
  - `.test-work\fixtures\`
  - `.test-work\temp\`
  - `.test-work\out\`
  - `.test-work\logs\`
  - `.test-work\reports\`
  - `.test-work\cache\`
