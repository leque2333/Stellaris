# 工具按需加载（Tool Shelves）

> 落点：中枢 `/v1` 网关（不改 AstrBot）
> 代码：`agent_core/tool_shelves/`
> 配置：`data/tool_shelves.json`、`data/clients.json`
> 状态：P1–P4 已上线（enforce_soft + 结果压缩 + 薄壳执行器）

---

## 0. 四层防护速查

```text
① 分类裁剪   只把这一轮可能用到的类别交给主力 AI         → 省 token、少误用
② 权限硬拒绝 非管理员客户端一律拿不到宿主机工具          → 安全兜底
③ 结果压缩   大体积工具返回先粗筛再清洗，短数值工具不过模型 → 防撑爆、防数字幻觉
④ 薄壳接管   AstrBot 侧只暴露 call_raw_tool / 薄工具，
             原始工具从模型工具表摘掉，结果仍走中枢清洗   → 主模型彻底看不见生肉
```

四层的失败策略统一为**放行**：配置坏了、分类器挂了、清洗失败、薄壳挂，都退回旧行为，绝不把机器人弄哑。

---

## 1. 解决什么

AstrBot 每轮都会把**全量工具 schema** 塞进 `/v1` 请求。工具越多：

- 短对话里工具说明比人设+记忆还长，注意力被稀释（实测 8 个工具 ≈ 2400 tokens，占短会话输入的 34%）；
- 长对话每轮重复计费；
- 主力 AI 容易"看到锤子就想敲钉子"，用工具去解决本该聊天的事。

闸门把工具按大类裁到这一轮真正可能用到的几个，再交给主力 AI。

```text
AstrBot ──(全量 tools + 记忆 + 消息)──► /v1 网关
                                          │
                                ┌─────────▼──────────┐
                                │  0. 快判（0 次模型）│
                                │  1. 执行 AI 选大类  │
                                │  2. 裁 tools        │
                                └─────────┬──────────┘
                                          ▼
                              主力 AI（人格+全量记忆+少量工具）
```

---

## 2. 决策流水线

| 顺序 | 条件 | 动作 | 是否调模型 |
|---|---|---|---|
| 1 | 请求里没有 `tools` | 直接放行 | 否 |
| 2 | 闸门关闭 / 配置缺失 / 客户端在 `exempt_clients` | 全量放行 | 否 |
| 3 | 最后一条消息是 `role=tool`（工具刚返回） | 沿用上轮类别，最多粘 `sticky_tool_turns` 轮 | 否 |
| 4 | 用户文本是纯语气词（嗯/好/哈哈/晚安），且不含动作词 | 沿用上轮类别；没有上轮只留常驻 | 否 |
| 5 | 其余 | 问执行 AI（DeepSeek）选大类 | **是** |

第 3 条特别说明：工具结果回灌**不重新分类、也不清空工具**。原因是工具链条常是「搜 → 读正文 → 回答」，第二轮清空工具会让"读正文"断在半路。

### 发给执行 AI 的内容（严格瘦身）

只发这些，**不发人设、不发记忆、不发工具 schema**：

```text
当前时间：2026-10-30 23:40 周四
场景：qq friend
最近三句：
  用户：今天想早点睡
  助手：那就去睡呀
当前：灯关了
```

system 侧只有：类别清单（`key 中文名：一句话简介`）+ 输出契约 + 4 条规则。

输出：

```json
{"need_tools": false, "categories": [], "confidence": 0.95, "reason": "仅陈述状态，无明确操作意图"}
```

### 分类口径（重要）

默认 **只在出现明确动作意图时才选类别**；情绪、抱怨、状态陈述（我困了 / 灯关了 / 好无聊）一律 `need_tools=false`。

这不是偷懒，是实测结论：让 DeepSeek 做"情境联想"时，它会把「今天开会好累」判成"可能需要放点舒缓音乐"，于是每句情绪化的闲聊都多出一次 700ms 调用 + 一堆用不上的生图工具——反而把主 AI 引向"用工具解决情绪问题"。少给工具比多给工具安全。

需要「半夜关灯 → 联想到放歌」时：

```json
"classifier": { "situational_inference": true }
```

打开后 `categories.*.hint` 会进入 prompt，模型开始按情境联想。**建议等真的接上音乐/灯控工具再开**——现在影音类只有 `generate_image` / `check_image_quota`，联想出来的工具也没法放歌。

---

## 3. 裁剪与字段处理

