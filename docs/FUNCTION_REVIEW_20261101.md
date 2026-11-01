# 功能完整性与冗余功能审计

> 日期：2026-11-01  
> 范围：`agent_core/`（133 个 py，26,625 行）、`console/`（含 5,391 行的 `server.py`、15 个 js 5,195 行、10 个 css 5,684 行）、`tests/`（73 个 py，8,542 行）、`scripts/`、文档与部署文件。  
> 方法：路由表 ↔ 前端调用 ↔ 文档承诺三方对账；Python 调用图（含函数内延迟 import）；死代码全仓引用扫描；数据库表读写方核对。  
> 限制：本机没有可用的 Python 解释器（`python.exe` 是 Windows 商店存根，`python --version` 退出码 1），Docker CLI 被沙箱拦截，因此**没有复跑 645 项测试和 ruff**。下文所有结论均为静态证据，行号以当前工作区为准。

> **状态更新（2026-11-01 晚）**：审计后已获得执行权限，P1 两项（F1/F2）与
> F3/F4/F5/F7/F8/F11、R3/R4/R16、主要前端死代码与性能问题已修复；
> 基线 645 测试 → 修复后 **705 测试全绿、ruff 全绿**。逐项见 **§5 修复记录**。
> 本报告保留审计时的原始证据，部分行号会因修复而漂移。

---

## 0. 结论摘要

| 维度 | 结论 |
|---|---|
| 核心网关 `/v1` | ✅ 主链路完整：鉴权、身份、人格、时间轴注入、事实/图、工具闸门、流式、账本、审计都在线 |
| 控制台 | ⚠️ 桌面端可用；**移动端无导航入口**，**没有退出登录**，**身份不能解绑** |
| 记忆子系统 | ❌ **可召回**记忆被锁死在约 4,000 字/人（控制台仍显示全部）；向量召回、jieba+FTS、长程日记、L3 详情四条能力**只写不读或只测不用** |
| 冗余规模 | ❌ 约 **3,800 行 Python 不可达/仅测试引用**（占生产代码 14%），另有约 700 行死前端与 CSS |
| 重复入口 | ⚠️ 至少 **8 组**同一功能两条入口（账本、工具权限、绑定、好感度、凭证编辑、模型保存…） |
| 文档一致性 | ⚠️ 至少 **16 处**文档描述与代码不符（召回、三层监控、测试数、桥接器、P4 插件、重复交接文档…） |
| 发布阻断 | 未发现 P0（本轮只审功能面，未做渗透测试）；发现 **2 项 P1** |

**最该先看的五件事**

1. 时间轴换页是**单行道**：超过 4,000 字的事件被标 `archived`，而检索排除 `archived`、回填条件恒不成立 → AI 实际只能想起最近约 4,000 字，但控制台仍显示全部（§1.2 F1）。
2. 文档反复宣传的「三维召回 + 单模块配额 + 向量 + jieba FTS」是**只被测试调用的死路径**，生产只用 `LIKE` + 曲线注入（§2.2 R1）。
3. `agent_core/mcp`、`plugins`、`bridge`、`source`、`module_manager` 五块约 **2,260 行**没有任何生产消费者（`mcp` 甚至已经 import 断链）；`connectors` 与 `adapters/astrbot` 经确认**保留**，已接入工具页插件扫描（§5）。
4. `/api/overview` 每次都给前端算一份**没人读**的 `modules`（含 TCP 探针），而 `/api/tokens` 被 **每 5 秒**轮询，每次重算 5 个聚合 + 重读 174KB JSON（§2.5）。
5. 控制台**没有退出登录按钮、手机上没有导航、绑定错档案无法撤销**（§1.3）。

---

## 1. 功能完整性

### 1.1 已核对可用的主链路

| 能力 | 证据 | 结论 |
|---|---|---|
| OpenAI 兼容 `/v1/chat/completions`（流式/非流式） | `console/server.py:1970-1975`、`4871-5083` | ✅ 完整，含 SSE、`[DONE]`、兜底话术 |
| `/v1/models` | `console/server.py:1003-1009` → `model_service.py:1141` | ✅ 返回 OpenAI list 形状 |
| 客户端 Key / 限流 / 访客降级 | `agent_core/clients.py:111-149` | ✅ 默认 `require_key=true`，无配置一律 401 |
| 身份 → 档案 → 好感度 | `server.py:3006-3109`、`profile/archives.py` | ✅ 白名单 + `platform_bindings` 双通道 |
| 人格注入（四段式人设卡 + 好感度温差） | `agent_core/persona.py:177-267`、`server.py:3328-3346` | ✅ |
| 时间轴注入（曲线压缩 + 预算守恒） | `server.py:3091-3109`、`memory/timeline_curve.py` | ✅ |
| 事实层 + 图关系注入 | `server.py:3294-3321`、`memory/facts.py:556-584` | ✅ |
| 指令式深时检索（非流式 + 流式限深） | `server.py:4554-4581`、`5005-5020` | ✅ |
| 工具闸门（分类/权限/压缩/动态申请） | `agent_core/tool_shelves/gate.py`、`server.py:3362-3548` | ✅ 测试覆盖最厚 |
| 系统工具（host / ledger）仅管理员 | `server.py:3397-3496`、`host_control.py`、`ledger_tool.py` | ✅ |
| Token 账本（实际模型、分时价格、币种分桶） | `server.py:3834-3843`、`model_service.py:1065-1117` | ✅ |
| 记忆导出/导入（6 表 + 备份 + 审计） | `memory/portability.py`、`server.py:1257-1296` | ✅ 原生格式完整 |
| 签名 / local_only / 静态路径穿越防护 | `api_security.py`、`server.py:963-1001` | ✅ |
| 前端路由完整性 | 62 个 handler 全部存在，`fetch()` 无 404 目标 | ✅ 无一处调用已下线前缀 |

