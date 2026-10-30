# 记忆系统三层模型（设计稿）

> 更新：2026-10-28
> 状态：v4 底座 + v7 聚合器已落库；网关记录 L1/L3；L1→L2 聚合器已实现（模板摘要）。
> 边界：记忆库只放 L3；事实/档案/好感/日记归独立的「画像」模块（`agent_core.profile`）。
> 目标：把「记忆」从“一坨事件”拆成三层不同粒度的数据，各自负责不同问题。

---

## 1. 三层是什么

| 层 | 名字 | 回答的问题 | 一行/一条是什么 | 数据量 |
|---|---|---|---|---|
| L1 | 调用索引（Timeline Index） | 她**用了哪个工具、哪个模块**？什么时候？成功没有？ | 一次调用：`qq / message.received`、`bilibili / comment.create`、`host / screenshot` | 很大，但很短 |
| L2 | 事件摘要（Timeline Event） | 中枢用这个模块**大概发生了什么**？ | 一段连续活动：`在『摸鱼群』聊了 40 分钟，聊到 IDE 额度和 CLI 报错` | 中等 |
| L3 | 记忆详情（Memory Details） | **具体发生了什么**？原话、参数、结果、附件是什么？ | 一次会话的完整消息、工具入参/出参、截图路径 | 很大，可长期归档 |

一句话：**L1 是目录，L2 是摘要，L3 是原文。**

### 1.1 人物档案与画像

**档案（Archive）= 一个人，不是 AI**，放在 `agent_core.profile.archives`：

| 规则 | 说明 |
|---|---|
| 管理员档案 | **必须存在**；`archive_id` 默认用主人 QQ，兼容旧画像键 |
| 用户档案 | **可选，必须手动创建**；不会因为陌生人说话就自动建档 |
| 平台身份 | QQ / B站 / 微信 / CLI 等账号挂在同一个人物档案下（`archive_identities`） |
| AI 人格 | **不在档案里**；统一由「人格设定」的全局人设卡 `data/persona.md` 决定 |
| 平台差异 | QQ / B站等账号与行为配置放在各自模块页，**不塞进档案** |
| 对外接口 | `/hub/v1/archives`、`/api/archives` 提供创建、列表、解析、改称呼/状态 |

**画像（Profile）绑定人物档案**，事实/好感/日记都按 `archive_id` 存：

| 内容 | 表 | 说明 |
|---|---|---|
| 事实 | `user_facts` | 一条条结论，可覆盖（superseded） |
| 画像 | `user_profiles` | 一个人的封面：昵称、身份、好感度、变化历史 |
| 日记 | `memory_digests` | 一段时间的压缩总结 |

关系：**时间轴/记忆库存“发生过什么”，人物档案回答“这个人是谁”，画像存“她据此知道了什么”，人格设定回答“她是谁”。**
`ProfileManager` 负责把 actor 解析成人物档案，再读写画像；记忆库只负责“当时具体说了什么”。

### 1.2 平台自动发现（只读嗅探）

配置留在 QQ 等宿主模块，中枢只读扫描，不复制配置：

| 来源 | 读取内容 | 发现结果 |
|---|---|---|
| `astrbot/data/cmd_config.json` | 管理员、白名单、provider 源 | 管理员 QQ、允许的群/好友 |
| `astrbot/data/data_v4.db` | `umo_aliases` / `conversations` | UMO、群名、活跃私聊 |

外部键：

- QQ：`onebot_napcat:GroupMessage:<群号>` / `onebot_napcat:FriendMessage:<QQ>`

发现结果写入 `platform_contexts`；用户只决定“这个上下文属于哪个人物档案”，写在 `platform_bindings`。
双填嗅探：名称和 UID 各一格，多个用逗号分隔；同一条记录同时命中名称和 UID 判 `paired`，只命中一边判 `name` / `uid`，绑定记录 `confidence` 和 `verified_by`。
执行 AI：双填框旁“AI 定位”会用只读工具（搜文本/读文件/看 SQLite/执行 SELECT）在 `HUB_SNIFF_ROOTS` 允许目录里找证据，返回 JSON 候选；工具结果脱敏，候选置信度封顶 0.85，仍需人确认绑定。
运行时插件带 `X-Platform` + `X-External-Id`，中枢查绑定找人物档案。
配置若被删，下一轮扫描把上下文标记为 `stale`，不会静默漂移。

