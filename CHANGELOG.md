# 更新记录

## 2026-11-01（第二轮：功能审计修复）

> 审计报告：[docs/FUNCTION_REVIEW_20261101.md](docs/FUNCTION_REVIEW_20261101.md)。
> 测试 645 → **679 全绿**，ruff 全绿。

### 功能

- **归档记忆可召回**：`search_narratives` 新增 `include_archived`，`[[记忆检索]]`、`/hub/v1/memory/search` 与预取统一带 `True`。修复「超过 4,000 字的旧事件搬进缓冲区后永远想不起来」
- **记忆检索开关真正生效**：`memory_tool_enabled` 在执行侧校验；指令提示词改为流式/非流式都注入（流式 `_stream_recall` 本来就在）
- **移动端导航**：恢复悬浮菜单按钮（此前被 `display:none !important` 隐藏，手机上只能停在仪表盘）
- **退出登录**：侧栏新增「退出」按钮
- **身份解绑**：新增 `POST /api/identity/unbind` + `store.unlink_identity`，同时清 `archive_identities` 与 `platform_bindings`；身份映射页把下拉改回「不配置」即解绑
- **好感度当前值可编辑**：档案详情新增输入框，接回 `PATCH /api/affection/<id>`
- **输出清洗**：口语正则规则加代码块/行内代码/Markdown 链接防护；完整结果回放的流式路径也清洗
- **设置页端口**：按当前 `location` 显示，不再硬编码 18890
- **工具页接回通用插件扫描**：`tool_shelves/plugin_scan.py` 不再绑定 AstrBot —— AstrBot（`@filter.llm_tool`）、NoneBot、LangBot / Dify 的 manifest、通用 Python 装饰器（`@tool` / `@function_tool` / `@register_tool` / `@mcp_tool` / `@ai_function`）都能扫；扫描目录在「设置 → 常规与系统 → 插件工具扫描」里配置（每行一个，存 `data/plugin_scan.json`，可勾选自动识别），只读、不写插件目录
- **记忆库快照与恢复**：设置 → 记忆库 → 快照与恢复，列出整库副本（迁移前 / 导入前 / 恢复前 / 手动）、大小、时间、schema 版本与总占用；支持立即备份、恢复（先自动生成 `pre_restore_*`）、手动删除；**不做自动清理**，快照只增不减

### 清理

- 删除 `/api/sniff_key`（路由 + `handle_sniff_key` / `sniff_provider_from_key`）、`/api/affection` 两条 GET、`/api/tools/shelves/status` 别名
- `module_manager.py` 510 → 26 行：连接中心下线后只剩 `get_module_permissions`；`/api/overview` 不再返回无人读的 `modules` / `modules_permissions`，也不再做 TCP 探针
- `console/backend.py` 去掉重复 import 块与 5 个死导出
- `models_config.json` 不再重复保存 192 条 `preset_templates`
- 前端：删除 28 个不可达函数、旧充值弹窗、旧工具权限面板、模块分布死面板、主页流水死渲染、隐藏桩写入、移动端旧导航；`models.js` 1081 → 538 行

### 性能

- `get_models_config` / `get_preset_models` 按 mtime 缓存，不再每 5 秒重读 174KB + 152KB JSON
- `/api/tokens` 走轻量投影（不查最近流水与账本页成本分组）
- 历史流水补算进程内限频 10 分钟，不再跟着 5 秒轮询做两次全表扫描

### 文档 / 部署

- `agent_core/memory/README.md` 按真实调用链重写（三维召回/向量/FTS/MemoryTools 均标注未接线）
- `MEMORY_LAYERS` 删除已下线的插件注册表 / 桥接器 / 执行 AI 章节，修正流式检索说明
- `MEMORY_MODULE` 修正「聚合器生成 memory_digests」；`PROJECT_ARCHIVE` 修正三维召回与性能监控描述
- `TOOL_SHELVES` / `CONNECT_ASTRBOT` 标注执行器插件为可选自备件
- `docker-compose.yml` 删除代码不读的三个环境变量；`deploy_check` Key 提示与现行安全默认一致；`pyproject.toml` 补 `pyyaml`

## 2026-11-01