### 1.2 P1：记忆只进不出（核心能力打折）

**F1. 时间轴归档后永远检索不到，回填实际是空操作**

- 每次回复结束都会 `_timeline_release`：`server.py:4144`（非流式）、`server.py:4886`（流式）。
- `release_tail` 把超出 `timeline_keep_chars`（默认 **4,000** 字）的旧事件标成 `visibility='archived'`：`agent_core/storage/repos/buffer.py:100-104`。
- 两条读取路径都排除 archived：
  - `list_candidates`：`agent_core/storage/repos/timeline.py:233`
  - `search_narratives`：`agent_core/storage/repos/timeline.py:290`
- 回填 `_timeline_restore` 只在**检索命中之后**调用（`server.py:4655`、`3718`），未命中直接 return（`server.py:4651-4653`）；而 `free = keep - used` 里的 `used` 又来自排除 archived 的 `list_candidates`（`server.py:3669-3673`），所以腾出的余量接近 0。
- 测试用 `assertGreaterEqual(after, before)` 掩盖了这一点：`tests/test_timeline_release.py:93`。

**影响**：跨平台连续记忆的**可召回**视界是「最近约 4,000 字/人」。更早的内容仍在 `memory.db` 里，但提示词、深时检索、`/hub/v1/memory/search` 都到不了；缓冲区无 TTL、无清理（`clear_buffer` 零调用）。更矛盾的是 `/api/timeline` 走的是 `list_events`（`timeline.py:161-198`，**不过滤 archived**），所以**控制台里看得见、AI 却想不起来**。这与 README「同一套事实层召回 / 翻旧账」的定位直接冲突。

**复现步骤（建议在测试端执行）**

```text
1. 建 10 条 narrative=1000 字的时间轴事件（person_id=arc_master）。
2. 触发一次 /v1 完成（或直接调 _timeline_release）→ 6 条变成 visibility='archived'，进 timeline_buffer。
3. POST /hub/v1/memory/search   → 用第 3 条的独有关键词搜，返回空（search_narratives 排除 archived）。
4. GET /api/timeline            → 仍显示 10 条（list_events 不过滤 archived）→ 界面与 AI 视野不一致。
5. 再触发一次 _timeline_restore → 恢复 0 条（free = 4000-4000 = 0）。
```

**建议**：检索时把 archived 纳入候选（用 `visibility IN ('private','archived')` 或在 `search_narratives` 放开），或给 `/api/timeline` 提供「恢复归档」；同时把 `test_restore_only_when_room` 改成断言恢复条数 > 0。

### 1.3 控制台可用性缺口（F2 为 P1，其余 P2/P3）

| ID | 问题 | 证据 | 影响 |
|---|---|---|---|
| F2 | **移动端没有导航入口** | `.sidebar` 在 ≤768px 被 `translateX(-100%)`（`console/static/css/mobile.css:86-104`）；唯一两个菜单按钮，一个内联 `display:none`（`index.html:107`）且 CSS 再 `!important`（`mobile.css:359`），另一个在 `display:none !important` 的顶栏里（`index.html:111,113`）；`.mobile-bottom-nav` 只有 CSS 没有 DOM（`mobile.css:37-83`） | 手机上只能停在仪表盘，其余 7 个页面都进不去 |
| F3 | **没有退出登录** | `handleLogout`（`core.js:640`）全项目 0 引用；`index.html` / `login.html` 无退出按钮；`POST /api/auth/logout` 只有这个死函数会调 | 只能等会话过期或清 Cookie |
| F4 | **身份映射不能解绑** | `identity_map.js:149-165` 把选择改回空时是 `dirty.delete(key)`，不是解绑；`saveIdentityMap` 只 POST `/api/identity/bind`（`identity_map.js:254`）；服务端 `DELETE /api/bindings`（`server.py:2452,2479`）从未被调用 | 绑错档案后无法撤销 |
| F5 | **口语正则清洗只对非流式生效，且用的是无防护版本** | `_out_message` 只在非流式路径调 `backend.clean_persona_reply`（`server.py:4716`）；流式 `_stream_output`（`server.py:5052-5069`）完全不清洗。`PersonaManager.post_process_response` 带代码块/链接防护罩（`persona.py:269-319`）但**零调用**；实际用的 `clean_persona_reply`（`persona_service.py:278-299`）没有防护 | 流式客户端看到的输出与配置的正则无关；非流式还可能误伤 Markdown |
| F6 | **语言切换是半成品** | `index.html` 只有 17 处 `data-i18n`，可见文案约 200 条硬编码中文；字典 87 键中 70 键无人使用（`core.js:53-231`） | 切 English 后 90% 仍是中文 |
| F7 | **「各工具与外接端 Token 消耗分布」永不显示** | 面板 `#panel-modules-breakdown` 默认 `display:none`（`index.html:270-280`），`core.js:446` 读 `data.tokens.modules_breakdown`；后端 payload 无此键（`model_service.py:1093-1117`） | 死面板 + 死渲染代码 |
| F8 | 设置页端口硬编码 `18890`，与默认 `18895` 不符；`#settings-port-input` 不存在 | `index.html:760`、`server.py:42`、`core.js:608-611` | 照页面配置会配错端口 |
| F9 | 顶栏时钟永不显示 | `core.js:394` 读 `data.summary.server_time`；`get_system_summary()` 没有该字段（`system_service.py:299-314`） | 控件空白 |
| F10 | 凭证编辑弹窗没有标题 | `models.js:394` 写 `#cred-edit-title`，`index.html:1014-1096` 无此元素 | 小瑕疵 |
| F11 | 好感度当前值不可编辑 | `PATCH /api/affection/<id>`（`server.py:2401-2421`）无前端调用；档案页只有只读进度条（`archives.js:67-94`） | 只能改上下限 |
| F12 | 算力矩阵/双池槽位无 UI，但每次启动仍拉 `/api/compute/config` | `models.js:851,873,951` 的目标 id 全不存在；`core.js:617` | 无用请求 + 死渲染 |
| F13 | 移动端旧导航、页标题、`tab-bridges` 等残留 | `core.js:299,358,309-311`、`index.html:978` | 维护噪音 |