### 1.3 执行 AI（一个内核，两种 profile）

| profile | 权限 | 用途 |
|---|---|---|
| `investigate` | 只读：搜文本、读文件、SQLite schema/select | 平台匹配、排查 |
| `execute` | 工作区读写 + 白名单命令（python/pytest/node/npm/git） | 执行主模型下发的任务 |

主模型通过 `delegate_execution(goal, mode)` 调用；执行 AI 不直接面向用户。
每次执行写一条 L1 `invocations`（module=`execution_ai`），记录目标、mode、工具调用和结果摘要。
写文件限制在工作区内，`data/memory.db`、密钥配置、`.git`、`backups` 受保护；命令禁止 shell 操作符。
整机 shell / 网络留给独立插件，不放进这个执行层。

### 1.4 插件是数据，不是代码

`plugins` 表只存：`plugin_id / name / root_path / protocol / spec / status`。
协议探针只识别通用协议（`sqlite` / `json` / `log`），不认识“AstrBot”或“B站插件”。
新增插件 = 扫描目录生成记录，不新增 Python 分支；控制台“插件注册表”从 `/api/plugins` 动态渲染。
AstrBot / B站 的上下文读取也已迁成 `data/plugin_specs.json` 里的声明式 spec，代码里不再有这两个连接器类。

### 1.5 通用桥接层

`BridgeSpec` 只描述 source，不写插件名：

```json
{"source_id":"demo_sqlite","kind":"sqlite","scope":"plugin","locator":"messages.db",
 "options":{"table":"messages","cursor":"id","content":"content"}}
```

`BridgeEngine` 按 `kind` 选驱动（`sqlite` / `json` / `log` / `http`），把源事件统一成 `HubEvent`；
`BridgeIngestor` 用 `event_key` 去重后写时间轴，`bridge_cursors` 保存增量位置。
`BridgeSpecInferer` 可先探测目标（文件或 URL）、再让执行 AI 生成 spec，并用真实样本试读；AI 不可用时退回启发式。`/api/bridges/attach` 支持 `start:true`，推断通过后直接启动。
`bridge_specs` 保存已启动的 spec（auto_start），重启后自动恢复；HTTP 驱动支持重试、页码/游标分页与批量导入。
源码分析：`SourceAnalyzer` 扫仓库提取语言/框架/配置/DB/路由/日志，`SourceSynthesizer` 对发现的 DB/事件 JSON 生成候选 spec 并试读；不按插件名写死。
OneBot 作为协议驱动后置，不改变这层抽象。

---

## 2. 三个真实例子

### QQ 群聊

- **L1**
  - `channel=qq`、`module=astrbot`、`event=message.received`、18:02:03
  - `module=llm.persona`、18:02:04、ok、1280 tokens
  - `module=memory.recall`、18:02:04、ok、命中 2 条
  - `module=memory.ingest`、18:02:09、ok
- **L2**
  - `QQ / 摸鱼群`：18:02–18:47，14 轮对话，聊了 IDE 额度和 CLI 报错
- **L3**
  - 14 条原始消息全文、2 次记忆召回的命中内容、模型请求/响应、工具参数与结果

### B站巡检

- **L1**
  - `plugin=bilibili`、`tool=bili_get_video_detail`、ok
  - `plugin=bilibili`、`tool=comment.create`、ok
- **L2**
  - `B站 / 东方某人丶`：看了《…》，留了一条评论
- **L3**
  - 视频元数据（BV 号、标题、UP 主）、评论文本、封面/截图路径

### 宿主机控制

- **L1**
  - `plugin=host_control`、`tool=pc.screenshot`、ok、耗时 120ms
- **L2**
  - `宿主机 / 截图`：帮主人截了一张屏
- **L3**
  - 截图文件路径、尺寸、创建时间

---

## 3. 和现有代码的差距