```python
keep = 常驻工具 ∪ 选中类别的工具 ∪ 未归类工具(unknown_tool_policy=always)
payload = [t for t in tools if t.name in keep]   # 保持上游原始顺序
```

三条硬规则：

1. **判定无需工具** → 只留常驻；常驻被裁光时，`tools`、`tool_choice`、`parallel_tool_calls` **三个字段整段删除**，绝不保留 `tool_choice:"none"`（部分上游会 400）。
2. **任何裁剪发生后** → `tool_choice` / `parallel_tool_calls` 一律剥掉（客户端常带 `auto`，上游行为不一致）。
3. **不往主 AI 的 system prompt 追加任何动态内容**，保护 prompt cache。`prompt_hint.enabled` 默认 false。

---

## 4. 兜底（enforce_soft）

| 情况 | 行为 |
|---|---|
| 分类器超时 / 报 5xx / 解析失败 | 先试上轮类别；没有则**全量放行**（`on_failure=previous_then_all`） |
| 分类置信度 < `confidence_floor` | 同上，不放任低置信裁剪 |
| 裁剪后上游返回**空响应**（无正文也无 tool_calls） | 用上游**全量**工具自动重试一次（每轮最多一次） |
| 闸门自身抛异常 | 全量透传，日志留痕 |
| 配置缺失 / JSON 损坏 | 闸门自动关闭，等价旧行为（全量透传） |

配置损坏时**永远偏向"多给工具"**，不会因为配置写错让机器人变哑巴。

---

## 5. 权限硬拒绝（宿主机安全）

两处配置，三层校验：

```text
data/tool_shelves.json → protection：
  admin_clients      ["astrbot"]                       ← 只有这些客户端算管理员
  host_tools         ["control_host_pc_via_antigravity"] ← 敏感工具
  deny_for_non_admin  true                             ← 非管理员一律拒绝

data/clients.json → 每个客户端：
  tool_permissions.role   admin / guest
  tool_permissions.deny   ["control_host_pc_via_antigravity", ...]
```

判定顺序（任何一层命中即拒绝）：

```text
1) 客户端 ID 不在 admin_clients 名单        → 强制拒绝 host_tools（纯代码兜底）
2) 客户端自称 role=admin 但不在名单里        → 仍然拒绝（双条件）
3) clients.json 的 tool_permissions.deny   → 逐客户端拒绝
4) protection.global_deny                  → 全局拒绝
```

**关键点**：第 1 条是纯代码保险。即使 `clients.json` 写漏了 deny、或者根本不存在，
非管理员客户端也拿不到宿主机工具。第 2 条防"改配置冒充管理员"。

`deny` 在闸门关闭（`gateway.enabled=false`）时**依然生效**——安全策略不随灰度开关失效。

> ⚠️ `data/clients.json` 不存在时，中枢对 `/v1` 完全不校验 Key（兼容旧行为）。
> 生产必须保留该文件；删掉它等于同时关掉 Key 校验和客户端级 deny。

---

## 6. 结果压缩模式（P3）

工具返回的生肉在送进主力 AI 之前先清洗，两步走、顺序固定：

```text
第 1 步（纯代码，0 次模型调用）
  去 ANSI 颜色码 → 去 <script>/<style> → <a> 转「文字 (URL)」 → 去标签
  → 压缩空白/空行 → 短行去重 → 头 70% + 尾 30% 截断
  实测：8.4KB 搜索结果 → 5KB（延迟 0）

第 2 步（只有大体积工具才走）
  粗筛后的文本交给执行 AI（DeepSeek），严格 JSON 输出：
  {"ok","summary","points[{"k","v"}]","sources","error","truncated","dropped"}
```

### 三类工具三条路

| 类型 | 工具 | 处理 | 是否过模型 |
|---|---|---|---|
| 大体积文本 | `you-search` / `you-contents` / `you-discover` / `bilibili_hub` / `control_host_pc_via_antigravity` | 粗筛 + 模型摘要 | 是 |
| 简短数值/状态 | `you-balance` / `check_image_quota` / `get_weather` / `send_message_to_user` / `future_task` | 代码格式化或原样放行 | **绝对不过** |
| 未配置 | 其他 | 仅超长截断 | 否 |

`SAFE_SHORT_TOOLS` 是硬编码白名单：数字类工具永远不进模型，从根上杜绝数字被改写。

### 防幻觉三条硬约束

```text
1. 具体数值（小数/百分号/货币/千分位）必须在原文出现过，否则整段作废、回退代码粗筛
2. URL 忽略 query/fragment 后必须能在原文找到；模型漏掉的原文 URL 会自动补回 sources
3. BV 号必须原样；truncated 标记必然传递，主力 AI 知道"还有东西没看到"
```