### 1.4 P2/P3：记忆与网关的语义缺口

| ID | 问题 | 证据 |
|---|---|---|
| F14 | **方案宣传的四层/三维记忆只落地一层** | `agent_core/memory/README.md:14-81` 宣传 MemoryTools（`memory_tools.py` 不存在）、三维召回、24~48h 经历流；实际生产只用曲线注入 + LIKE |
| F15 | **长程日记只写不读** | 写入 `reflection_engine.py:31-75`（维护线程 `server.py:5287`）；`recall_digests` / `list_digests` 无生产调用方；`key_facts` 恒 `[]` |
| F16 | **L3 详情只写不读，且无界增长** | 写：`server.py:3807-3813`；读：`get_detail/list_details` 只有测试；全库无 `DELETE FROM memory_details`，`expires_at` 写了不读 |
| F17 | 文档称模型可调 `read_memory_detail`，代码不存在 | `docs/MEMORY_LAYERS.md:214`（全仓仅此一处） |
| F18 | L1→L2 聚合摘要被所有读取路径过滤掉 | `plugin_activities` 无生产写入方（`repos/ledger.py:66` 的 `insert_activities` 只有测试调用）→ 摘要无「要点」→ `_drop_empty_summaries` 剔除（`episodic_stream.py:68-76`，用于 `server.py:3097`、`system_service.py:250`、`server.py:1190`） |
| F19 | 换页策略与文档不符 | `timeline_swap.py:13-14` 定义了 `TAIL_SHARE/MIDDLE_SHARE` 但从未引用；真正跑的 `buffer.py:142-154` 只按字数一刀切 |
| F20 | 面板 4 个召回参数只喂死路径 | `candidate_chars / candidate_limit / lookback_days / half_life_hours` 只被 `episodic_stream.py:54-62,97-103` 消费；且 `MemoryHub.recall_timeline` 硬编码 `half_life_hours=36.0`（`memory/__init__.py:131`） |
| F21 | `memory_tool_enabled` 只是提示词开关，执行路径不校验（关掉后若模型仍自发写出指令，仍会查库重答） | 消费点只有 `server.py:328-334`；`server.py:4554-4581`、`5005-5020` 不检查该键 |
| F22 | 外部格式导入忽略 `replace` 模式 | `portability.py:246-261` 只有 native 分支传 `mode`；返回值硬写 `"mode": "merge"`（`:173`） |
| F23 | 群聊里回忆不到群友发言（是否符合预期需确认） | `_timeline_events`（`server.py:3111-3119`）同时按 `person_id` 与 `actor_id` 过滤；`tests/test_recall_policy.py:40-56` 的「同群共享」只在死路径 `recall_timeline(include_channel=True)` 上通过 |
| F24 | OpenAI 兼容细节：请求里的 `model` 只做标签（按定位是刻意，但 `/v1/models` 仍列 192 条上游缓存模型，客户端会以为可选）、`reasoning_effort` 不能透传、未知路径返回空 404 而非 JSON error | `server.py:2838-2842,2897-2904`、`model_service.py:90-106` |
| F25 | 声称「三层性能监控（含 Docker 容器级 CPU/内存）」未实现 | `PROJECT_ARCHIVE.md:133-141` vs `deploy_check.py:60-99` 只查容器是否存在；`system_service.py` 无 docker stats，只有进程名规则匹配 |

### 1.5 用户旅程视角

| 旅程 | 状态 | 备注 |
|---|---|---|
| 首次启动 / 建库 / 迁移 / 随机口令 | ✅ | `run_server` → `enforce_password_policy`；口令文件改密后删除 |
| 登录 / 强制改密 | ✅ | 改密吊销旧会话并清 Cookie |
| 退出登录 | ❌ | 无入口（F3） |
| 接入外部客户端 | ⚠️ | 只能在 `data/clients.json` 手填 Key（符合当前 spec，但没有 UI）；`scripts/connect_astrbot.py` 可代劳 |
| 对话（流式/非流式） | ✅ | 含工具透传与兜底 |
| 查看记忆（时间轴/事实/关系） | ⚠️ | 面板正常，但更早的记忆实际不可见（F1） |
| 档案 / 身份绑定 | ⚠️ | 能绑，不能解绑（F4） |
| 工具管理 | ✅ | 全局工具 + 档案级权限（后者在档案页） |
| 账单 / 预警 / 充值 | ✅ | 数字与币种分桶正常；轮询偏重（§2.5） |
| 设置（模型 / 日志 / 自检 / 记忆库） | ✅ | 端口显示与实际不符（F8） |
| 手机访问 | ❌ | 无导航（F2） |

---

## 2. 冗余功能与死代码

### 2.1 量化总览

| 类别 | 规模 |
|---|---|
| Python 生产代码 | 26,625 行 |
| **不可达 / 仅测试引用的子系统** | **≈3,800 行（14%）** |
| 未生效的存储契约 | 524 行（`storage/repositories.py`） |
| 死 API 路由 / 死分支 | ≈300 行 |
| 前端死函数 + 死 UI + 孤儿 CSS | ≈700 行（28 个函数、4 个 DOM 块、约 10 组选择器） |
| 只测试用的测试资产 | 10 个测试文件（1,025 行） |

> 项目自己的 `docs/DEV_RULES.md:57` 写着「不要为了兼容旧路由保留死分支：路由删了就把函数也删掉」。
> 下面这些死代码大多是在「连接中心 / 模块管理 / MCP / 服务注册」下线时只删了路由、没删实现的遗留，
> 属于同类问题的系统性复发，而不是零星疏漏。

