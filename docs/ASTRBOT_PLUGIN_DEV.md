# AstrBot 插件接入开发指南

> 更新：2026-11-01
> 读者：写第三方 AstrBot 插件的人（不是改中枢的人）
> 前提：中枢已启动、AstrBot 已能正常聊天
> 一句话：**插件负责平台和工具，中枢负责人格、记忆、模型和账本。插件只把「谁在说话」和「他说了什么」发到 `/v1`。**

---

## 0. 先选路线

| 你的插件 | 走哪条路 | 要改什么 |
|---|---|---|
| 只是普通聊天，跟着 AstrBot 默认模型走 | AstrBot Provider | 零代码，见 [ASTRBOT_SETUP.md](ASTRBOT_SETUP.md) |
| 插件自己发请求、改写上下文、服务多用户 | 直连 `/v1` | 加 3~6 个请求头 |
| 还要查档案、翻深度记忆、读写知识库 | `/hub/v1` | 额外申请管理员 Key |
| 要把自己的工具挂给模型 | `/v1` + `tools` | 工具由插件自己执行；工具页自动可见，**不需要先登记** |

> AstrBot 默认 Provider 只做文本转发，不会带插件 `tools`；要让工具进入中枢，必须走
> 「直连 `/v1` + `tools`」这条。接上之后不需要在中枢登记工具，工具页刷新/扫描即可看到。

**不要做的事（先看这段能省很多返工）**

```text
✗ 不要在插件里再存一份人格 / 记忆 / 模型 Key
✗ 不要把群号、会话 ID、昵称当成用户 UID
✗ 不要给中枢发 system prompt 指望它生效（中枢会重建人格提示词）
✗ 不要把管理员 Key 发到前端 / 客户端 / 仓库里
✗ 不要在中枢里实现平台协议或消息发送
```

### 0.1 接入即用：最短路径

```text
1. 中枢启动，data/clients.json 加一个客户端（第 2 节）
2. 插件走 /v1/chat/completions，带 X-Platform / X-User-Id / X-Channel（第 3 节）
3. 插件把自己的工具放进请求的 tools 数组（第 5 节）
4. 工具页看到它：
   - AstrBot 与中枢同机、插件目录能被扫到 → 工具页「扫描新插件」立即出现
   - AstrBot 在远端、扫不到目录 → 插件发一次带 tools 的 /v1 请求，刷新工具页即可看到
5. 模型返回 tool_calls，插件执行自己的工具
```

**不需要先到工具页登记工具。** 未登记工具默认放行（`unknown_tool_policy=always`）；
登记只影响工具页归类和类目裁剪，不影响“模型能不能调用”。

---

## 1. 心智模型

```text
平台事件（QQ / B站 / 定时任务）
        │
        ▼
   AstrBot 插件
   ├─ 收消息、决定要不要回
   ├─ 调 /v1 拿回复
   ├─ 执行自己的工具
   └─ 把回复发回平台
        │
        ▼
   Stellaris 中枢 /v1
   ├─ 认人：platform + UID → 档案
   ├─ 人格 / 好感度 / 事实 / 图关系
   ├─ 时间轴召回 / 深度记忆
   ├─ 模型路由 / Token 账本
   └─ 返回 OpenAI 标准响应 + hub 身份元数据
```

边界只有一条：**中枢不执行插件工具，插件不维护第二套人格和记忆。**

---

## 2. 第一步：给插件签一个 Key

中枢默认**拒绝匿名调用**。`data/clients.json` 不存在时，`/v1` 和 `/hub/v1` 都会返回 `401 missing_key`。

### 2.1 写一个客户端

编辑 `data/clients.json`（没有就新建；模板见仓库根目录 `clients.example.json`）：

```json
{
  "require_key": true,
  "clients": [
    {
      "id": "my_plugin",
      "name": "我的 AstrBot 插件",
      "keys": ["hub_sk_my_plugin_REPLACE_WITH_RANDOM"],
      "channel": "my_plugin",
      "default_actor": "master",
      "allow_recall_memory": true,
      "allow_ingest_timeline": true,
      "rate_limit_per_minute": 120
    }
  ]
}
```

改完不用重启：注册表按文件修改时间热加载。Key 必须是随机值，不要用示例里的 `change_me`。

### 2.2 `default_actor` 是最大的坑

它的真实语义是「**这个客户端上报的 UID 可不可信**」，不是「所有请求都是谁」：

