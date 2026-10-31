# 验收报告 · 2026-10-31

> 对象：测试端 `D:\Stellaris_test`（18895 运行实例）+ 开发端 `D:\Stellaris`
> 范围：本轮重构（合并分叉、网关拆分、路由表化、死代码清理、热路径、存储拆分）
> 结论：**P0 严重缺陷 0 项**；P1 待办 1 项（`module_manager.py` 仍有 236 行函数且大部分已无调用方）；
> 305 个测试两端通过，21 项运行时接口验收全绿。

---

## 一、验收环境

```text
中枢进程    D:\Stellaris_test\console\server.py 18895（PID 重启多次后仍稳定）
数据库      data/memory.db  13.2 MB  schema 26  41 张表
模型        主控 Gemini 3.8 Flash / 执行 DeepSeek（凭据池）
测试        unittest，305 项（1 skip：test_bilibili_module，模块已归档）
```

---

## 二、验收清单

### A. 代码质量（静态）

| 项 | 方法 | 结果 |
|---|---|---|
| A1 语法编译 | 全部 151 个 `.py` 过 `ast.parse` | ✅ 0 错误 |
| A2 同文件重复定义 | AST 扫描函数名 | ✅ 0 处 |
| A3 TODO / FIXME / XXX / HACK | 全文扫描 | ✅ 0 处 |
| A4 超 100 行函数 | AST 统计 | ⚠️ 7 个（见 P1） |
| A5 最长函数 | AST 统计 | ⚠️ 236 行（`scan_real_system_topology`） |
| A6 ruff | `ruff check` | ⚠️ 89 处 F401 全是转发导出、4 处 E402（测试内 sys.path 后 import）、4 处 F841 未用变量 |
| A7 文件 BOM | 首字节扫描 | ✅ 8 个已清除（此前让统计脚本漏算 8 个文件） |

> 说明：`console/backend.py` 的 50 处 F401 是 `from .modules.x import y` 后由
> `server.py` 通过 `backend.y()` 调用的转发导出，**不是死代码**，ruff 无法识别这种用法。

### B. 测试

| 项 | 方法 | 结果 |
|---|---|---|
| B1 开发端 | `unittest discover` | ✅ 305 通过 / 1 skip |
| B2 测试端 | 同上 | ✅ 305 通过 / 1 skip |
| B3 覆盖本轮改动 | 网关 E2E、工具货架、清洗、档案、存储迁移 | ✅ 对应用例全绿 |

### C. 网关运行时（21 项，全过）

| 项 | 结果 |
|---|---|
| C1 首页 `/` | ✅ 200 |
| C2 `/api/auth/status` | ✅ 200 |
| C3 `/v1/models` | ✅ 200，192 个模型 |
| C4 非流式对话 | ✅ 200，正常回话 |
| C5 工具调用链路 | ✅ 200，`finish_reason=tool_calls` |
| C6 流式 SSE | ✅ 200，以 `data: [DONE]` 收尾 |
| C7 账本写入 | ✅ `session_type=acceptance`、`tools` 列 |
| C8 时间轴写入 | ✅ 最新事件 `source_module=acceptance` |
| C9 档案读取 `/hub/v1/archives` | ✅ 200 |
| C10 记忆召回 | ✅ 能接上历史话题（读路径通） |
| C11 并发 5 个请求 | ✅ 全部 200，无锁冲突 |
| C12 重启后数据不丢 | ✅ 事件 580 → 580 |

### D. 鉴权与安全边界

| 项 | 方法 | 结果 |
|---|---|---|
| D1 匿名 `POST /api/tools` | 无 session | ✅ 401（历史上是公开可写） |
| D2 匿名 `POST /api/tools/policy` | 无 session | ✅ 401 |
| D3 匿名 `POST /api/archives` | 无 session | ✅ 401 |
| D4 匿名 `DELETE /api/bindings` | 无 session | ✅ 401 |
| D5 `POST /api/tools/clean` | 薄壳专用 | ✅ 200 放行（设计如此） |
| D6 错误 API Key | `Bearer wrong-key` | ✅ 401 |
| D7 已下线路由 | `/api/modules` 等 4 条 | ✅ 404 |
| D8 源码密钥自检 | `scripts/check_no_secrets.py` | ✅ 未发现真实账号或 Key |