### 2.2 不可达子系统（整块）

| 子系统 | 行数 | 生产可达性 | 证据 |
|---|---|---|---|
| `agent_core/bridge/` | 1,093 | 仅 `/api/adapters/attach`（无 UI/无文档客户端，且 `restore()` 启动不跑） | `server.py:2235-2247,5228-5239`；`repos/plugins.py:116-208` |
| `agent_core/connectors/` | 839 | 后台每 60s 扫一次，只写 `platform_contexts`（仅档案页的绑定展示间接读）；`matcher.py` 仅测试 | `server.py:5242-5259,5367`；`discovery.py:38,69`；`repos/contexts.py:186-190` |
| `agent_core/adapters/astrbot/` | 542 | 0（`ComponentScanner`/`HostResolver` 仅测试） | `tests/test_components.py:15`、`test_host_resolver_ai.py:15` |
| `agent_core/source/` | 235 | 仅 `/api/adapters/*` | `server.py:2199-2212` |
| `agent_core/mcp/` | 229 | **0 调用，且 `manager.py:54,103` import 已删除的 `..services`** | `server.py:5208-5215`（函数无调用方） |
| `agent_core/plugins/` | 212 | 仅测试 | `tests/test_plugins.py:15` |
| `agent_core/memory/timeline_swap.py` | 82 | 仅测试 | `tests/test_timeline_swap.py:12` |
| `console/modules/module_manager.py` | ≈490 / 510 | 只喂 `/api/overview` 里无人读的 `modules` 字段（剩下的 `get_module_permissions` 仍在用） | `server.py:1061-1062`；前端全仓无 `data.modules` 读取 |
| `repos/plugins.py` services 段 | 74 | 0 调用（「服务注册」下线残骸） | `repos/plugins.py:210-283` |
| **合计** | **≈3,796** | | |

附带问题：

> **2026-11-01 晚更正**：`agent_core/connectors`（平台自动识别）与
> `agent_core/adapters/astrbot`（AstrBot 宿主/插件扫描）经使用者确认必须保留，
> 并已接入「工具页插件内工具扫描」链路（§5）。上表把它们列为不可达是审计时的
> 静态结论，不再作为删除建议；其余 `mcp` / `plugins` / `bridge` / `source` 仍待决策。

- `agent_core/mcp` 的断链使 `requirements.txt:8` 的 `mcp>=1.0.0` 在中枢进程里**无人 import**（唯一 import 在 `console/mcp_hub_tools.py:34`，运行在 AstrBot 容器）。
- `agent_core/adapters/astrbot` + `auto_provision.py` 写入的 `components` / `hosts` 表在生产侧**只写不读**（`list_components`/`list_hosts` 只被自身与测试调用）。
- `data/topology_config.json`、`modules_state.json`、`modules_custom.json`、`modules_permissions.json` 属于已下线模块体系；最后这个文件根本不存在，导致 `get_module_permissions()` 永远返回默认值。

### 2.3 活代码里的重复入口

| ID | 重复 | 证据 | 规范建议 |
|---|---|---|---|
| R2 | `/api/tokens` 与 `/api/ledger` 函数体完全相同 | `server.py:1089-1095`；注释「只查账本、首屏快」不成立：都调 `get_token_analytics()`（`model_service.py:1065-1117`，5 个聚合 + backfill + 重读 174KB JSON） | 给 `/api/tokens` 做轻量投影 |
| R3 | `/api/tools/shelves` 与 `/api/tools/shelves/status` 同 handler | `server.py:717-718`；后者全仓零引用 | 删别名 |
| R4 | 好感度两套实现 | 死：`/api/affection*` + `_affection_overview`（`server.py:720-721,1641-1664`）；活：档案详情 + `PATCH /api/archives/<id>`（`archives.js:385`、`server.py:2771-2814`） | 删 `/api/affection*` |
| R5 | `GET`/`POST` `/api/models/sync_upstream` 同实现 | `server.py:1078,2299`；前端只用 GET（`models.js:819`） | 删 POST |
| R6 | 身份绑定有多条写入路径 + 两张表 | `/api/identity/bind` 同时写 `archive_identities` 与 `platform_bindings`（`server.py:1432-1440`）；`/api/bindings` 是另一条（`:2250-2271`），`DELETE /api/bindings` 只解绑后者（`:2479-2487`）；discovery 还会自动写一条管理员绑定（`discovery.py:69`） | 保留一条，或让解绑同时清两张表 |
| R7 | 「档案级工具权限」实现两遍 | 死版 `tools_page.js:218-358`（入口 `#tools-policy-panel` 不存在）；活版 `archives.js:175-262` | 删死版 |
| R8 | 两套凭证编辑器 / 两条模型保存路径 / 两个充值弹窗 | 死：`models.js:599-814`（旧行内编辑器 + `saveModelsConfig`）、`#credits-modal`（`index.html:984-1011`，永不 `.show`）；活：`openCredentialEditor`→`saveCredentialsPoolConfig`（`models.js:387-754`）、`#recharge-modal`（`index.html:1234`） | 删死版 |
| R9 | 两套口语正则实现 | `persona_service.clean_persona_reply`（活、无防护）vs `PersonaManager.post_process_response`（死、有防护） | 合并到有防护的那套，并补流式 |
| R10 | 两份 `DEFAULT_RULES` 抄同一份 50 行 | `agent_core/persona.py:20-75`、`console/modules/persona_service.py:42-97` | 单一来源 |
| R11 | `console/backend.py` 把同一份 50 个名字写两遍 | `backend.py:26-82` 与 `:83-139`；`sys.path` 已包含两个根 | 合并为一个 import 块 |
| R12 | `storage/repositories.py`（524 行、87 个 abstractmethod）**没有任何类继承** | `sqlite_store.py:29` 未继承 `Store`，全仓无 `class X(Store)`；实现已漂移 17 个方法 | 改 `Protocol` 或删除 |
| R13 | `FactStore` 与 `FactManager` 双写 `user_facts`，语义不同 | pending 阈值 0.65（`facts.py:521`）vs 0.6（`repos/facts.py:26`），CHANGELOG 说 <0.6 | 收敛到一套 |
| R14 | 三张建了但从不读写的表 | `memory_facts` / `memory_entities` / `memory_relations`：`facts.py:408-468`；注释自认「已没人读」（`facts.py:739`） | 迁移删表 |
| R15 | `ingested_events` 只写不读 | `repos/plugins.py:110` 唯一 SQL | 删 |
| R16 | `GET /api/sniff_key` 永远 404，函数与 `handle_sniff_key`/`sniff_provider_from_key` 无调用 | `server.py:706,1097-1099`；`model_service.py:19-42` | 删（两轮复盘已要求） |
| R17 | 其他零消费者端点 | `/api/connectors`、`/api/connectors/scan`、`/api/bindings`、`/api/knowledge`、`DELETE /api/knowledge/<id>`、`/hub/v1/knowledge/ingest`、`/api/logs/alerts`、`/api/tools/clean`（仅 P4 薄壳用） | 逐个决定「接 UI / 标运维 / 删」 |
| R18 | `models_config.json` 里重复存了 192 条 `preset_templates` | `model_service.py:138` 每次都用上游缓存覆盖；文件仍有 174KB | 保存前剔除 |