| 值 | 行为 | 适合 |
|---|---|---|
| `"master"` | 信任 `X-User-Id`，按 UID 正常解析成主人 / 成员 / 访客 | 正确上报 UID 的多用户插件 |
| `"guest"`（默认） | 不信任 UID：先按访客处理；开了临时访客才按 UID 生成 `guest_<平台>_<UID>`，仍然没有档案、不召回、不写长期事实 | 不需要档案的场景 |

所以：

```text
多用户插件、需要每人一个档案 → default_actor 写 "master"
不需要档案                   → 可以写 "guest"，但必须先在中枢打开「临时访客」，否则 403
```

> 命名是历史原因。`"master"` 不会让每个请求都变成主人：普通 UID 仍然解析成 `user_<uid>`，只有白名单里的身份才有档案。
> 代价是这个 Key 也能调管理接口（见第 6 节），所以它只能待在插件服务端。

### 2.3 其余字段

| 字段 | 作用 |
|---|---|
| `id` / `name` | 客户端标识，会出现在审计和账本来源里 |
| `keys` | 这个客户端的所有 Key，可轮换 |
| `channel` | 请求没带 `X-Channel` 时的兜底通道名 |
| `allow_recall_memory` | 是否允许召回长期记忆 |
| `allow_ingest_timeline` | 是否允许把对话写进时间轴 |
| `rate_limit_per_minute` | 每分钟请求上限，`0` 表示不限 |
| `tool_permissions` | 工具权限（`role` / `allow` / `deny` / `scope`），给工具闸门用；`scope=request` 表示本轮 tools 全量保留，`catalog`（默认）按类目裁 |

### 2.4 先验证 Key

```bash
curl http://127.0.0.1:18895/v1/models
```

`/v1/models` 不需要 Key，能返回模型列表就说明中枢活着。

```bash
curl http://127.0.0.1:18895/v1/chat/completions \
  -H "Authorization: Bearer hub_sk_my_plugin_xxx" \
  -H "Content-Type: application/json" \
  -H "X-Platform: qq" \
  -H "X-User-Id: 10001" \
  -H "X-Channel: qq_group:123456" \
  -H "X-Channel-Type: group" \
  -d '{"model":"任意","messages":[{"role":"user","content":"在吗"}]}'
```

返回 `200` 且有 `choices[0].message.content`，就说明 Key 和身份链路都通了。

---

## 3. 第二步：每个请求都要说清「谁在说话」

### 3.1 请求头契约

| 头 | 必填 | 说明 |
|---|---|---|
| `X-User-Id` | **是** | 发送者的**平台底层 UID**（QQ 号、B站 UID），不是群号、不是昵称 |
| `X-User-Name` | 否 | 展示名；中文必须 URL 编码（`urllib.parse.quote`） |
| `X-Platform` | 建议 | `qq` / `bilibili` / `telegram` / `discord`…；缺省会猜，猜错就串档案 |
| `X-Channel` | 建议 | 稳定的会话 ID，如 `qq_group:123456`；**只用于记忆隔离，不参与身份匹配** |
| `X-Channel-Type` | 否 | `group` / `private` |
| `X-Group-Id` | 群聊建议 | 群号 |
| `X-Group-Name` | 否 | 群名；中文要 URL 编码 |
| `X-Session-Id` | 否 | 宿主自己的会话 ID |
| `X-External-Id` | 否 | 平台账号 / 会话外部键，用于绑定匹配 |
| `X-Host-Id` | 建议 | 插件标识（如 `astrbot`、`bilibili`），决定时间轴主标签 |
| `X-Plugin-Chain` | 否 | 插件调用链，如 `astrbot,my_plugin`，时间轴会写成 `A → B` |
| `X-Caller-Plugin` | 否 | 单次调用方，配合 `X-Host-Id` 使用 |

鉴权用 `Authorization: Bearer <key>`，也兼容 `X-Hub-Key: <key>`。

### 3.2 身份优先级

```text
body.user  →  X-User-Id  →  X-Actor-Id  →  正文里的 "昵称(12345):"  →  Key 的 default_actor
```

只认底层 UID。**没有 UID 不会被当成主人**：中枢会生成 `guest_<客户端>_<hash>`，然后因为没有档案返回 `403 guest_disabled`（除非开了临时访客）。

### 3.3 白名单：不是有 UID 就有记忆

中枢是白名单制。`platform + UID` 必须落在某个档案的 `archive_identities` 里，才会：

```text
有档案级对话（否则 403，除非开了临时访客）
有长期事实 / 图关系 / 自定义好感度
进档案召回
```

给插件用户建档，任选一种：

```text
控制台 → 档案 → 选档案 → 添加身份
```

