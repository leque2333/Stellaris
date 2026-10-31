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

## 4. 召回窗口与聚合分段（2026-10-31 调整）

### 4.1 时间轴怎么写

两条写入路径并存：

```text
L1 invocations / plugin_activities
      │ 聚合器：同一通道 + 同一会话对象，静默 10 分钟切段
      ▼
L2 agent_timeline   一条摘要：`master；模块：llm.persona、qq（共 6 个片段）`

对话本身（另一条）
每次成功回复 → 一条对话行：`某说：「…」；安桉回答：「…」` + detail_id
      ▼
L3 memory_details   原文（941 条）
```

对话行默认**不折叠**（`session_window_seconds=0`）；只有聚合器会把一串模型调用压成一条。

### 4.2 召回不再按条数

以前候选是「最新 200 条」，实测只能覆盖 14.9 小时，而且大半是测试流量。
现在改成**时间窗 + 来源过滤 + 高上限**：

| 参数 | 默认 | 环境变量 |
|---|---|---|
| **候选池字数预算** | 300,000 字 | `HUB_RECALL_CHARS` |
| 回看天数（可选，0=关） | 0 | `HUB_RECALL_DAYS` |
| 候选条数上限（安全阀） | 3000 | `HUB_RECALL_CANDIDATES` |
| 排除来源 | verify/acceptance/sec-live/archcheck/keycheck/sig-probe/kb_test | `HUB_RECALL_EXCLUDE` |
| 近因半衰期 | 168 小时（7 天） | `HUB_RECALL_HALF_LIFE_HOURS` |

按字数而不是按天数：安静期能保住更多历史，话多的时候也不会把候选池撑爆。
从新到旧累计 `len(narrative)`，到预算就停（至少保留一条）。

半衰期原来写死 36 小时，等于「两天前的记忆基本放弃」；拉到 7 天后，
旧记忆靠语义相似度仍然能召回到，不至于被时间项压死。

实测（测试库 598 条事件）：

```text
旧口径  最新 200 条      覆盖 14.9 小时
新口径  时间窗 + 过滤     395 条，覆盖 240 小时（10 天）
```

### 4.3 聚合按「群聊 / 私聊」分段

分段键 `(channel, context_kind, context_id)`：

```text
群聊    (qq, group,   群号)
私聊    (qq, private, 会话 id)
兜底    (qq, channel, 人物 uid)
```

身份画像仍归到同一个 `person_id`，只是**摘要按会话切**。
前提是请求里带上下文头：`X-Group-Id` / `X-Session-Id` / `X-Channel-Type`。

**AstrBot 自带会话头，不用插件。** 它的 OpenAI provider 会给每个请求加
`x-astrbot-conversation-id`（`build_conversation_headers`），hub 直接读：

```text
aiocqhttp:GroupMessage:784826874   → (group,   784826874)
aiocqhttp:FriendMessage:2986371638 → (private, 2986371638)
<uuid>                             → (conversation, <uuid>)   按会话分开，但不知道群号
```

也支持显式覆盖：`X-Group-Id` / `X-Session-Id` / `X-Channel-Type`。

实测（测试端）：

```text
带 GroupMessage 头 → timeline_segments: (qq, group, 784826874)
带 FriendMessage 头 → timeline_segments: (qq, private, 2986371638)
```

### 4.4 上下文占用

召回注入是硬上限：8 条 + 前 2 条原话（各 ≤280 字），折算 **700~900 tokens**；
QQ 通道实测平均 8,874 tokens，记忆占 6~10%。数据库增长不影响这个数——
它由注入上限决定，不由时间轴条数决定。

## 5. 未来方向：时间轴即工作记忆（假设，未实现）

> 2026-10-31 讨论稿。当前实现仍是「无限增长的时间轴 + 召回时切 8 条」；
> 这一节描述的是目标形态，动手前要先定预算。

### 5.1 目标模型

```text
热层  时间轴     固定字数预算，整段注入 prompt，不再切片
温层  记忆库摘要  每条对话一段压缩摘要 + 向量，按需检索
冷层  记忆库原文  完整 prompt / 回复 / 工具参数，只在明确需要时取
```

- **时间轴是给 AI 的工作记忆**：里面的每一条都应当对理解当下有用
- **超出预算的旧条目降级**：写入记忆库摘要后从时间轴移除，而不是在时间轴上切一刀
- **聚合摘要（模块/调用记录）不进时间轴**：那是运维记录，放记忆库或审计日志
- **访问记忆库 = 换页**：取回旧记忆时临时把热层的一部分换出，保证总上下文不超预算