### 2.4 前端死代码

- **28 个不可达函数**（全仓引用扫描确认）：`handleLogout`、`navigateMobileTab`、`goToModelSettings`、`logsEsc`、`openCreditsModal/closeCreditsModal/setQuickTopUp/submitCreditsTopUp`、`toggleKeyVisibility`、`getModelReasoningOptions`、`updateReasoningDropdownOptions`、`checkModelReasoningSupport`、`updateCredentialField/onCredentialAuthTypeChange/onCredentialKeyInput`、`onCredentialModelChange`、`saveModelsConfig`、`saveComputeMatrixSlots/saveComputeProvidersConfig`、`testCredentialLatency`、`renderCredentialsPoolConfig`、`providerKeyAt/testProviderConnectionAt/updateProviderFieldAt/updateProviderField/testProviderConnection`、`savePatrolConfig`、`onToolsTargetChange`、`renderTargets/renderPolicyPanel/onPolicyModeChange/savePolicy/deletePolicy/loadPolicies`、`mtRenderSourceFoot`。
- **死 UI 块**：`#credits-modal`（与活着的 `#recharge-modal` 重复）、`#panel-modules-breakdown`（后端无数据）、隐藏的 `#overview-activity-body` 渲染逻辑（`core.js:486-527`）、`#top-navbar` 内部仍是活跃写点（`#view-title`、`#server-time`…）。
- **孤儿 CSS**：`persona.css` 4/5、`overview.css:888-930`、`ledger.css:171-305`、`identity_map.css:184-259`、`components.css:960-976`、`mobile.css:37-83,364-377`、`timeline.css` 的 `.timeline-item`。

### 2.5 每请求 / 每轮询的重复计算

| 现象 | 证据 |
|---|---|
| 每个 `/v1` 请求构造 **1~2 个完整 `MemoryHub`**（连带 `ArchiveManager` + `FactManager` + `ReflectionEngine`；`FactManager.__init__` 还会每次查库并可能写入 4 条硬编码的主人事实） | `server.py:3025,3282`；`profile/manager.py:29-36`；`fact_manager.py:50-74` |
| 每个请求 new 一个 `PersonaManager`（读人设文件 + 规则文件，无跨请求缓存；`persona_manager` 单例存在但从未使用） | `server.py:3331-3333`；`persona.py:323` |
| 每个请求 new 1~4 个 `FactStore`，每次再 new `GraphStore`（开库 + `CREATE TABLE IF NOT EXISTS` + `PRAGMA WAL`） | `server.py:3302,4015,4670`；`facts.py:225-233`；`graph_store.py:22-33` |
| `/api/tokens` 每 5 秒被轮询，每次都跑 5 个聚合 + 两个 backfill 全表扫描 + 读 174KB `models_config.json` | `main.js:36`；`model_service.py:1065-1117`；`system_service.py:98-172` |
| `/api/overview` 一次请求同时重算 `/api/tokens`、`/api/models_config`、`/api/affection`、`/api/timeline` 的内容，并额外跑无人读的模块 TCP 探针 | `server.py:1050-1064`；`module_manager.py:450-476,341-394` |
| 预取与指令检索是两段近乎逐行相同的复制代码 | `server.py:3685-3721` 与 `4626-4662` |

---

## 3. 文档 / 部署 / 测试一致性