```bash
curl -X POST http://127.0.0.1:18895/hub/v1/archives/arc_master/identities \
  -H "Authorization: Bearer <管理员 Key>" \
  -H "Content-Type: application/json" \
  -d '{"platform":"bilibili","user_id":"123456","display_name":"B站昵称"}'
```

如果希望陌生人也能聊，在控制台「设置 → 常规与系统 → 访问策略」打开**临时访客**；但临时访客没有档案、不写长期事实、好感度固定 50。

### 3.4 四种场景的请求头

QQ 群聊：

```http
X-Platform: qq
X-User-Id: 10001
X-User-Name: %E5%B0%8F%E9%B1%BC
X-Channel: qq_group:123456
X-Channel-Type: group
X-Group-Id: 123456
X-Host-Id: astrbot
X-Plugin-Chain: astrbot,my_plugin
```

QQ 私聊：

```http
X-Platform: qq
X-User-Id: 10001
X-Channel: qq_private:10001
X-Channel-Type: private
X-Host-Id: astrbot
```

B站评论 / 私信：

```http
X-Platform: bilibili
X-User-Id: 654321
X-Channel: bilibili_comment:BV1xx411c7mD
X-Channel-Type: group
X-Host-Id: bilibili
```

定时任务 / 主动总结（没有真实发送者）：

```http
X-Platform: cron
X-User-Id: system
X-Channel: cron:daily_summary
X-Host-Id: cron
```

> 主动任务也要先建档：给 `cron/system` 建一个身份（挂到某个 guest 档案），或者复用已建档的
> 系统身份。**不要借用主人的 UID**，否则会污染主人的时间轴和好感度；不建档一样 403。

---

## 4. 第三步：调用 `/v1/chat/completions`

### 4.1 请求

```http
POST /v1/chat/completions
Authorization: Bearer <key>
Content-Type: application/json
X-Platform: qq
X-User-Id: 10001
X-Channel: qq_private:10001
X-Channel-Type: private
```

```json
{
  "model": "gemini-3.8-flash",
  "messages": [
    {"role": "user", "content": "你还记得我上次说的那个暗号吗"}
  ],
  "stream": false
}
```

| 字段 | 说明 |
|---|---|
| `model` | 只是**请求标签**；真实路由由中枢「设置 → 模型配置」决定。用 `/v1/models` 返回的名字最省事 |
| `messages` | 建议带最近 10~20 条对话；中枢会在最前面重建自己的 system prompt |
| `stream` | `true` 走 SSE；身份元数据只在响应头里 |
| `tools` | OpenAI function schema 数组，见第 5 节 |
| `user` | 底层 UID 的另一种写法，等价于 `X-User-Id` |
| `tool_choice` / `temperature` / `max_tokens` 等 | 透传给上游；工具闸门裁剪后，只有强制指定的工具**确实被裁掉**时才会剥 `tool_choice`，引用工具仍在时保留 |

**关于 `system` 消息**：插件发来的 `system` 会被中枢过滤掉，人格提示词由中枢按 `data/persona.md` + 档案 + 事实 + 召回重新拼。插件想加额外设定，正确位置是中枢的人格卡或档案设定，不是请求里的 system。

**关于上下文**：中枢负责长期记忆，但召回是「跟当前问法相关」的，不保证把最近几轮原样带上。插件要自己带一个滚动窗口，否则模型容易「失忆」。推荐：

```python
history = deque(maxlen=20)   # 10 轮
history.append({"role": "user", "content": text})
payload["messages"] = list(history)
```

### 4.2 响应

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gemini-3.8-flash",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "记得，你说的是……"},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 1234, "completion_tokens": 56, "total_tokens": 1290},
  "hub": {
    "actor_id": "master",
    "actor_name": "主人",
    "archive_id": "arc_master",
    "platform": "qq",
    "sender_uid": "10001",
    "affection": 90,
    "bound_by": "binding"
  }
}
```

`hub` 是中枢附加的身份回执，插件可以拿它做：

```text
显示「当前以谁的档案在对话」
判断有没有命中档案（archive_id 为空 = 访客/未绑定）
调试身份串没串
```

同样的信息也在响应头里，流式和非流式都有：

```http
X-Hub-Actor-Id / X-Hub-Actor-Name / X-Hub-Archive-Id
X-Hub-Platform / X-Hub-Sender-Uid / X-Hub-Affection / X-Hub-Bound-By
```

`X-Hub-Actor-Name` 是 URL 编码的，读出来要 decode。

注意：响应里的 `model` 是**请求标签**，不是实际调用的模型。实际模型看控制台账本，或看 `/api/tokens`。

### 4.3 流式

```http
Content-Type: text/event-stream
```

```text
data: {"id":"...","choices":[{"delta":{"content":"记"}}]}
data: {"id":"...","choices":[{"delta":{"content":"得"}}]}
data: [DONE]
```

工具调用也在同一个流里，出现在 `delta.tool_calls`，此时 `content` 可能为空：

```json
{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"set_emotion","arguments":"{\"emotion\":\"heureux\"}"}}]}}]}
```

插件要同时处理 `delta.content` 和 `delta.tool_calls`；收到工具调用后，按第 5 节把 `role:"tool"` 结果回填，再请中枢生成正文。

规则：

```text
① 逐行读 data:，忽略空行和注释
② [DONE] 结束
③ hub 身份只在响应头，不在 chunk 里
④ 开了流式后，模型不会挂「[[记忆检索]]」那条精确指令；
   中枢的「时间锚点预取」仍然生效（前天/昨天/上次这类词会先查库）