### 5.2 代价（必须先算清）

当前实测（测试库 611 条 / 51,434 字）：

| 方案 | 每次请求注入 | 相对现在 |
|---|---|---|
| 现状：召回 8 条 | 约 700~900 字 | 1× |
| 整条时间轴（5 万字体量） | 约 51,000 字 ≈ 77,000 tokens | 约 80× |
| 预算 15,000 字 | ≈ 22,000 tokens | 约 25× |
| 预算 5,000 字 | ≈ 7,500 tokens | 约 9× |
| 预算 2,000 字 | ≈ 3,000 tokens | 约 4× |

QQ 通道现在平均 8,874 tokens/次；把整条时间轴塞进去，每句话的成本会翻一个量级。
所以「整段给 AI」必须配一个**很小的预算**，否则账单先炸。

### 5.2.5 记忆曲线（已实现引擎，未接入 prompt）

按**字数份额**分三段，越靠近现在越完整：

| 位置 | 占预算字数 | 压缩比 | 处理 |
|---|---|---|---|
| 前段 | 30% | 1.00 | 原样保留 |
| 中段 | 40% | 0.85 | 轻度压缩 |
| 后段 | 30% | 0.60 | 深度压缩 |

规则：

- **按字数不按条数**：一条长记忆不该顶掉十条短的
- **短句不压**（< 100 字直接保留）：把 72 字的对话压到 61 字只会压出歧义
- **只在句读处下刀**（。！？；换行），落在引号里就补到收尾引号，超远就退到引号前
- 压缩后不会比原文更长；被预算挤出的事件不进视图，完整数据本来就在记忆库

预览接口（要登录）：

```powershell
curl.exe "http://127.0.0.1:18895/api/memory/timeline_curve?budget=20000" -H "X-Session-Token: <token>"
```

实现：`agent_core/memory/timeline_curve.py`（`build_curve` / `compress_narrative` / `summarize`）。

**已接入 prompt（2026-10-31）**：`_collect_recall_memories` 改为整段曲线注入，
不再切 8 条；预算 `HUB_TIMELINE_BUDGET` 默认 8,000 字（含 `[时间] ` 前缀开销）。

实测（测试端，8,000 预算）：

```text
时间轴块  8,027 字 / 88 条 / 覆盖约 16 小时
prompt    3.7k（基础） → 8.7k tokens
```

召回打分那段（`_format_recall_lines` / `_recall_exact_hits`）随之删除，
需要更早的记忆时再上「换页」——那是下一步的混合模式。

### 5.3 降级机制（草案）

```text
写入：新事件进时间轴，累计字数 +N
超限：从最旧开始，把事件压缩成一句摘要写进 memory_details(kind='timeline_digest')
      → 删除时间轴行（或标记 archived=1，不可见）
摘要：聚合器只在「有要点」时写摘要行；纯模块调用记录直接写审计日志，不进时间轴
读取：recall = 时间轴全量（预算内），需要更早的记忆时按需换页
```

### 5.4 连续高压文本（游戏 / 工具场景）

- 按事件来源分级：工具日志、巡检这类**低语义高频率**内容只留摘要 + 计数，不进热层
- 单条超长（例如整段游戏文本）先切块，摘要进热层、原文进冷层
- 需要时用一个 `memory_budget` 全局预算，热层/温层/冷层按比例分配

### 5.5 落地顺序建议

1. 定预算（2k / 5k / 15k 字），先只做「超限降级」，召回逻辑不动
2. 观察一周：AI 是否还记得住最近的事、成本是否可接受
3. 再把召回从「切 8 条」改成「整段注入」，同时上线换页
4. 最后接高压文本的分级与切块

## 6. 深时记忆检索（设计稿，未实现）

> 触发场景：「我前天在 QQ 上给你发小鱼干了」——时间轴只覆盖最近十几小时，
> 这句话要靠记忆库翻旧账，但不能把一天的记录整段灌进上下文。

### 6.1 链路

```text
用户带时间锚点说话
   ↓
执行 AI（工具挂载层）解析：时间范围 + 关键词（前天 / QQ / 小鱼干）
   ↓
主 AI 发起工具调用 → 中枢记忆检索接口（hub 直连）
   ↓
脚本按字数做闸门：小的直通、大的先压缩
   ↓
主 AI 临时腾出时间轴预算，插入这批旧记忆
   ↓
本轮结束：旧记忆压缩一次写回时间轴，腾出的近期片段拼回
```

### 6.2 脚本闸门（第一步）