> 本轮按第三方验收审计修复了 3 项 P0、11 项 P1 与关键 P2，并处理了两轮复核提出的残留。
> 审计与复核报告本身不入库（保留在本地工作区），这里只记录修复结果。

### 安全（第二轮复核修复）

- **默认部署不再是管理员**：`ClientProfile.default_actor` 默认 `guest`；没有 `clients.json` 或配置损坏时 `resolve()` 返回 `missing_key`，`require_key` 默认 `true`；管理类 `/hub/v1/*` 接口必须真带 Key；`connect_astrbot.py` 与 `clients.example.json` 改为 `require_key: true`
- **legacy 配置不再回退 0.0.0.0**：`api_security._merged()` 的 `bind_host` 回落跟随默认值 `127.0.0.1`，`save_config` 会把字段写全
- **算力配置脱敏**：`/api/compute/config`、`/api/overview` 只回 `api_key_masked` + `has_key`，保存时自动还原未改动的 Key
- **清洗接口强制 Key**：`/api/tools/clean` 无条件要求客户端 Key（会消耗模型额度）
- **流式检索限深真正生效**：到达上限后改用纯转发，不再重新进入指令分支
- **工具闸门 fail-closed**：身份级拒绝抛异常时摘掉全部工具，不再全量透传
- **知识库检索收口**：`/hub/v1/knowledge/search` 要求管理员 Key
- **DELETE 死路由**：`do_DELETE` 拦截已下线前缀，并删除对应死分支
- 新增 `.dockerignore`（构建上下文不再包含 `data/`、`backups/`、日志、密钥）；`/api/sniff_key` GET 下线

### 安全（第一轮）

- **静态目录路径穿越**：`/static/` 下发前做 realpath + commonpath 校验，只发白名单扩展名，并加 `X-Content-Type-Options: nosniff`
- **默认口令下线**：删除内置 `admin888`；首启生成随机口令（控制台打印 + `data/auth_initial_password.txt`），登录后强制改密（≥12 位）
- **口令哈希升级**：改为 PBKDF2-HMAC-SHA256（24 万轮，带算法前缀），旧 `sha256(salt:password)` 登录成功时自动升级
- **改密即下线**：改密吊销全部旧会话并清 Cookie；存量弱口令启动时自动轮换
- **适配器接口收口**：`/api/adapters/analyze|generate|attach` 改为需登录，`repo_path` 限定 `data/imports/`（可用 `HUB_ADAPTER_ROOTS` 追加），预览脱敏
- **`do_PATCH` 补登录校验**：与 `do_DELETE` 对齐，匿名 PATCH 一律 401
- **身份解析 fail-closed**：无 UID 不再回落主人（按客户端生成访客 actor），解析异常按访客处理并写审计
- **主人别名收敛**：默认只认 `MASTER_QQ` 与显式 `MASTER_ALIASES`，删除 `admin`/`leque` 通用词旁路
- **档案越权**：`/hub/v1/archives/resolve` 对非管理员 Key 只回最小字段
- **XSS / 会话**：登录响应不再回传 Token，前端改用 HttpOnly Cookie；工具行、算力卡片、首页流水三处注入点改为转义 + 事件委托
- **API Key 脱敏**：`/api/models_config`、`/api/compute/config` 只回掩码与 `has_key`，保存时自动还原未改动的 Key
- **宿主机控制**：`powershell` 动作默认关闭，需 `HUB_HOST_POWERSHELL_ENABLED=1` 且只放行只读白名单命令
- **请求面加固**：请求体上限 8MB（超限 413）、handler 超时 60s、并发上限 64
- **SSE CORS**：不再固定 `*`，仅同源返回
- **默认监听**：改为 `127.0.0.1`（Docker 用 `HUB_BIND=0.0.0.0`）
- **审计覆盖**：清账本、绑身份、工具策略、访客策略、改密均写 `audit_log`

### 记忆与账本（P1 功能逻辑）