```

如果插件只需要最终文本、又不想处理 SSE，直接用 `stream: false`。QQ 消息本来也是整条发送，非流式不会影响观感。

### 4.4 错误码

| HTTP | `error.code` | 含义 | 处理 |
|---|---|---|---|
| 401 | `missing_key` | 没带 Key / 没配 `clients.json` | 检查 `Authorization` |
| 401 | `invalid_key` | Key 错或已撤销 | 换 Key，别重试 |
| 403 | `guest_disabled` | 这个 UID 不在白名单，且临时访客关闭 | 加身份，或开临时访客 |
| 429 | `rate_limited` | 超过 `rate_limit_per_minute` | 退避重试 |
| 413 | — | 请求体过大 | 别把图片 base64 塞进 messages |
| 400 | — | JSON 不合法 | 检查序列化 |

---

## 5. 工具调用：模型决定，插件执行

中枢把 `tools` 透传给模型，模型返回 `tool_calls`，**中枢不执行插件工具**。插件要自己跑完这个循环：

```python
async def chat_with_tools(messages: list[dict], tools: list[dict]) -> str:
    payload = {"model": MODEL, "messages": messages, "tools": tools, "stream": False}
    data = await post_json("/v1/chat/completions", payload)
    message = data["choices"][0]["message"]

    while message.get("tool_calls"):
        messages.append(message)  # assistant 的 tool_calls 原样带回
        for call in message["tool_calls"]:
            name = call["function"]["name"]
            args = json.loads(call["function"].get("arguments") or "{}")
            result = await run_my_tool(name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(result, ensure_ascii=False),
            })
        data = await post_json("/v1/chat/completions", {**payload, "messages": messages})
        message = data["choices"][0]["message"]

    return message.get("content") or ""
```

### 5.1 工具页怎么看到它

工具页合并四个来源，**不需要先登记**：

| 来源 | 什么时候出现 | 怎么刷新 |
|---|---|---|
| **插件扫描** | AstrBot 和中枢同机，插件目录能被扫到（`ASTRBOT_DATA_DIR/plugins`、项目下 `astrbot/data/plugins`，或设置里配的路径） | 工具页「扫描新插件」 |
| **链路观测** | 插件发过一次带 `tools` 的 `/v1` 请求后，`tool_registry.json` 会记录 | 工具页「刷新」 |
| 配置 | `data/tool_shelves.json` 的 `always_available` / `categories` | 改完点「扫描新插件」 |
| 系统工具 | 中枢自带（宿主机 / 账本） | 自动出现 |

插件扫描只读 `metadata.yaml` + 插件 `main.py` 里的 `@filter.llm_tool`，按插件 `display_name` 分组；
扫描路径在「设置 → 常规与系统 → 插件工具扫描」里配。远端 AstrBot 扫不到目录也没关系，
第一次 `/v1` 带 `tools` 的请求会把它登记进观测列表。

> 工具页「扫描新插件」会清插件扫描的 15 秒缓存并重载货架配置；「刷新」只是重新拉当前列表。

未登记工具不会单独成组：中枢会按 `X-Caller-Plugin` → `X-Plugin-Chain` 最后一段 → `X-Host-Id` → client_id
把它归到对应插件，组名和工具名旁显示小红点；只有完全拿不到插件信息的工具才落到「未知插件」。
组名优先用插件 `metadata.yaml` 的 `display_name`（如 `Bloub 桌宠`），内部包名（`astrbot_plugin_bloub_voice`）
只用于匹配，不会直接展示。

### 5.2 小插件想要“本轮工具全量”

默认 `catalog` 会按类目裁；如果你只带几个 UI 工具（例如 `set_emotion`），可以按客户端声明：

```json
{
  "id": "bloub_voice",
  "tool_permissions": { "role": "guest", "scope": "request" }
}
```

`scope=request` 表示本轮 `tools` 全量保留、不跑分类器；仍然执行 deny / approval / host 保护和
`max_tools` 上限（默认 12）。AstrBot 这类大目录宿主继续用默认 `catalog`，避免每轮注入全部 schema。

### 5.3 几个必须知道的细节

```text
1. 只处理你自己注册的工具名。
   hub_request_tools / hub_host_control / hub_ledger_query 是中枢内部工具，
   它们由中枢自己执行，正常情况下不会漏到你这里；遇到就忽略，别执行。