| 检索结果大小 | 处理 |
|---|---|
| ≤ 2,000 字 | 原样返回给主 AI |
| 2,000~8,000 字 | 只给时间轴里的精简叙述（不带原文） |
| > 8,000 字 | 交给执行 AI 压缩成要点摘要，原文留在库里 |

原则：**主 AI 永远拿不到超过预算的整段旧账**；原文只在必要时按条取。

### 6.3 临时换页（第二步）

```text
进入深时检索：
  budget_extra = 旧记忆占用（例如 3,000 字）
  从时间轴尾部腾出同样字数（最近的那一段，压缩后暂存）
  → 时间轴 = 近期(压缩态) + 旧记忆(本次)

本轮结束：
  旧记忆压缩一次（即使它此刻离现在最近），写回时间轴尾部
  腾出的近期片段解压拼回原位
  总字数回到预算内
```

关键点：

- **最近的记忆也必须压一次**：它已经从「当前」变成了「历史」，不能保持全量
- 拼回的是**腾出时那段转成压缩态的近期记忆**，不是原样复制
- 整个过程对主 AI 透明，它只看到「这一轮上下文里有旧事」

实现进度：

- ✅ 预算守恒（预取路径）：`_timeline_budget_with_reserved`，检索占多少字，
  时间轴就让出多少（下限 2000 字）；`HUB_TIMELINE_BUDGET` 是总预算
- ⬜ 指令式路径（`[[记忆检索]]` 重答）还没让出预算，需要重拼一次 sys_prompt
- ✅ 检索结果写回时间轴：`_recall_writeback`
  - 写到**最靠前**的位置（`timestamp=now`），source=`memory_recall`
  - **写入时就压缩一次**（0.6）；普通记忆只在掉进后 30% 时才压，两者不同
  - 只取前 3 条拼成一句「回忆：…」，10 分钟内重复检索不重复写入
- ⬜ 与档案事实联动（命中旧事时一并带出相关事实）

### 6.4 需要新增的东西

| 组件 | 状态 |
|---|---|
| `POST /hub/v1/memory/search` | ✅ 已实现（2026-10-31） |
| 提示词内挂载（中枢自己执行） | ✅ 已实现（2026-10-31） |
| 换页控制器 | 待做 |

### 6.4.1 中枢自带的「特殊接口」

不需要 AstrBot 参与：中枢在拼 system prompt 时追加一段说明，

```text
【记忆检索】需要更早的记忆时，在回复开头单独写一行：
[[记忆检索: 关键词 | 起始日期 可选 | 结束日期 可选 | 工具名 可选]]
写完继续正常回答；中枢会查库、把结果补给你、再让你重答一次。
```

模型输出里出现这行时，中枢自己执行检索（`store.search_narratives`：
来源 + 时间范围 + 关键词，默认全词命中、无结果降级任一命中），把结果作为
一条 user 消息追加后重答；指令行在返回给用户前剥掉。开关 `HUB_MEMORY_TOOL=0`。

- **流式也能检索（预取）**：用户话里带时间锚点（前天/昨天/上次/记得/说过…）时，
  中枢在调模型**之前**就先查一次记忆库，把结果追加进 system prompt。
  这是纯前置动作，流式/非流式都生效，不依赖模型会不会写指令。
- 指令式（`[[记忆检索: ...]]`）只在非流式生效，作为精确控制的补充；
  流式下不挂这段提示，避免模型写了没人执行。
- 检索结果按字数闸门裁剪，默认取一半预算（4,000 字）

接口用法：

```powershell
curl.exe -X POST http://127.0.0.1:18895/hub/v1/memory/search `
  -H "Content-Type: application/json" `
  -H "Authorization: Bearer <客户端 Key>" `
  -d '{"query":"小鱼干 科技新闻","start":"2026-10-29","end":"2026-10-31 23:59","budget":8000}'
```

```json
{"ok": true, "mode": "compressed", "hits": 112, "chars": 7974,
 "original_chars": 19704, "needs_ai_summary": true, "records": [...]}
```

- 关键词默认 **AND**（全词命中）；没结果才自动降级成 OR
- `start` / `end` 支持 `YYYY-MM-DD`、`YYYY-MM-DD HH:MM`、unix 秒
- `budget` 默认 8000，最大 50000；`person_id` 可选
- 只返回时间轴叙述，原文不进这次响应（避免一次灌满上下文）

### 6.5 与现有实现的衔接

- 时间轴整段注入（第 5 节）已经在跑，预算 `HUB_TIMELINE_BUDGET`
- 换页控制器就是给这个预算做「借贷 + 归还」
- 检索接口复用 `memory_details`（原文）与 `agent_timeline`（精简）