### E. 数据完整性

| 项 | 结果 |
|---|---|
| E1 schema 版本 | ✅ 26 |
| E2 表数量 | ✅ 41（含 timeline FTS 索引表） |
| E3 关键行数 | ✅ 事件 580 / 流水 457 / 档案 2 / 事实 8 / 身份 2 |
| E4 账本分桶 | ✅ master·USD 455 笔 $0.5673；function·CNY 2 笔 ¥0.0005 |
| E5 配置文件 | ✅ models_config / compute_providers / auth_config / clients / tool_shelves 均合法 JSON |
| E6 备份 | ✅ backups 下 30 个 `.db` 快照，最新 `pre_migrate_20261031_013843` |

### F. 部署与重启

| 项 | 结果 |
|---|---|
| F1 杀进程 → 重启 | ✅ 端口恢复、无残留进程 |
| F2 日志轮转 | ✅ `.prev` 正常生成 |
| F3 `err.log` | ✅ 仅一条「Gemini 连接超时 → 切换备用凭证」，属预期降级 |

### G. 两端一致性

| 项 | 结果 |
|---|---|
| G1 源码 | ✅ 149 个 `.py` 逐文件 hash 一致；测试端多 2 个部署脚本 |
| G2 文档 | ✅ HANDOFF / REFACTOR_HANDOFF 已同步 |

---

## 三、指标对照（真实基线，已修正 BOM 漏算）

| 指标 | 基线 `00c434f` | 现在 | 变化 |
|---|---|---|---|
| 函数总数 | 1518 | 1711 | +193（拆出来的小函数） |
| > 20 行函数 | 297 | **276** | −21 |
| > 50 行函数 | 80 | **60** | −20 |
| > 100 行函数 | 18 | **7** | −11 |
| 最长函数 | 1021 行 | **236 行** | −785 |
| `console/server.py` | 3781 行 / 8 个 >100 | 3300 行 / **0 个** | 入口函数 1033 → 16 |
| `agent_core/storage/sqlite_store.py` | 2003 行 / 105 方法 | **88 行门面** + 10 个领域模块 | 结构拆分 |
| 测试 | 298 | **305** | +7（新增 18、删 11 个死模块用例） |

---

## 四、问题清单

### P0 · 严重缺陷
**0 项。**

### P1 · 应尽快处理（1 项）

**`console/modules/module_manager.py` 仍是最大的单体**

```text
236 行  scan_real_system_topology      （/api/modules/topology 路由早已 404）
123 行  execute_module_probe           （只被 probe_single_module / probe_all_modules 调用）
 48 行  get_modules_overview           （唯一还活着的：/api/overview 在用）
```

外部调用只剩 `get_modules_overview`（1 处）和 `get_module_permissions`（2 处），
其余约 400~500 行属于「路由没了、代码还在」。建议：要么归档整块扫描能力，
要么只保留 `get_modules_overview` 并拆掉 236 行那个函数。

### P2 · 记录项（不阻塞）

1. `console/backend.py` 50 处 F401 是转发导出，建议加 `# noqa: F401` 或显式 `__all__`，否则 lint 一直红。
2. 4 处 F841 未使用变量（`compute_providers` ×2、`spec_connector` ×1、`module_manager` ×1）。
3. 8 个文件曾有 UTF-8 BOM，导致我自己的统计脚本一度漏算（已清、已修正基线）。
4. `recall_timeline` 28 行里 17 行是参数签名，属已知例外。

---

## 五、结论

- **没有发现重构引入的严重 bug**：网关三条路径、账本/时间轴写入、鉴权边界、并发、重启持久化全部实测通过。
- **代码掌控度**：`server.py` 从 3781 行 / 8 个超 100 行函数降到 3300 行 / 0 个；存储层从 1 个 2005 行的类拆成 10 个领域模块；死代码累计删除约 1400 行。
- **唯一需要盯的是 `module_manager.py`**：它不在本轮重构范围内，但已经是新的「最大单体 + 半死代码」。
- 结论：**可以视为重构完成，允许推送远端**；`module_manager` 作为下一轮的首要项。