2. 未登记工具默认放行（unknown_tool_policy=always），不是“必须登记才能用”；
   工具页分类只影响裁剪与 token 预算。普通小插件可以保持默认；想整轮全量就用上面的 scope=request。
   tool_choice 只在强制工具确实被裁掉时才剥；引用工具仍在时保留。
3. 如果中枢开了「工具结果压缩」，你回填的 role=tool 原文会在进模型前被清洗；
   数值和 URL 有校验，但不要把关键状态只放在工具结果里。
4. 每轮 tool_calls 都写账本，但带 tool_calls 的轮次不写时间轴；
   最终那条纯文本回复才进记忆。
5. 工具调用会带上 X-Host-Id / X-Plugin-Chain，时间轴能看出是哪个插件调了什么。
```

---

## 6. 需要更多上下文时：`/hub/v1`

`/hub/v1` 是机器接口，用 Key 鉴权，不走控制台登录态。

| 接口 | 方法 | Key 要求 | 用途 |
|---|---|---|---|
| `/hub/v1/archives/resolve?platform=&user_id=` | GET | 任意有效 Key | UID → 档案；非管理员只返回最小字段 |
| `/hub/v1/archives` | GET | **管理员** | 列出全部档案 |
| `/hub/v1/archives/{id}` | GET | **管理员** | 完整档案（事实 / 设定 / 好感度） |
| `/hub/v1/archives` | POST | **管理员** | 建档案，可顺手绑身份 |
| `/hub/v1/archives/{id}/identities` | POST | **管理员** | 给档案挂平台身份（白名单） |
| `/hub/v1/archives/{id}` | PATCH | **管理员** | 改状态 / 好感度窗口 |
| `/hub/v1/memory/search` | POST | **管理员** | 深时检索：关键词 + 时间范围 |
| `/hub/v1/knowledge/search?q=&limit=` | GET | **管理员** | 知识库检索 |
| `/hub/v1/knowledge/ingest` | POST | **管理员** | 写入知识库（自动分块） |

「管理员 Key」的判定是：**真的带了 Key，且 `default_actor == "master"`**。

### 6.1 查一个人是谁

```bash
curl "http://127.0.0.1:18895/hub/v1/archives/resolve?platform=qq&user_id=10001" \
  -H "Authorization: Bearer <key>"
```

非管理员只拿到：

```json
{"archive_id":"arc_master","display_name":"主人","role":"admin","platforms":["qq"]}
```

管理员还能拿到 `facts` / `settings` / `profile` / `affection` / `identities`。

### 6.2 深度记忆检索

```bash
curl -X POST http://127.0.0.1:18895/hub/v1/memory/search \
  -H "Authorization: Bearer <管理员 Key>" \
  -H "Content-Type: application/json" \
  -d '{"query":"小鱼干 暗号","person_id":"arc_master","budget":3000}'
```

```json
{
  "ok": true,
  "mode": "full",
  "hits": 2,
  "chars": 180,
  "original_chars": 180,
  "needs_ai_summary": false,
  "query": ["小鱼干", "暗号"],
  "records": [
    {"event_id": "...", "time": "2026-10-30 21:14", "narrative": "……", "source": "qq_group:123456"}
  ]
}
```

`mode` 会按总字数在 `full` / `narrative` / `compressed` 之间切换；`budget` 控制返回上限。

### 6.3 知识库

```bash
curl "http://127.0.0.1:18895/hub/v1/knowledge/search?q=明日方舟&limit=3" \
  -H "Authorization: Bearer <管理员 Key>"
```

```bash
curl -X POST http://127.0.0.1:18895/hub/v1/knowledge/ingest \
  -H "Authorization: Bearer <管理员 Key>" \
  -H "Content-Type: application/json" \
  -d '{"title":"活动规则","content":"……","source":"my_plugin","tags":["活动"]}'
