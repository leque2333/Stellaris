# DeepSeek Harness 适配版（构思）

> 状态：设计稿，未实施  
> 目标版本：DSH `0.2.x` / Cordis `~4`  
> 结论：优先做“DSH 原生插件 + Stellaris 记忆侧车”，而不是把整个 Stellaris 塞进 DSH。

## 1. 两种接法

| 方式 | DSH 的角色 | Stellaris 的角色 | 适合 |
|---|---|---|---|
| **A. Provider 直连** | DSH 只当客户端 | 完整 `/v1` 大脑：人格、记忆、账本 | 想保留完整安桉人格 |
| **B. 原生插件** | DSH 继续用 DeepSeek 原生模型 | 记忆 / 档案 / 账本 / 预警侧车 | 想保留 DSH 的工具、技能、子代理 |

推荐 **B 为主，A 作为可选模式**。

原因：DSH 的强项是工具循环、技能、子代理、工作流；如果所有请求都绕回 Stellaris `/v1`，DSH 自己的工具和会话能力会被削弱，而且人格 prompt 容易和 DSH 的 system prompt 打架。

## 2. 推荐架构

```text
DSH Web / CLI / SDK
  │
  ├─ DeepSeek 原生模型
  ├─ DSH 工具 / 技能 / 子代理
  │
  └─ @stellaris/dsh-plugin
       ├─ agent/pre-step   → 注入记忆块
       ├─ agent/turn-stopping → 回写这一轮
       └─ tools            → 记忆 / 档案 / 账本 / 预警
                 │ HTTP
                 ▼
          Stellaris 18895
          ├─ /api/agent/context
          ├─ /api/agent/ingest
          ├─ /api/agent/profile
          ├─ /api/ledger/query
          └─ /api/token/alerts
```

## 3. DSH 插件包

```text
packages/stellaris-dsh/
  package.json          @stellaris/dsh-plugin
  src/index.ts          Cordis 入口
  src/client.ts         Stellaris HTTP 客户端
  src/tools.ts          defineTool 注册
  src/prompt.ts         记忆块渲染
  src/ingest.ts         回写与幂等
```

### 3.1 Cordis 入口

```ts
import type { Context } from '@deepseek-ai/cordis'
import { defineTool } from '@deepseek-ai/dsh-tools'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

export const name = 'stellaris-memory'
export const inject = ['tools']

export function apply(ctx: Context, config: Config) {
  const client = new StellarisClient(config)

  ctx.tools.register(defineTool({/* memory_search */}))
  ctx.tools.register(defineTool({/* profile_get */}))
  ctx.tools.register(defineTool({/* ledger_query */}))

  ctx.on('agent/pre-step', async ({ agent, messages, signal }, next) => {
    const decision = await next()
    if (decision.kind === 'reject' || signal.aborted) return decision
    const block = await client.context({ agent, messages: decision.messages })
    if (!block) return decision
    return {
      ...decision,
      messages: [...decision.messages, createUserMessage({
        content: [{ type: 'text', text: block }],
        source: { kind: 'stellaris-memory', form: 'snapshot' },
      })],
    }
  })
}
```

### 3.2 记忆注入

不要直接改 DSH 的全局 system prompt；用 **user-role 注入消息**，放在用户消息之后：

```text
<stellaris-memory>
档案：乐阙（admin）
平台：dsh
最近时间轴：
- 10:20 讨论记忆导出
- 09:50 修复账本改单价
长期事实：
- 偏好中文、直接给结论
</stellaris-memory>
```

原因：

- 不破坏 DSH 的 system prompt 缓存
- 不覆盖 DSH 自己的工具/技能说明
- 每轮可以按 token 预算裁剪
- 没有记忆时完全不注入

配置项：

```ts
interface Config {
  baseUrl: string
  apiKeyEnv: string
  archiveId?: string
  userId?: string
  injectMemory: boolean       // 默认 true
  injectBudgetChars: number   // 默认 2000
  autoIngest: boolean         // 默认 true
  ingestRoles: ('user' | 'assistant')[]
  ledgerTool: boolean         // 默认 false，管理员再开
}
```

### 3.3 回写

在 `agent/turn-stopping` 读取本轮用户/助手文本，写回：