设计上刻意**只拦具体数值，不拦纯整数**：模型写"共 60 条""worker 0 至 199"属于可推导计数，
拦掉它们只会让摘要变废。拦错比漏拦更伤——所以宁可漏。

### 实测效果（真实 DeepSeek）

```text
you-search 60 条结果      8438 字 → 562 字（-93%），2.1s，1 次模型调用
宿主机 systemctl 输出      7413 字 → 214 字（-97%），ANSI 已去
you-balance               不走模型，原文保留
```

### 配置

```json
"compression": {
  "enabled": true,
  "slot": "logic_slot",
  "timeout_ms": 8000,
  "max_raw_chars": 24000,
  "min_model_chars": 1200,
  "skip_tools": ["send_message_to_user", "future_task"],
  "profiles": {
    "you-search":  { "mode": "model", "max_chars": 1600, "strip_html": true },
    "you-contents":{ "mode": "model", "max_chars": 2000, "strip_html": true },
    "bilibili_hub":{ "mode": "model", "max_chars": 1600, "strip_html": true },
    "control_host_pc_via_antigravity": { "mode": "model", "max_chars": 1600, "strip_html": false }
  }
}
```

- `mode: "code"` → 只粗筛，不调模型（想省钱/怕延迟就用这个）。
- `min_model_chars`：粗筛后短于这个长度就直接放过，省一次调用。
- 清洗失败（超时/解析失败/数值校验不过）→ 回退到代码粗筛结果，**对话不受影响**。

---

## 7. 薄壳接管（P4）

P1–P3 解决的是"少给工具、压结果"，但原始工具仍然躺在 AstrBot 的工具表里。
P4 把它们从模型视野里彻底摘掉，只留薄壳：

```text
主模型工具表（P4 后）
├─ send_message_to_user      # 常驻
├─ future_task               # 常驻
├─ you_search_clean          # 薄壳：搜索（结果已压缩）
├─ you_contents_clean        # 薄壳：读网页（结果已压缩）
├─ call_raw_tool             # 执行器壳：按白名单调被隐藏的原始工具
├─ generate_image / check_image_quota / get_weather
└─ you-discover              # 没接薄壳，暂留

被摘掉的原始工具（模型看不见，执行器调得到）
├─ you-search / you-contents / you-balance
└─ bilibili_hub / control_host_pc_via_antigravity（若装了）
```

### 7.1 两条路径

**路径 A：中枢薄壳 MCP（已上线，You.com 系列）**

```text
AstrBot ──stdio MCP──► hub-tools 薄服务（容器内）
                          │ npx 拉起 You.com 官方 MCP
                          ▼
                      生肉（20~30KB）
                          │ POST /api/tools/clean
                          ▼
                      中枢清洗（同一套 P3 逻辑）
                          │ 摘要（~1KB）
                          ▼
                       主模型
```

实现：`console/mcp_hub_tools.py`，AstrBot 侧配置在 `mcp_server.json → hub-tools`。
薄工具名：`you_search_clean` / `you_contents_clean`。

**路径 B：AstrBot 执行器插件（已上线，通用）**

```text
主模型 ──call_raw_tool(tool=you-search, arguments={...})──► 插件
  插件：① 校验管理员 ② 校验白名单 ③ 从工具管理器取原始工具执行
  生肉 ──POST /api/tools/clean──► 中枢清洗 ──► 摘要回给主模型
```

实现：`astrbot_plugins/astrbot_plugin_hub_tool_executor/`。
关键机制：`@filter.on_astrbot_loaded()` 里把白名单工具**停用**——
停用后不在模型工具表里，但 AstrBot 的 `get_func()` 有"未激活回退"，插件仍能调用。

### 7.2 中枢清洗接口

```text
POST /api/tools/clean
  Authorization: Bearer <client key>        # clients.json 存在时必填
  {"tool":"you-search","args":{...},"raw":"...","user_query":"..."}
  →
  {"ok":true,"text":"压缩后的摘要","truncated":false,"stats":{...}}
```

任何异常都返回 `ok:true` + 截断原文，绝不让薄壳调用失败。

### 7.3 为什么不是"插件侧中继清洗"

AstrBot 的 `on_tool_end` 钩子能拿到 `tool_result`，但返回类型是 `None`，
**改不了**已经写回消息的结果；插件也没有"调用另一个工具并替换结果"的公开 API。
所以只能由中枢/薄壳自己拥有工具入口。