```

返回 `201` 和 `{source_id, title, chunks}`。

> 知识库内容可能含私人资料，所以只给管理员 Key。普通插件不要为了「顺手」拿管理员 Key。

---

## 7. 最小可运行插件（AstrBot v4）

下面这份示例把「收消息 → 调中枢 → 发回复」跑通，可以直接改成你的插件。

### 7.1 目录

```text
astrbot/data/plugins/astrbot_plugin_hub_demo/
├── metadata.yaml
├── _conf_schema.json
└── main.py
```

### 7.2 `metadata.yaml`

```yaml
name: astrbot_plugin_hub_demo
display_name: 中枢接入示例
desc: 把消息转发到 Stellaris 中枢，使用中枢的人格与记忆。
short_desc: Stellaris 中枢接入示例。
version: v1.0.0
astrbot_version: ">=4.26,<5"
author: you
```

### 7.3 `_conf_schema.json`

```json
{
  "hub_base_url": {
    "type": "string",
    "description": "中枢地址",
    "default": "http://127.0.0.1:18895"
  },
  "hub_api_key": {
    "type": "string",
    "description": "中枢签发的插件 Key",
    "default": ""
  },
  "model": {
    "type": "string",
    "description": "请求标签，取 /v1/models 里的名字",
    "default": "gemini-3.8-flash"
  },
  "platform": {
    "type": "string",
    "description": "平台名，如 qq / bilibili",
    "default": "qq"
  },
  "timeout_seconds": {
    "type": "int",
    "description": "单次请求超时",
    "default": 120
  }
}
```

### 7.4 `main.py`

```python
from __future__ import annotations

import json
from collections import defaultdict, deque
from urllib.parse import quote

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_NAME = "astrbot_plugin_hub_demo"


@register(PLUGIN_NAME, "you", "把消息转发到 Stellaris 中枢", "1.0.0")
class HubDemoPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))

    async def initialize(self):
        timeout = float(self.config.get("timeout_seconds", 120))
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        )

    async def terminate(self):
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ---------------- 中枢契约：地址、身份头 ----------------
    def _base_url(self) -> str:
        return str(self.config.get("hub_base_url", "http://127.0.0.1:18895")).rstrip("/")

    def _headers(self, event: AstrMessageEvent) -> dict:
        group_id = str(event.get_group_id() or "")
        return {
            "Authorization": f"Bearer {self.config.get('hub_api_key', '')}",
            "Content-Type": "application/json",
            "X-Platform": str(self.config.get("platform", "qq")),
            "X-User-Id": str(event.get_sender_id() or ""),
            "X-User-Name": quote(str(event.get_sender_name() or ""), safe=""),
            "X-Channel": event.unified_msg_origin,
            "X-Channel-Type": "group" if group_id else "private",
            "X-Group-Id": group_id,
            "X-Host-Id": "astrbot",
            "X-Plugin-Chain": f"astrbot,{PLUGIN_NAME}",
        }

    # ---------------- 调 /v1 ----------------
    async def _chat(self, event: AstrMessageEvent, text: str) -> dict:
        if self._session is None:
            raise RuntimeError("插件尚未初始化")
        history = self._history[event.unified_msg_origin]
        history.append({"role": "user", "content": text})
        payload = {
            "model": self.config.get("model", ""),
            "messages": list(history),
            "stream": False,
        }
        try:
            async with self._session.post(
                f"{self._base_url()}/v1/chat/completions",
                headers=self._headers(event),
                json=payload,
            ) as resp:
                raw = await resp.text()
                if resp.status != 200:
                    raise RuntimeError(f"中枢 {resp.status}: {raw[:200]}")
                data = json.loads(raw)
        except Exception:
            history.pop()  # 失败的那句话不要留在上下文里
            raise
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        history.append({"role": "assistant", "content": content})
        return {"content": content, "hub": data.get("hub") or {}}

    @filter.command("hub")
    async def cmd_hub(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        for prefix in ("/hub", "hub"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break
        if not text:
            yield event.plain_result("用法：/hub <要说的话>")
            return
        try:
            result = await self._chat(event, text)
        except Exception as exc:
            logger.warning(f"[{PLUGIN_NAME}] 调用中枢失败: {exc}")
            yield event.plain_result("中枢暂时联系不上，稍后再试")
            return
        yield event.plain_result(result["content"] or "（空回复）")
```

### 7.5 改成你的插件时要动的地方

```text
① metadata.yaml 的 name 必须以 astrbot_plugin_ 开头
② _conf_schema.json 里的 hub_api_key 让用户在 AstrBot WebUI 填，不要写死在代码里
③ platform 要写真实平台名；不同平台共用同一个 UID 会串档案
④ X-Channel 用「平台_类型:ID」的稳定格式，群聊和私聊要能区分
⑤ 如果插件要自己跑工具循环，把 _chat 换成第 5 节的版本
⑥ 主动发消息（不经过 event）时，必须自己构造 UID 和 X-Channel
⑦ 示例用 aiohttp；AstrBot 运行环境通常已带，独立插件仍要在 requirements 里声明
```

> AstrBot 的插件 API 会随版本演进；上面用的是 v4.26 系列的写法。升级后如果 `initialize` / `filter` 有变化，以 [AstrBot 官方插件文档](https://docs.astrbot.app/dev/star/plugin-new.html) 为准，中枢这一侧不用改。

### 7.6 带工具的最小改动

在 `_chat` 的 payload 里加上 `tools` 即可；插件不需要先到中枢登记：

```python
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_emotion",
            "description": "设置桌宠表情；每次回复最多调用一次",
            "parameters": {
                "type": "object",
                "properties": {"emotion": {"type": "string", "enum": ["heureux", "triste"]}},
                "required": ["emotion"],
            },
        },
    }
]