```json
{
  "idempotency_key": "dsh:<sessionId>:<turn>",
  "archive_id": "arc_master",
  "platform": "dsh",
  "source": "dsh",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

规则：

- 只写对话正文，不写工具调用和工具结果
- `sessionId + turn` 幂等，重试不重复
- 失败只记日志，不阻塞 DSH 回合
- 用户关闭 `autoIngest` 时不写

### 3.4 工具

| 工具 | 作用 |
|---|---|
| `stellaris_memory_search` | 按语义/时间检索记忆 |
| `stellaris_memory_recent` | 最近时间轴 |
| `stellaris_memory_ingest` | 手动写一条 |
| `stellaris_profile_get` | 档案、角色、平台绑定 |
| `stellaris_ledger_query` | 只读账本查询 |
| `stellaris_alert_status` | 余额 / Token 预警 |

管理员才注册 `ledger_query` 和 `alert_status`；普通档案只拿记忆和画像。

## 4. Stellaris 侧新增接口

### 4.1 `POST /api/agent/context`

```json
{
  "platform": "dsh",
  "user_id": "dsh:local",
  "archive_id": "arc_master",
  "query": "刚才说的导出方案",
  "budget_chars": 2000
}
```

返回：

```json
{
  "ok": true,
  "archive": {"id": "arc_master", "display_name": "乐阙", "role": "admin"},
  "text": "<stellaris-memory>...</stellaris-memory>",
  "sources": ["timeline", "facts"]
}
```

### 4.2 `POST /api/agent/ingest`

```json
{
  "idempotency_key": "dsh:session:12",
  "archive_id": "arc_master",
  "platform": "dsh",
  "source": "dsh",
  "messages": [{"role": "user", "content": "..."}]
}
```

返回写入的事件 ID 和跳过原因。

### 4.3 `GET /api/ledger/query`

复用 `hub_ledger_query` 的只读逻辑：

```text
action=summary|recent|daily
days=7
limit=10
```

## 5. DSH profile 配置

### 5.1 原生插件模式

```yaml
- id: stellaris-memory
  name: '@stellaris/dsh-plugin'
  config:
    baseUrl: http://127.0.0.1:18895
    apiKeyEnv: STELLARIS_API_KEY
    archiveId: arc_master
    injectMemory: true
    injectBudgetChars: 2000
    autoIngest: true
    ledgerTool: true
```

安装：

```bash
dsh plugin --profile web add @stellaris/dsh-plugin
```

### 5.2 Provider 直连模式（可选）

```yaml
- id: llm-pi-ai
  name: '@deepseek-ai/dsh-llm-pi-ai'
  config:
    providers:
      stellaris:
        api: openai-completions
        baseURL: http://127.0.0.1:18895/v1
        apiKeyEnv: STELLARIS_API_KEY
        headers:
          X-Platform: dsh
          X-User-Id: !!js process.env.STELLARIS_UID
        models:
          - id: stellaris
            contextWindow: 1048576
```

```yaml
- id: agent-default-model
  name: '@deepseek-ai/dsh-agent-default-model'
  config:
    provider: stellaris
    model: stellaris
```

注意：

- 这条路线会让 Stellaris 接管人格、记忆、账本
- DSH 的 system prompt 要压到最小，避免双人格
- DSH 工具仍由 DSH 执行；Stellaris 只负责给工具表和记录
- 需要给 DSH 单独的 client key，不要复用管理员 Key

## 6. MCP 过渡方案

如果不想先写 Cordis 插件，可以先做一个 `stellaris-mcp`：

```yaml
- name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: stellaris
    transport: streamable-http
    url: http://127.0.0.1:18895/mcp
```

工具自动变成：

```text
mcp__stellaris__memory_search
mcp__stellaris__profile_get
mcp__stellaris__ledger_query
```

优点：一两天的量，不改 DSH 核心。  
缺点：不能在 `agent/pre-step` 自动注入记忆，只能靠模型主动调工具。

## 7. 身份与安全

- DSH 使用独立 client key，平台固定 `dsh`
- `archive_id` 由配置或 DSH 用户映射决定，不从模型输入猜
- 记忆注入默认只读；写回由插件控制
- 账本/预警工具仅管理员档案
- 不把 Stellaris 的宿主机控制工具暴露给 DSH
- 所有 API 走现有会话/签名/本机限制
- DSH 侧记录 `audit_log`：`dsh_context`、`dsh_ingest`、`dsh_ledger_query`

## 8. 分阶段落地

| 阶段 | 内容 | 预估 |
|---|---|---|
| P0 | `stellaris-mcp` 工具桥 | 1–2 天 |
| P1 | `@stellaris/dsh-plugin` 工具 + pre-step 注入 | 3–5 天 |
| P2 | turn-stopping 回写 + 幂等 | 2 天 |
| P3 | provider 直连模式 + profile 示例 | 2 天 |
| P4 | 打包、版本兼容、验收 | 2 天 |

## 9. 验收

- 没有 Stellaris 时 DSH 正常启动，只少工具
- Stellaris 挂了不阻塞 DSH 回合
- 记忆注入不超过预算
- 回写幂等：同一 `sessionId+turn` 只写一次
- 非管理员看不到账本/预警工具
- DSH 的原有 fs/pwsh/subagent/workflow 工具不受影响
- provider 模式下 DSH 工具仍能正常执行

## 10. 不做什么

- 不把 Stellaris 的 Web 控制台嵌进 DSH
- 不让 DSH 直接读 `memory.db`
- 不把宿主机控制工具自动暴露给 DSH
- 不要求 DSH 改自己的 OpenAI 协议
- 不做双向实时同步；以会话边界回写为准