- **事实时效**：写入即维护 `active` / `pending` / `superseded`；低置信度（<0.6）进 pending，同谓词轴新事实自动覆盖旧事实
- **图关系可用**：`memory_graph.db` 改为从 `user_facts` 建图，不再永远为空
- **账本记实际模型**：流式累加器接收上游 `model`；工兵与抽事实按真正跑通的凭证模型记账
- **未定价不套价**：查不到单价的模型成本记 0，不再套用主控单价
- **兜底话术不进记忆**：上游失败时不写时间轴、不抽事实、不评好感度
- **隐私开关贯通**：预取与工具事件同样遵守 `allow_recall_memory` / `allow_ingest_timeline`
- **预算守恒**：检索占用 `reserved` 真正透传给时间轴预算；下限跟随面板最小值 500
- **检索写回可召回**：写回事件保留说话人 `actor_id`，主人能按 actor 过滤召回
- **导入可召回**：导入记忆默认落到主人档案，支持 `person_id`；ChatGPT 顶层数组能被识别
- **聚合与缓冲**：摘要写入 `person_id`；缓冲区加唯一索引并 `INSERT OR IGNORE`
- **流式检索限深**：`[[记忆检索]]` 最多重开 2 轮

### 工程

- Docker 改用 `requirements.txt`（原来装的是空的 `requirements-agent.txt`）；`requirements-agent.txt` 改为 `-r requirements.txt`；compose 端口统一 `18895:18890`
- `Ctrl+C` 正常退出；迁移 29（事实置信度/失效时间 + 缓冲区唯一索引）
- 前端清理死调用（`refreshAlerts`、`selectSidebarModule`、`/api/patrol_config`）
- 新增 `tests/test_audit_regressions.py`（路径穿越、适配器根目录、请求体上限、身份、事实时效、Key 脱敏等）
- 测试数 305 → **645**；文档同步更新（监听地址、预算默认值、测试数、安全清单、端口）

## 2026-10-31

### 新增

- **记忆调参面板**：并入「设置」子页；注入预算 / 候选池 / 聚合窗口 / 检索开关可调，带实时预算预览与三档预设
- **工具货架**：按需加载、四类目（media/search/server/platform）、单轮上限、管理员提权
- **档案级工具权限**：不同档案可用不同工具，陌生访客走 `_guest` 策略
- **档案系统**：好感度浮动窗口、自定义设定、未知好感度（`?`）、AI 自动评估
- **Token 账本**：新增 `tools` 字段，按「通道 + 工具」归因
- **性能监控**：进程树（中枢本体）+ Docker 容器 + 规则匹配三层统计
- **消息中心**：进程内 info/warn/error 队列，控制台顶部红点

### 变更

- 身份与密钥改为配置驱动：`data/identity.json` + 环境变量，源码只留占位符
- 运行端口统一为 `18895`（旧 18890/18891 由 nginx 兼容层转发）
- 运维事件（凭证轮换）不再写入时间轴，只进消息中心
- 工具来源全部指向 AstrBot（MCP / 插件 / 内置），中枢不提供工具

### 修复