| ID | 不一致 | 证据 |
|---|---|---|
| D1 | 测试数三个版本：README 645、DEV_RULES 320+1、静态 `def test_` 657 | `README.md:104`、`docs/DEV_RULES.md:45` |
| D2 | CI 的 ruff 只查 `E9,F63,F7,F82`（语法级），README 让贡献者跑全量 | `.github/workflows/tests.yml:44` vs `README.md:101` |
| D3 | `docs/TOOL_SHELVES.md`、`docs/CONNECT_ASTRBOT.md`、`docs/HANDOFF.md` 反复引用 `astrbot_plugins/astrbot_plugin_hub_tool_executor/`，该目录**在仓库里不存在** | `TOOL_SHELVES.md:341,374`、`CONNECT_ASTRBOT.md:89,139`；`Test-Path astrbot_plugins` = False |
| D4 | `scripts/deploy_p4.py` 硬编码 `D:\Stellaris` → `D:\Stellaris_test`，且同步目标 `astrbot_plugins/...` 不存在 | `deploy_p4.py:26-44` |
| D5 | `docker-compose.yml` 的 `MEMORY_DB_PATH` / `PERSONA_FILE_PATH` / `ROBOT_BASE_DIR` 代码**一个都不读**（实际是 `MEMORY_HUB_DB_PATH` / `data/persona.md`） | `docker-compose.yml:14-17` vs `storage/config.py:31-38` |
| D6 | `deploy_check` 的安全提示与现行默认相反：「没有 clients.json（require_key 默认关闭）」「require_key=false 按默认主控放行」 | `agent_core/deploy_check.py:139,144` vs `clients.py:51,70-73` |
| D7 | 文档仍在描述已 404 的 `/api/plugins`、`/api/bridges/attach`，并称 `bridge_specs` 重启后自动恢复（实际 `restore()` 只在首次 `/api/adapters/attach` 时调用） | `docs/MEMORY_LAYERS.md:79,93,94` vs `server.py:685-689,5228-5239` |
| D8 | `PROJECT_ARCHIVE.md` 的能力清单与实现不符：三维召回、三层监控（Docker 容器） | `PROJECT_ARCHIVE.md:87,133-141` |
| D9 | `agent_core/memory/README.md` 描述一个不存在的四层架构（MemoryTools 模块、24~48h 经历流、`storage.py` 是 DAL） | `agent_core/memory/README.md:14-86` vs 目录实际文件 |
| D10 | `PROJECT_SPECIFICATION.md` 仍把 `memory_facts` 当事实主表并列出双时间字段 | `PROJECT_SPECIFICATION.md:242-277` vs `facts.py:408-468,739` |
| D11 | `console/mcp_hub_tools.py` 默认清洗地址硬编码为某个内网 IP（`192.168.x.x`，应改为 localhost/环境变量） | `mcp_hub_tools.py:46` |
| D12 | `pyproject.toml` 依赖缺 `pyyaml`（Docker 走 requirements.txt 所以镜像没事，`pip install .` 会缺） | `pyproject.toml:22-28` vs `requirements.txt:9` |
| D13 | `tests/test_bilibili_module.py` 因 `modules/bilibili` 不存在**永久 skip**，等于 0 覆盖 | `tests/test_bilibili_module.py:19-20` |
| D14 | `docs/MEMORY_LAYERS.md:505-507` 的深时闸门阈值（2,000/8,000）与代码（700/2,700）差 3 倍 | `agent_core/memory/deep_recall.py:21-22` |
| D15 | `docs/MEMORY_MODULE.md:103-109` 把聚合器职责写成生成 `memory_digests`；实际聚合器只写 `agent_timeline` | `aggregator.py:174-179` vs `reflection_engine.py:55` |
| D16 | 文档自身重复：`PROJECT_ARCHIVE.md` 与 `项目档案.md` 同一篇（措辞略异）；`docs/REFACTOR_HANDOFF.md` 停留在「306 测试、进行中、未推远端」，已被 `docs/HANDOFF.md`（645 测试）取代，但 DEV_RULES 仍指向旧的 | `项目档案.md:1-5`、`docs/REFACTOR_HANDOFF.md:1-6`、`docs/DEV_RULES.md:4-5` |

---

## 4. 修复建议

### 第一批：低风险清理（建议本周内做，约 1,500 行，含前端）

1. 删除 `/api/sniff_key` 路由 + `_get_sniff_key` + `handle_sniff_key` / `sniff_provider_from_key`（两轮复盘已背书）。
2. 删除 `/api/tools/shelves/status` 别名、`/api/affection*`、`/api/logs/alerts`、`POST /api/models/sync_upstream`、`/api/knowledge`（GET/DELETE）与 `/hub/v1/knowledge/ingest`（保留 hub search）。
3. 删除 `agent_core/mcp/`、`_get_mcp_manager`、`tests/test_mcp.py`，并从 `requirements.txt` 移除 `mcp`。
4. 删除 `agent_core/plugins/`、`tests/test_plugins.py`、`agent_core/memory/timeline_swap.py` + 其测试、`connectors/matcher.py` + 其测试、`repos/plugins.py:210-283` + `services` 表；归档 `tests/test_bilibili_module.py`（永久 skip，等于 0 覆盖）。
5. `console/backend.py` 合并重复 import 块，删 5 个死导出。
6. 前端：删除 §2.4 的 28 个函数、`#credits-modal`、死 policy 面板、`#panel-modules-breakdown` 及对应 CSS。
7. 文档：改 D5–D16；把 `MEMORY_LAYERS` 里已 404 的路由删掉；`项目档案.md` 与 `REFACTOR_HANDOFF.md` 合并或删除。

### 第二批：补功能（需要设计确认）

1. **修 F1**：让 `search_narratives` / `list_candidates` 可按需包含 archived，或提供恢复接口；补一条「归档后可召回」的测试。
2. **修 F2/F3/F4**：移动端加一个可见菜单按钮（`.mobile-menu-btn-float` 恢复显示）；设置页/侧栏加退出登录；身份映射支持解绑（调用 `DELETE /api/bindings` 并同时清 `archive_identities`）。
3. **统一输出清洗**：流式也走带代码块/链接防护的清洗；删掉重复实现；补测试。
4. **决定召回路线**：要么把 `recall_timeline` 接回主路径，要么删掉向量 + FTS + 三因子打分，只保留曲线 + LIKE，并同步文档与面板参数。**不要维持「文档说有、代码没有」的现状。**
5. **L3 / digests**：要么提供只读详情接口与摘要注入（真正打通长程记忆），要么停止写入并加清理（隐私 + 体积）。
6. **性能**：`/api/tokens` 轻量化（只回 today/overall/credits）；`get_models_config` 加 mtime 缓存；`/api/overview` 删 `modules` 字段；`MemoryHub` / `PersonaManager` 改进程内单例或注入。