payload = {
    "model": self.config.get("model", ""),
    "messages": list(history),
    "stream": False,
    "tools": TOOLS,
    "tool_choice": "auto",
}
```

接完后：

```text
1. 插件发一次带 tools 的 /v1 请求
2. 工具页「刷新」→ 出现 set_emotion（source=observed，未登记）
3. 模型返回 tool_calls → 用第 5 节的循环执行并回填 role:"tool"
4. 如果 AstrBot 和中枢同机、插件目录能被扫描到，工具页「扫描新插件」会直接显示
   source=plugin，不需要等第一次请求
```

未登记不影响调用。若把 `unknown_tool_policy` 改成 `deny`，它会从模型可见工具里消失
（不是只隐藏工具页），默认不建议——新工具会悄悄失效。

---

## 8. 中枢侧登记（可选，不改插件协议）

插件只要带了第 3 节的标准头，中枢就能认出来，**不需要为中枢改协议**。只有字段名和标准头不一致时，才做一层归一化。

> 工具也不需要先登记：未登记工具默认放行，工具页的登记只负责归类和类目裁剪。
> 新插件装好后点「扫描新插件」即可看到；远端插件发一次带 `tools` 的请求后，刷新工具页也能看到。

### 8.1 声明式适配器（推荐，先试这个）

新建 `console/adapters/declarative/my_plugin.json`：

```json
{
  "plugin_id": "my_plugin",
  "enabled": true,
  "priority": 5,
  "match": {"path_prefix": "/my", "user_agent": "my-plugin/"},
  "fields": {
    "platform": "'my_platform'",
    "uid": "body.sender.id | headers.X-User-Id",
    "name": "body.sender.name | headers.X-User-Name",
    "session": "body.chat_id | session_id",
    "group": "body.group_id",
    "host_id": "'my_plugin'"
  }
}
```

只有 `"enabled": true` 才会加载。声明式适配器只做字段映射，不执行代码；`fields` 里能读
`headers.*` / `body.*` / `channel_id` / `session_id` / `path`，用 `|` 写降级顺序。

### 8.2 生成 Python 适配器（结构复杂时）

```powershell
# 只看提示词，不花钱
python scripts\generate_plugin_adapter.py --plugin-dir <插件目录>