- 启动脚本与默认端口不一致：脚本打印 18895 却启动在 18890（被 nginx 占用），导致后端起不来、18895 返回 502；默认端口统一为 18895
- 网关身份污染：说话人错误地使用 `clients.json` 的客户端显示名，导致记忆和提示词出现「AstrBot QQ 通道（唯一主管理员客户端）」；改为档案昵称 / 主人名
- 长期事实 / 实体关系面板读错表：写入已统一到 `user_facts`，面板却仍读空的 `memory_facts`，导致一直显示「还没有」；现在统一读 `user_facts`
- 实体关系不再整句照搬事实：从「喜欢… / 是…的主人 / 使用… / 答应给…」等句式抽「谓词 + 客体」，和事实列表区分开
- 时间轴页面混入纯模块聚合摘要（如「master；模块：llm.persona、qq（共 4 个片段）」）；这类行不再展示，带「要点」的摘要和真实对话仍保留
- 时间轴命名与标签：页面统一叫「时间轴」；主标签取平台上下文的宿主插件（如 `napcat`），副标签统一为 `平台:UID`（如 `qq:<主人UID>`），不再写死 QQ/B站；群名保留在正文前缀
- 群内工具调用单独成事件：标签为「真实工具插件名 + 平台:人的UID」；`call_raw_tool` 会从参数里取内层工具名，不再把宿主插件当工具名
- 人员 UID 兜底：请求头没带 UID 时按档案主身份回查（如 `arc_master` → 主人 UID），避免标签退化成 `qq:master`
- 支持插件调用链：`X-Plugin-Chain` / `X-Caller-Plugin` + `X-Host-Id`，时间轴主标签显示 `A → B`；不传则退回单插件
- 主 AI 动态工具申请：被裁剪后可调 `hub_request_tools(intent)` 按意图加载已授权工具；中枢只加载、不执行
- 动态工具补齐：流式请求先完整生成再回放 SSE；每轮默认 2 次（`HUB_DYNAMIC_TOOL_ROUNDS` 最高 4）；申请写 `dynamic_tool_request` 审计和 `dynamic` 指标
- 宿主机控制：AstrBot 主入口 `control_host_pc_via_antigravity`（Host Bridge）+ 中枢备用 `hub_host_control`（本地 PowerShell/截图/锁屏），管理员限定并写 `host_control` 审计
- 宿主机桥更名：`antigravity_bridge` → `host_bridge`；配置 `host_bridge_config.json`；Antigravity Agent 仅保留为可选自然语言模式
- 宿主机控制安全边界改为管理员身份：发送者 UID 必须映射到 `archives.role=admin` 的档案；只有客户端 role=admin 但人不是管理员时，主/备入口都不注入
- 工具页新增「系统工具」：两套宿主机控制统一注册、展示和启停；状态落 `data/system_tools.json`，不绑定 AstrBot 页面逻辑
- 工具页只保留全局视图：系统工具置顶，其余按插件折叠；个人工具权限移到「档案 → 工具权限」
- 时间轴标签修正：不再把 web 会话 UUID 当插件名，web 请求主标签回落到 `web`
- 主控/功能凭证支持分时价格：凭证编辑器可加多个峰谷时段，计费和旧流水补算按请求时间匹配；未配置时仍用基础单价
- 新增插件接入契约：中枢适配插件，不要求插件改协议；支持通用头、声明式字段映射、执行 AI 生成适配器（生成物需测试和人工启用）
- 适配器注册表落地：`console/adapters/base.py` + `registry.py`，AstrBot 适配器已迁入；`generated/*.py` 可自动加载并参与归一化
- 插件适配器生成器：`scripts/generate_plugin_adapter.py` 用执行 AI 生成 adapter + test，静态校验；生成物 `ENABLED=False`，人工 review 后启用
- 声明式适配器：`console/adapters/declarative/*.json` 支持字段映射、path/header/UA 匹配，`enabled=true` 才加载
- 记忆库维护：设置页显示数据库占用（含 WAL），支持原生 JSON 导出、一键导入；兼容 JSONL / ChatGPT 导出 / 纯文本，导入前自动备份
- Token 预警：账单页显示余额/今日 token/动态上限；余额阈值按币种放在充值弹窗（主/副余额），动态上限按最近 N 天用量推算，每天只提醒一次
- 新增系统工具 `hub_ledger_query`：管理员 AI 可只读查询账本汇总、最近流水和按天消耗；中枢本地执行，审计 `ledger_query`
- 文档更新：新增记忆模块架构、功能挂载架构；架构总览补齐请求生命周期与 2026-11-01 变更清单
- 网关工具闸门：`archive_id` 引用了未定义变量，异常被 fail-open 吞掉，档案级策略静默失效
- 账本「来源」列：明细查询漏了 `session_type` / `tools`，页面只显示通道、看不到工具
- 记忆库 `llm_request` 现在留存**实际发给模型的 system prompt**（含人设、好感度、召回原文）
- 时间轴 `person_id` 归一：`master` / `qq:xxx` / `user_xxx` 全部收敛到 `archive_id`
- B 站动态与直播接口：修正 Referer，图片动态补正文兜底
- 工具设置接口忘记 `_send_json` 导致写入成功但无响应

### 安全

- 源码 / 文档 / 测试里的真实账号、群号、B 站 UID、API Key 全部替换为占位符
- 启动脚本去掉角色名和个人机器路径
- 测试统一使用假身份（`tests/__init__.py`）

## 2026-10-27

- 剥离第三方框架，中枢只保留人格、档案、记忆、路由、账本
- 定时任务、平台接入、工具执行全部移到 AstrBot

## 2026-10-26

- 初始版本：人格中枢 + 时间轴记忆 + OpenAI 兼容网关