### 第三批：需要产品决策（删或扶正）

| 对象 | 现状 | 选项 |
|---|---|---|
| `agent_core/bridge` + `source` + `/api/adapters/*` | curl-only，1,328 行 | 决定「插件桥接」是否继续；不做就整链删除并改文档 |
| `agent_core/connectors` | **保留**：平台自动识别入口（AstrBot 上下文 → `platform_contexts`），用户明确要求保留 | 已接回工具页链路：新增 `tool_shelves/plugin_scan.py` 扫描插件内 `llm_tool` |
| `agent_core/adapters/astrbot` + `auto_provision` + `components/hosts` 表 | **保留**：AstrBot 宿主识别与插件扫描依赖它（`default_plugin_roots`） | 组件卡可后续接进工具页/档案页，不作为删除项 |
| `module_manager` | 490/510 行只为无人读字段 | 删，保留 `get_module_permissions` 直到档案级策略接管 |
| `console/mcp_hub_tools.py` | 仍匹配当前 API，缺配套插件目录 | 把执行器插件补回仓库，或在文档里明确「可选件、需自备」 |

### 不建议删的

- `/api/archives` 与 `/hub/v1/archives` 的别名（单实现 + `console=True`，刻意复用）。
- `_REMOVED_PATH_PREFIXES`（12 条已下线前缀的 404 契约；建议补测试锁住，而不是删）。
- `/api/tools/shelves` + reload/metrics reset（`TOOL_SHELVES.md` 明确作为运维 curl 入口）。

---

## 附录 A：文档承诺 → 实际状态

| 文档承诺 | 位置 | 实际 | 判定 |
|---|---|---|---|
| OpenAI 兼容网关，流式/非流式 | README:3-5 | 实现 | ✅ |
| 统一连续记忆，跨平台同一档案 | README:3 | 实现 | ✅ |
| 三条召回线索 + 单模块配额 | PROJECT_ARCHIVE:87 | 仅测试调用 | ❌ 死路径 |
| 向量召回 / jieba FTS / LIKE 兜底 | PROJECT_SPEC:233-240 | 只有 LIKE 在线 | ❌ 2/3 死 |
| 事实层双时间（valid_at/invalid_at） | PROJECT_SPEC:250-260 | `user_facts` 只有 status/created/updated，`memory_facts` 空表 | ⚠️ 部分 |
| 图关系由事实自动生成并注入 | PROJECT_SPEC:284-300 | 注入来自 `FactStore.graph()` 现算；`memory_graph.db` 只写 | ⚠️ 两套 |
| 工具货架四类目 + 提权 | PROJECT_ARCHIVE:61-70 | 实现 | ✅ |
| Token 预警 | docs/TOKEN_ALERTS | 实现 | ✅ |
| 记忆库导出/导入 | MEMORY_PORTABILITY | 实现（replace 模式例外） | ⚠️ |
| 性能监控三层（进程树/Docker/规则） | PROJECT_ARCHIVE:133-141 | 只有规则匹配 + nvidia-smi | ❌ |
| 宿主机控制 + 账本查询工具 | ARCHITECTURE:105-111 | 实现 | ✅ |
| 插件适配器契约 + 生成器 | PLUGIN_ADAPTERS | 实现，但无 UI、生成器从未产出 | ⚠️ |
| 连接中心/桥接/模块市场已下线 | SPEC:418-427 | 路由 404，但后端仍在（bridge/module_manager） | ⚠️ 半下线 |

## 附录 B：审计方法与限制

- **路由对账**：从 `_CONSOLE_GET_ROUTES`、`_POST_ROUTES`、`do_PATCH`/`do_DELETE`、`_handle_hub_*` 提取全部路径，与 15 个 JS 文件的 `fetch()` 目标和文档交叉比对；62 个 handler 全部存在。
- **调用图**：正则解析 `from/import`（含相对导入与函数内延迟 import），再对每个可疑符号做全仓 grep，避免把「只被测试调用」误判为「完全未用」。
- **证据等级**：本报告的每条结论都标注了 `path:line`，均为当前工作区实际内容；未运行任何代码。
- **未能验证**：645 项测试是否全绿、ruff 是否干净、Docker 镜像是否可构建、运行时性能数字。建议在 `D:\Stellaris_test` 用空 `data/` 复跑一次后再据本报告动刀。

---

## 5. 修复记录（2026-11-01 晚，本轮已落地）

> 环境：本机 Python 3.14.5 + 依赖齐全，Docker 可用。
> 基线：`645 tests OK (skipped=1)`、`ruff All checks passed`。
> 修复后：`705 tests OK (skipped=1)`、`ruff All checks passed`、15 个 JS 文件 `node --check` 全过。

### 5.1 功能修复