### 7.4 部署

```powershell
python scripts\deploy_p4.py            # 只同步
python scripts\deploy_p4.py --restart  # 同步并重启中枢 + AstrBot
```

手工要点：

```text
1. console/mcp_hub_tools.py 要放进 AstrBot 挂载卷：astrbot/data/mcp_hub_tools.py
2. 插件目录放到 astrbot/data/plugins/astrbot_plugin_hub_tool_executor/
3. mcp_server.json 增加 hub-tools；原有 you-search 可保留（会被执行器隐藏）
4. cmd_config.json → plugin_set 增加 astrbot_plugin_hub_tool_executor
5. 重启 AstrBot，日志出现"已从模型工具表隐藏: you-search"即成功
```

### 7.5 实测

```text
AstrBot 工具表      you-search / you-contents / you-balance 已隐藏
薄壳 MCP            只暴露 you_search_clean / you_contents_clean
端到端              容器内薄壳 → 宿主机中枢清洗 → 1138 字摘要（原始 26.7KB）
执行器链路          8942 字原始结果 → 490 字摘要
```

## 8. 配置 `data/tool_shelves.json`
```json
{
  "gateway": {
    "enabled": true,
    "mode": "enforce_soft",
    "exempt_clients": [],
    "max_tools": 12,
    "sticky_tool_turns": 2,
    "on_failure": "previous_then_all",
    "log_decisions": false,
    "classifier": {
      "slot": "logic_slot",
      "timeout_ms": 4000,
      "temperature": 0.0,
      "max_tokens": 120,
      "confidence_floor": 0.45,
      "situational_inference": false
    },
    "auto_expand": { "enabled": true, "retry_on_empty": true }
  },
  "fast_path": { "min_chars": 2, "max_chars": 10, "trivial_regex": "...", "force_tools_regex": "..." },
  "always_available": ["send_message_to_user", "future_task"],
  "categories": {
    "media":    { "label": "影音类",   "brief": "...", "tools": ["generate_image", "check_image_quota"] },
    "search":   { "label": "搜索类",   "brief": "...", "tools": ["you-search", "you-contents", "you-discover", "you-balance"] },
    "server":   { "label": "服务器类", "brief": "...", "tools": ["control_host_pc_via_antigravity", "get_weather"] },
    "platform": { "label": "平台类",   "brief": "...", "tools": ["bilibili_hub"] }
  },
  "unknown_tool_policy": "always"
}
```

要点：

- **分类是数据，不是代码**。新接工具只需把名字加进对应 `categories.*.tools`，代码零改动。
- `unknown_tool_policy: "always"`：配置里没登记的工具有两种结局——默认放行；若想让它被裁，改成 `"deny"`。先放行是为了不让新接的工具悄悄失效。
- `always_available`：主力 AI 永远能直接调的常驻工具，目前只有 `send_message_to_user`、`future_task`。
- 配置**按文件 mtime 热加载**，改完不用重启；也可以 `POST /api/tools/shelves/reload` 强制重读。
- 环境变量：`TOOL_SHELVES_CONFIG_PATH` 指定配置路径，`TOOL_SHELVES_DISABLED=1` 强制关闭闸门。

---

## 9. 观测

### 8.1 聚合指标

`GET /api/tools/shelves`（需登录）：

```json
{
  "enabled": true,
  "mode": "enforce_soft",
  "last_decision": { "path": "classify", "categories": ["search"], "tools_in": 9, "tools_out": 5, ... },
  "categories": [...],
  "metrics": {
    "today": { "calls": 42, "by_path": {"classify": 12, "fast_trivial": 18, "tool_result": 9, "no_tools": 3},
               "tools_in": 320, "tools_out": 118, "tools_saved": 202, "save_ratio": 0.63,
               "errors": 0, "latency_p95_ms": 1180 },
    "compression": { "results": 17, "raw_chars": 184320, "clean_chars": 26800,
                     "saved": 157520, "save_ratio": 0.854, "model_calls": 12,
                     "truncated": 3, "errors": 0 },
    "recent": [ ... 最近 300 条决策/压缩记录 ... ]
  }
}
```

落在 `data/tool_shelves/metrics.json`，按天聚合，只留最近 14 天。

### 8.2 逐轮明细（默认关闭）

`gateway.log_decisions: true` 后写 `data/tool_shelves/decisions.jsonl`（5MB × 3 滚动）：