# 真正调用执行 AI，生成 adapter + test
python scripts\generate_plugin_adapter.py --plugin-dir <插件目录> --run
```

生成物：

```text
console/adapters/generated/<plugin_id>.py
tests/test_adapter_<plugin_id>.py
```

生成物默认 `ENABLED = False`。人工 review、跑完测试后，把 `ENABLED` 改成 `True` 才会被注册表加载。**生成不等于启用。**

### 8.3 时间轴标签

```text
tag_primary      X-Plugin-Chain → X-Host-Id → 平台上下文 → 兜底
tag_secondary    档案 ID；没有档案才用 平台:UID
tag_permission   admin / guest / system / unknown
```

插件侧只要把 `X-Host-Id` 和 `X-Plugin-Chain` 写对，控制台的「记忆库」就能按插件筛选。

---

## 9. 验收清单

接完后按这个顺序验，不要只看「有回复」：

```text
[ ] GET /v1/models 返回模型列表
[ ] 带 Key + X-User-Id 的 /v1/chat/completions 返回 200
[ ] 响应 hub.archive_id 与预期档案一致（不是空、不是别人的）
[ ] 控制台「记忆库」出现该插件的 tag_primary
[ ] 控制台「账单流水」来源是请求里的 `X-Channel`，模型是实际模型
[ ] 不带 Key → 401 missing_key
[ ] 未白名单 UID → 403 guest_disabled（或按预期进临时访客）
[ ] 错误 Key → 401 invalid_key，不重试
[ ] 超时 / 中枢挂掉时插件有兜底话术，不是抛异常刷屏
[ ] 流式（如果开了）能读到 [DONE]，且能读响应头
[ ] 带 tools 的请求能在工具页看到（本地插件扫描，或首次请求后刷新）
[ ] 未登记工具也能被模型调用（不再被“无需工具”误裁）
[ ] 需要全量工具的小插件用 scope=request 验证过；大目录宿主仍走 catalog
[ ] 工具循环（如果开了）只执行自己的工具，遇到 hub_* 忽略
[ ] 日志里没有完整 Key、没有完整私聊原文
```

---

## 10. 排错

| 现象 | 多半是 | 处理 |
|---|---|---|
| `401 missing_key` | 没带 Key / `clients.json` 不存在 | 补 Key；确认 `require_key` |
| `401 invalid_key` | Key 拼错 / 已撤销 | 用 `data/clients.json` 里的原值 |
| `403 guest_disabled` | UID 不在白名单 | 加身份，或开临时访客 |
| `403 local_only` | 开了「只允许本机」 | 把插件来源网段加进 `allow_cidrs` |
| 回复永远是同一个人 | `default_actor` 写成了 `guest` | 多用户插件改成 `master` |
| 档案串了 | `X-Platform` 缺失或写错 | 显式传平台名 |
| 群聊把群号当人 | `X-User-Id` 填了群号 | 填发送者 UID，群号放 `X-Channel` / `X-Group-Id` |
| 模型不记得上一句 | 插件没带历史消息 | 带最近 10~20 条 `messages` |
| 插件塞的 system 没生效 | 中枢会重建人格提示词 | 把人设放中枢，不要放请求 |
| 时间轴没有记录 | 带 tool_calls、空回复、或关了 ingest | 看 `allow_ingest_timeline` 和回复内容 |
| 工具页看不到新工具 | 插件目录不在扫描路径 / 15 秒缓存 | 设置里加插件目录，点「扫描新插件」；远端插件先发一次带 tools 的请求再「刷新」 |
| 模型不调用插件工具 | 旧版本闸门误裁，或 `unknown_tool_policy=deny` | 升级到 F22 后未登记工具默认放行；检查 `tool_shelves.json` 的 policy |
| 强制 tool_choice 没生效 | 引用的工具被裁掉，或上游不支持 | 看工具页的 `tools_after`；小插件用 `scope=request` |
| 账本模型不对 | 看的是响应 `model`（请求标签） | 以账本 / `/api/tokens` 为准 |
| 流式读不到身份 | 只解析 `data:`，没读响应头 | 在 HTTP 层读 `X-Hub-*`，或改非流式 |
| 调用很慢 | 模型超时 / 召回慢 | 客户端超时 ≥120s，别用 10s 硬超时 |

---

## 11. 安全红线

```text
① 插件 Key 只放服务端配置，不进前端、不进仓库、不进日志
② 按本指南配置的多用户插件 Key（`default_actor: master`）具备管理接口权限，
   泄露等于泄露档案
③ 不要用 Key 的 default_actor 来「方便地变成主人」；它只决定信不信 UID
④ 不要把平台 UID、QQ 号、B站 UID 写进源码，真实值只放 data/ 或插件配置
⑤ 不要让插件直连中枢数据库；一切走 HTTP
⑥ 中枢开了请求签名后，公网来源必须带 X-Timestamp + X-Signature；
   内网来源默认豁免，插件在局域网/容器里通常不用改
⑦ 插件日志只记事件和错误码，不记完整 Key、不记完整私聊原文
```

签名规则见 [API_SECURITY.md](API_SECURITY.md)；身份和白名单细节见 [IDENTITY_ACCESS.md](IDENTITY_ACCESS.md)。

> 本文按 2026-11-01 的代码行为写。如果和实现冲突，以 `console/server.py`、
> `agent_core/clients.py`、`agent_core/identity.py` 为准，并回来改这份文档。

---

## 12. 相关文档

- [从下载项目到连接 AstrBot](ASTRBOT_SETUP.md)
- [一键连接 AstrBot](CONNECT_ASTRBOT.md)
- [身份传输层契约](TRANSPORT_LAYER.md)
- [身份、白名单与访问策略](IDENTITY_ACCESS.md)
- [插件接入契约](PLUGIN_ADAPTERS.md)
- [功能挂载架构](FUNCTION_MOUNTING.md)
- [安全总览](SECURITY.md) / [API 安全](API_SECURITY.md)
- [后续开发规范](DEV_RULES.md)