| 编号 | 改动 | 涉及文件 |
|---|---|---|
| **F1** | `search_narratives` 新增 `include_archived`；`[[记忆检索]]`、`/hub/v1/memory/search`、预取统一带 `include_archived=True` —— 换页搬走的旧事件可以重新召回 | `agent_core/storage/repos/timeline.py`、`console/server.py`、`tests/test_timeline_release.py` |
| **F2** | 移动端悬浮菜单按钮恢复显示（去掉 `display:none !important`，补固定定位样式；桌面端仍隐藏） | `console/index.html`、`console/static/css/mobile.css` |
| **F3** | 侧栏新增「退出」按钮，`handleLogout` 从死代码变回入口 | `console/index.html`、`console/static/css/base.css`、`console/static/js/core.js` |
| **F4** | 新增 `POST /api/identity/unbind` + `store.unlink_identity`，同时清 `archive_identities` 与 `platform_bindings`；身份映射页把下拉改回「不配置」即解绑 | `agent_core/storage/repos/archives.py`、`console/server.py`、`console/static/js/identity_map.js`、`tests/test_identity_unbind.py` |
| **F5** | 输出清洗加代码块/行内代码/Markdown 链接防护；流式「完整结果回放」路径也清洗；删除重复的 `PersonaManager.post_process_response` | `console/modules/persona_service.py`、`agent_core/persona.py`、`console/server.py`、`tests/test_persona_clean.py` |
| **F7** | 删除 `#panel-modules-breakdown` 死面板与对应渲染代码 | `console/index.html`、`console/static/js/core.js` |
| **F8** | 设置页端口改为按当前 `location` 动态显示 | `console/index.html`、`console/static/js/core.js` |
| **F10** | 凭证编辑弹窗补回缺失的标题 `#cred-edit-title` | `console/index.html` |
| **F11** | 档案详情新增「当前好感度」输入框，`PATCH /api/affection/<id>` 接回前端 | `console/index.html`、`console/static/js/archives.js` |
| **F21** | `memory_tool_enabled` 在执行侧也校验（非流式/流式都不再绕过）；指令提示词改为流式同样注入 | `console/server.py`、`tests/test_memory_tool.py` |
| **F22** | 工具闸门不再误裁未登记工具：`need_tools=false` / 语气词 / 工具结果回灌统一走 `_tools_for()`；全是未登记工具时跳过分类器；`tool_choice` 仅在强制工具真被裁掉时剥；新增 `tool_permissions.scope=request` 按客户端保留本轮全量工具 | `agent_core/tool_shelves/gate.py`、`console/server.py`、`tests/test_tool_shelves.py` |
| **工具页扫描** | 保留平台识别链路并做成**通用**插件扫描：AstrBot（`metadata.yaml` + `@filter.llm_tool`）、NoneBot、LangBot / Dify 的 manifest、通用 Python 装饰器（`@tool` / `@function_tool` / `@register_tool` / `@mcp_tool` / `@ai_function`）都支持；扫描目录在「设置 → 常规与系统 → 插件工具扫描」里配置（存 `data/plugin_scan.json`，可自动识别）；`list_tools` 合并顺序 配置 → 系统 → 插件扫描 → 链路观测。`connectors` / `adapters/astrbot` 保留 | `agent_core/tool_shelves/plugin_scan.py`、`admin.py`、`console/server.py`、`tests/test_plugin_tool_scan.py` |

### 5.2 冗余清理

| 编号 | 改动 |
|---|---|
| R3 | 删除 `/api/tools/shelves/status` 别名 |
| R4 | 删除 `/api/affection`、`/api/affection/overview` 与 `_affection_overview`（保留 PATCH 供 F11） |
| R16 | 删除 `/api/sniff_key` 路由、`_get_sniff_key`、`handle_sniff_key`、`sniff_provider_from_key` |
| A8 | `module_manager.py` 从 510 行缩到 26 行，只留 `get_module_permissions`；`/api/overview` 不再返回无人读的 `modules` / `modules_permissions`，也不再做 TCP 探针 |
| R11 | `console/backend.py` 合并重复 import 块（140 行 → 52 行），只导出实际使用的名字 |
| R13 | `models_config.json` 不再重复保存 192 条 `preset_templates`（改由上游缓存派生，按 mtime 缓存） |
| 前端 | 删除 28 个不可达函数、旧充值弹窗 `#credits-modal`、旧工具权限面板、`#panel-modules-breakdown`、`#overview-activity-body` 渲染、隐藏桩写入、移动端旧导航与 `.mobile-bottom-nav` CSS；`models.js` 1081 → 538 行 |

### 5.3 性能与一致性

- `get_models_config` / `get_preset_models` 按 mtime 缓存，不再每 5 秒重读 174KB + 152KB JSON。
- `/api/tokens` 走 `get_token_analytics(light=True)`：不查最近流水、不算账本页专用的成本分组。
- 历史流水补算（两次全表扫描）进程内限频 10 分钟，不再跟着 5 秒轮询跑。
- `docker-compose.yml` 删除三个代码根本不读的环境变量（`ROBOT_BASE_DIR` / `MEMORY_DB_PATH` / `PERSONA_FILE_PATH`）。
- `deploy_check` 的客户端 Key 提示改成与现行安全默认一致。
- `pyproject.toml` 补 `pyyaml`。
- 文档：`agent_core/memory/README.md` 按真实调用链重写；`MEMORY_LAYERS` 删除已下线的 plugins/bridges/execution AI 章节并修正流式检索说明；`MEMORY_MODULE` 修正「聚合器生成 memory_digests」；`PROJECT_ARCHIVE` 修正三维召回与性能监控描述；`TOOL_SHELVES` / `CONNECT_ASTRBOT` 标注执行器插件为可选自备件。

### 5.4 本轮未做（仍需决策）

| 项 | 原因 |
|---|---|
| 删除 `agent_core/bridge` / `source` / `mcp` / `plugins`（≈2,400 行） | 属产品决策：bridge 仍挂着 `/api/adapters/*`；`connectors` 与 `adapters/astrbot` 已确认保留（平台自动识别 → 工具页） |
| `storage/repositories.py`（524 行未继承的 ABC） | 改成 `Protocol` 或删除需要同步 `docs/STORAGE.md`，单独一轮做 |
| `FactStore` 与 `FactManager` 双写 `user_facts` | 需要先定「以哪套为准」，再做数据迁移 |
| i18n（F6） | 200 条硬编码文案，需逐页替换并补翻译，工作量大 |
| 流式逐块清洗（F5 的纯透传路径） | 任意正则无法安全地在流式块边界生效；当前只保证「完整结果回放」路径清洗 |
| Docker 容器级性能指标（F25） | 需要新增 docker stats 采集与前端展示，属新功能 |
| `agent_core/mcp` 删除 + `requirements.txt` 去 `mcp` | 依赖 `console/mcp_hub_tools.py`（跑在 AstrBot 容器）是否保留；本次仅记录，不动依赖 |

