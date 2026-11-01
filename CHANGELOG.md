# 更新记录

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
- 时间轴命名与标签：页面统一叫「时间轴」；主标签取平台上下文的宿主插件（如 `napcat`），副标签统一为 `平台:UID`（如 `qq:2986371638`），不再写死 QQ/B站；群名保留在正文前缀
- 群内工具调用单独成事件：标签为「真实工具插件名 + 平台:人的UID」；`call_raw_tool` 会从参数里取内层工具名，不再把宿主插件当工具名
- 人员 UID 兜底：请求头没带 UID 时按档案主身份回查（如 `arc_master` → `2986371638`），避免标签退化成 `qq:master`
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