| 层 | 现在有什么 | 差距 |
|---|---|---|
| L1 | `invocations` 表；网关已记录 channel / model / tool 三类调用，带 `trace_id` | 插件内部工具、CLI 的调用还没接入；尚无 L1→L2 聚合 |
| L2 | `agent_timeline`（120 条，带标签） | 现在是“一条消息一条”，不是“一段活动一条”；摘要质量不够；缺 `trace_id` 串联 |
| L3 | `memory_details` 表；网关已存请求、响应、工具参数 | 插件侧未覆盖；无 TTL/清理任务；大 payload 未限长 |

粗略完成度：**L1 ≈ 70%，L2 ≈ 75%，L3 ≈ 50%**。

---

## 4. 目标表结构（v4 草案）

### L1：`invocations`

```sql
CREATE TABLE invocations (
  invocation_id TEXT PRIMARY KEY,
  trace_id TEXT NOT NULL,          -- 一次请求/一段会话的串联 id
  parent_id TEXT,                  -- 嵌套调用（工具里再调模型）
  layer TEXT NOT NULL,             -- channel / plugin / tool / model / internal
  module_id TEXT NOT NULL,         -- qq / bilibili / host_control / memory.recall
  tool_name TEXT,                  -- 具体工具名，可为空
  channel_id TEXT,
  actor_id TEXT,
  started_at REAL NOT NULL,
  ended_at REAL,
  status TEXT DEFAULT 'ok',        -- ok / error / timeout
  summary TEXT,                    -- 一句话，供 L2 聚合
  detail_id TEXT,                  -- 指向 L3
  event_id TEXT,                   -- 结算后指向 L2
  metadata TEXT DEFAULT '{}'
);
CREATE INDEX idx_inv_trace ON invocations(trace_id);
CREATE INDEX idx_inv_time ON invocations(started_at);
CREATE INDEX idx_inv_module ON invocations(module_id, started_at);
```

### L3：`memory_details`

```sql
CREATE TABLE memory_details (
  detail_id TEXT PRIMARY KEY,
  invocation_id TEXT,
  event_id TEXT,
  kind TEXT NOT NULL,              -- message / tool_args / tool_result / llm_request / llm_response / attachment
  mime TEXT DEFAULT 'application/json',
  content TEXT,                    -- 原文或 JSON
  size_bytes INTEGER DEFAULT 0,
  created_at REAL NOT NULL,
  expires_at REAL,                 -- 可选 TTL
  metadata TEXT DEFAULT '{}'
);
CREATE INDEX idx_detail_event ON memory_details(event_id);
CREATE INDEX idx_detail_inv ON memory_details(invocation_id);
```

### L2：继续用 `agent_timeline`

- `agent_timeline` 不再承载“原始消息”，只承载摘要；
- `metadata` 里存 `trace_id`、`invocation_ids`、`detail_ids`；
- 现有 120 条视为历史 L2，`trace_id` 为空即可，不重写。

---

## 5. 写入与读取流程

```
通道请求
  → L1: channel 收到消息
  → L3: 存请求原文
  → L1: 模型调用；L3: 存请求/响应
  → L1: 工具调用；L3: 存入参/出参
  → 会话结束 / 静默超时
      → 聚合 L1 → 一条 L2 事件（摘要 + 标签）
      → L2.metadata 记录 invocation_ids
读取
  → 日常对话：只读 L2（省 token）
  → 用户问“具体怎么说的”：模型调 read_memory_detail(event_id) 读 L3
```

关键原则：

- **L1 只存索引**，不存正文，可长期保留；
- **L2 是回忆入口**，提示词只注入 L2；
- **L3 默认不注入**，按需读取，避免上下文爆炸；
- L3 可设 TTL / 大小上限 / 敏感字段脱敏。

---

## 6. 已定与待确认

**已定**

- 记忆库只装 L3 详情；事实/档案/好感/日记独立为「画像」模块（`hub.profile`）。
- 画像后续作为身份映射的落点。
- L1→L2 聚合器已实现：同一通道/对象 + 静默窗口 → 一条摘要；L2 先用模板，不额外调模型。

**待确认**

1. **L1 粒度**：每次工具调用一条（默认），还是每次“模块被使用”一条？内部模型调用算不算 L1？
2. **L3 保留策略**：永久 / 90 天 / 按大小滚动？私聊全文是否加密、是否只留本地？