```json
{"ts":"2026-10-30 23:41:02","path":"classify","cats":["search"],"conf":0.9,"in":9,"out":5,
 "tools_out":["you-search","you-contents","you-balance","send_message_to_user","future_task"],
 "reason":"明确要求搜索","tool_calls":["you-search"],"trace":"tr_...","expanded":false}
```

排查"这一轮为什么没给工具"就看它。

### 8.3 关键看板

```text
save_ratio      工具 tokens 节省比       目标 > 50%
errors          分类器失败数            持续 > 0 要查 DeepSeek key / 网络
by_path         快判命中率               fast_trivial + tool_result 占比越高越省钱
misfire 日志    "本轮无工具但主 AI 仍尝试调用 xxx"   ← 这类要立刻调类别描述
compression.save_ratio   工具结果压缩比       目标 > 70%
compression.model_calls  清洗走模型的次数     you-balance 之类应为 0
compression.errors       清洗失败数          持续 > 0 查 DeepSeek key / 数值校验
```

---

## 10. 运维

```powershell
# 改配置后不用重启；要强制重读：
Invoke-RestMethod -Method Post 'http://127.0.0.1:18895/api/tools/shelves/reload' `
  -Headers @{ 'X-Session-Token' = '<登录后的 session>' }

# 清空统计
Invoke-RestMethod -Method Post 'http://127.0.0.1:18895/api/tools/shelves/metrics/reset' `
  -Headers @{ 'X-Session-Token' = '<登录后的 session>' }

# 一键回滚（不改代码）
$cfg = Get-Content 'data/tool_shelves.json' -Raw | ConvertFrom-Json
$cfg.gateway.enabled = $false
$cfg | ConvertTo-Json -Depth 10 | Set-Content 'data/tool_shelves.json' -Encoding utf8
```

改代码后仍需按交接文档重启 `18895`。

---

## 11. 依赖

- 执行 AI 必须是**云端 DeepSeek**，走 `logic_slot` 的第一个候选凭证：
  - `data/models_config.json → function_credentials`（执行凭证，当前 DeepSeek `deepseek-chat`）；或
  - 环境变量 `DEEPSEEK_API_KEY`。
- 分类器与清洗器**共用同一条单次调用通道**（`classifier.complete_once`）：
  不做多凭证轮换、不做本地兜底，失败就是"全量放行 / 原文放行"（0 资源兜底）。
- 每次调用会产生正常 token 费用（分类约 150–250 tokens，清洗约 300–400 tokens），走执行凭证账本。
- `data/clients.json` 是权限与 Key 的依据；**不要删除**。

---

## 12. 已知限制 / 下一步

1. **影音类现在只有生图工具**。你举的"半夜关灯 → 放音乐"需要先接音乐/灯控工具，再把它们加进 `categories.media.tools`。
2. `you-discover`（找 agent/MCP）几乎用不到，但仍在 search 类里；确认后可以从类别里摘掉。
3. 宿主机工具已做三层拒绝（见第 5 节）；仍建议给非管理员客户端单独签发 Key，不要和 AstrBot 共用。
4. **清洗是"压缩不改写"**：`truncated` 时主力 AI 只能靠"带更窄条件重新调用"拿细节。若某类查询需要全量明细，把该工具 `mode` 改成 `code`，或把它加进 `skip_tools`。
5. **P4 已上线**：You.com 系列走中枢薄壳 MCP，其余走 AstrBot 执行器插件；`bilibili_hub` / `control_host_pc_via_antigravity` 在当前 AstrBot 里已不存在，装上后会自动进入隐藏白名单。
6. 清洗会让工具结果回灌多一次约 1~2 秒延迟；`you-balance` / `check_image_quota` / `get_weather` 已走 0 延迟快通道。

---

## 13. 测试

```powershell
cd D:\Stellaris
python -m unittest tests.test_tool_shelves -v
```

52 项，全部不联网：快判、分类解析、裁剪、粘性、兜底、字段删除、max_tools、
权限硬拒绝（管理员 / 非管理员 / 冒充 admin / 闸门关闭仍生效）、
结果压缩（ANSI / HTML / 去重 / 截断、短数值工具不过模型、URL 与数值防篡改、模型失败回退）、
端到端（mock 上游真起 server，验证宿主机工具被摘、工具结果压缩后回灌）、
薄壳清洗接口（缺参 400 / 错 Key 401 / 短数值直通 / 上游不可达回退）。

`tests/test_gateway.py` 显式设置 `TOOL_SHELVES_DISABLED=1`，保证网关透传回归不被分类器影响。
