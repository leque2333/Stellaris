# Stellaris · 交接文档

> 项目名：**Stellaris**（原“助手记忆中枢 / Agent Hub”）；当前人格卡角色：助手（数据，可随时替换）  
> 交接时间：2026-10-30（追加：工具按需加载 P1–P2 已上线）  
> 开发端：`D:\Stellaris`（唯一改代码的地方）  
> 测试端：`D:\Stellaris_test`（跑测试 + 跑 18895，不在里面改代码）  
> 生产数据库 schema：**26**  
> 当前测试：**310 项**（全部通过）

> 目录命名：2026-10-31 起两个目录都改用项目名，不再叫「机器人 / 机器人_完整版」。
> 代码一律先在 `D:\Stellaris` 改完、跑通测试，再整目录复刻到 `D:\Stellaris_test` 验证。

---

## 1. 一句话交接

这是一个只负责 **人格、档案、时间轴记忆、事实/图关系、模型路由、Token 账本** 的中枢。

平台接入、QQ / B站 / 群聊、工具执行、消息发送都在 **AstrBot**，不在这里。

```text
QQ / B站 / 群聊 / CLI / 其他客户端
              │
              ▼
           AstrBot
   （平台适配、工具、消息发送）
              │ OpenAI 兼容 /v1
              ▼
        Stellaris
  ┌──────────────────────────┐
  │ 人格 / 档案               │
  │ 时间轴 / 事实 / 图         │
  │ 模型路由 / Token 账本      │
  │ Web 控制台                 │
  └──────────────────────────┘
```

---

## 2. 当前运行状态

### 2.1 端口

| 端口 | 用途 | 状态 |
|---|---|---|
| `18895` | 中枢 Python 生产实例 | 监听中 |
| `18890` | nginx 明文 HTTP 入口，反代 `18895` | 监听中 |
| `18891` | nginx TLS 入口，反代 `18895` | 监听中 |
| `53353` | nginx TLS 入口，反代 `18895` | 监听中 |
| `6185` | AstrBot Web/API | 交接时未监听，需单独启动 |
| `6099` | NapCat Web/API | 交接时未监听，需单独启动 |

nginx：

```text
D:\nginx-1.31.3\nginx.exe
PID: 28404（交接时）
```

nginx 配置：

```text
D:\nginx-1.31.3\conf\nginx.conf
```

关键反代：

```text
18890 -> 127.0.0.1:18895
18891 -> 127.0.0.1:18895
53353 -> 127.0.0.1:18895
```

### 2.2 中枢进程

```text
Python: C:\Users\user\AppData\Local\Python\bin\python.exe
Python 版本: 3.14.5
启动命令: python console\server.py 18895
工作目录: D:\Stellaris_test
PID: 23400（交接时，重启后会变化）
```

日志：

```text
D:\Stellaris_test\logs\hub-18895.out.log
D:\Stellaris_test\logs\hub-18895.err.log
```

### 2.3 重要提醒

- **不要**在 `18895` 再启动第二个中枢；nginx 已经反代到这里。
- `18890` 不是另一个实例，只是 nginx 兼容入口。
- AstrBot / NapCat 当前没有监听，需要时单独启动；中枢本身不负责 QQ/B站协议。
- `D:\Stellaris` 是开发端（只在这里改代码），`D:\Stellaris_test` 是测试端（跑测试、也跑着 18895）；不要把开发数据库直接覆盖测试端数据库。

---

## 3. 目录说明

```text
D:\Stellaris_test
├─ agent_core\                 # 记忆、人格、档案、模型、存储
│  ├─ memory\                  # 时间轴/事实/图/embedding
│  ├─ profile\                 # 档案、身份绑定
│  ├─ storage\                 # SQLite schema / store
│  ├─ compute_providers.py     # 模型路由
│  ├─ persona.py               # 人格上下文
│  ├─ identity.py              # 身份解析
│  └─ access_policy.py         # 访客策略
├─ console\
│  ├─ server.py                # HTTP、Web、OpenAI /v1 网关
│  ├─ backend.py               # 控制台后端聚合
│  ├─ index.html               # Web 控制台
│  ├─ modules\                 # model/system/memory 服务
│  └─ static\                  # JS / CSS
├─ data\
│  ├─ memory.db                # 主库：档案/时间轴/事实/账本
│  ├─ memory_graph.db          # 独立图库
│  ├─ models_config.json       # 凭证/模型/价格/额度
│  ├─ compute_providers.json   # 槽位与 provider
│  ├─ auth_config.json         # 控制台账号/密码哈希
│  ├─ upstream_models_cache.json
│  └─ persona.md          # 人格文件
├─ docs\                       # 架构/接入/记忆层文档
├─ tests\                      # 310 个测试
├─ backups\                    # 迁移/清理备份
└─ logs\
```

人格与项目解耦：

```text
data/persona.md 是唯一人格卡；保存人设时写入这里
角色名从人设卡解析：# 人设：XXX / # 【XXX：...】 / YAML name: XXX
环境变量 BOT_NAME 可强制覆盖角色名
旧 data/anan_persona.md 仅在 persona.md 缺失时自动迁移/回退
项目代码不硬编码具体角色名；替换 persona.md 即换角色
agent_core/persona_template.md 只是通用兜底模板，不含具体角色
```

人格设定页（`人格设定 → 基底设定`）：

```text
AI 名称       小输入框
人格设定      大文本域
对话范例      大文本域
其他          大文本域
保存后写入 data/persona.md：
  # 人设：<名称>
  ## 人格设定
  ## 对话范例
  ## 其他

好感度不写在人格文本里；统一在「人格设定 → 好感度」配置：
  阶段名称 / min_score / max_score / attitude_hint
  80–100 阶段的 attitude_hint 承接原“厌世度几乎完全消失”的条件行为
  态度指引用大文本框展示，避免长文本被截断
```

---

## 4. 启动与重启

### 4.1 启动生产中枢

```powershell
$py = 'C:\Users\user\AppData\Local\Python\bin\python.exe'

Start-Process -FilePath $py `
  -ArgumentList 'console\server.py','18895' `
  -WorkingDirectory 'D:\Stellaris_test' `
  -WindowStyle Hidden `
  -RedirectStandardOutput 'D:\Stellaris_test\logs\hub-18895.out.log' `
  -RedirectStandardError  'D:\Stellaris_test\logs\hub-18895.err.log'
```

验证：

```powershell
Get-NetTCPConnection -LocalPort 18895 -State Listen
```

打开：

```text
http://127.0.0.1:18895
https://leque233.xyz:53353
```

### 4.2 重启中枢

```powershell
$proc = Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*server.py 18895*' } |
  Select-Object -First 1

if ($proc) {
  Stop-Process -Id $proc.ProcessId -Force
  Start-Sleep -Seconds 2
}
```

然后执行 4.1 的启动命令。

### 4.3 重载 nginx

```powershell
cd 'D:\nginx-1.31.3'
.\nginx.exe -t
.\nginx.exe -s reload
```

### 4.4 测试

开发端：

```powershell
cd 'D:\Stellaris'
python -m unittest discover -s tests -t .
```

测试端：

```powershell
cd 'D:\Stellaris_test'
python -m unittest discover -s tests -t .
```

交接时结果：

```text
Ran 310 tests
OK
```

---

## 5. 核心配置

### 5.1 `data/models_config.json`

主要字段：

```text
active_model
master_credentials[]
function_credentials[]
credits
preset_templates[]
models[]
persona_slot / logic_slot / fact_slot（若有）
```

主控凭证字段：

```text
name, provider, model, auth_type, api_key, base_url
currency, input_price, cache_price, output_price
is_active
```

功能凭证字段相同，另有：

```text
allocated_quota / balance
```

当前生产状态：

```text
active_model = gemini-3.8-flash
master_credentials:
  Gemini 主控凭证
  Google / gemini-3.8-flash
  USD
  input 0.75 / cache 0.075 / output 3.75
function_credentials:
  执行 AI（DeepSeek）
  DeepSeek / deepseek-chat
  currency 未显式设置（按 DeepSeek 识别为 CNY）
  input 0.2574 / cache 0 / output 1.0287
credits:
  total_quota 50 / currency USD
```

> `api_key` 是敏感信息，不在本文档中抄录。交接时请直接查看配置文件或控制台，不要把密钥发到聊天/文档。

### 5.2 `data/compute_providers.json`

当前槽位：

```text
persona_slot = google_gemini_official
logic_slot   = deepseek_official
```

说明：

- `persona_slot`：前台人格、主对话。
- `logic_slot`：复杂逻辑任务。
- `fact_slot`：事实抽取；若未配置，代码会按实现回退。
- provider 中包含真实 base URL / model / key；本文件同样是敏感文件。

### 5.3 `data/auth_config.json`

控制台管理员账号和密码哈希。

- 不要手改密码哈希。
- 通过 UI 修改密码。
- 不要提交到 Git。

### 5.4 访客策略

文件：

```text
data/access_policy.json
```

当前生产没有该文件，表示使用默认值：

```json
{
  "guest_enabled": false,
  "guest_timeline_enabled": false,
  "guest_default_affection": 50
}
```

默认行为：

- 非白名单、访客关闭 → `guest_disabled`，拒绝。
- 白名单身份 → 只使用自己的档案。
- 访客开启 → 临时 `guest_<platform>_<uid>`，不写事实/图。
- 访客时间轴默认关闭。
- 不要为每个普通用户自动建档。

---

## 6. 数据与数据库

### 6.1 主库

```text
data/memory.db
```

schema 版本：**24**

核心表：

```text
agent_timeline        # 时间轴事件
memory_facts          # 双时间事实
user_facts            # 用户事实
archives              # 档案
archive_identities    # 平台身份绑定
archive_aliases       # 旧档案 ID -> 新档案 ID
token_usage           # Token 账本（主表）
token_usage_ledger    # 兼容视图
```

`schema_migrations` 记录迁移。

### 6.2 v24 账本字段（v26 补 tools 列）

`token_usage` 新增：

```text
credential_type TEXT   # master / function / recharge / ''
currency TEXT          # USD / CNY
tools TEXT             # v26：本轮实际调用的工具名，逗号分隔（账本「来源」列）
```

账本分组：

```text
credential_type + currency
```

因此可以分开：

```text
主控凭证 · USD
主控凭证 · CNY
执行凭证 · USD
执行凭证 · CNY
```

旧流水会在 `/api/ledger` 调用时自动回填归属；无法识别的归为“未归类”。

### 6.3 图库

```text
data/memory_graph.db
```

独立 SQLite 图文件，不是外部图数据库服务。

### 6.4 当前生产档案

```text
archive_id: arc_master
display_name: 主人
role: admin

identities:
  qq / <你的QQ>
  bilibili / <B站UID>

archive_aliases:
  <你的QQ> -> arc_master
```

原则：

- 档案 ID 不再等于 QQ 号。
- 只保留一个管理员档案。
- 新建档案角色只有 `admin` / `guest`。
- 不为每个普通用户自动建档。

> 开发目录的 `data/memory.db` 是测试/空数据，不要用它判断生产档案。

---

## 7. API 入口

### 7.0 工具按需加载（Tool Shelves）

`/v1` 网关会把 AstrBot 送来的全量工具按大类裁剪后再交给主力 AI，
工具返回的大体积结果会先压缩再回灌。
配置 `data/tool_shelves.json` + `data/clients.json`（均热加载），
代码 `agent_core/tool_shelves/`，详见 [TOOL_SHELVES.md](TOOL_SHELVES.md)。

```text
GET  /api/tools/shelves                # 配置视图 + last_decision + 今日节省比 + 压缩指标
POST /api/tools/shelves/reload         # 强制重读配置
POST /api/tools/shelves/metrics/reset  # 清空统计
```

四句话记住行为：

```text
1. 语气词 / 工具结果回灌 / 无 tools 请求 → 不调分类模型
2. 其余 → DeepSeek 选大类（只看时间+最近三句+类别简介），只给选中类别的工具
3. 非管理员客户端 → 宿主机工具强制拒绝（纯代码兜底，闸门关了也生效）
4. 大体积工具结果 → 代码粗筛 + DeepSeek 清洗；数字类短工具绝不过模型
```

**P4 薄壳（已上线）**：

```text
You.com 系列  → hub-tools 薄 MCP（容器内），只暴露 you_search_clean / you_contents_clean
其他工具      → astrbot_plugin_hub_tool_executor：call_raw_tool 按白名单执行
原始工具      → 被 on_astrbot_loaded 停用，模型工具表里看不见，执行器仍调得到
结果清洗      → 统一 POST /api/tools/clean
```

**安全要点**：`data/clients.json` 存在时 `/v1` 才校验 Key。删掉它 = 关闭 Key 校验。
宿主机工具 `control_host_pc_via_antigravity` 只对 `protection.admin_clients`（当前 `astrbot`）开放。

回滚：

```text
只关裁剪     data/tool_shelves.json → gateway.enabled = false
只关压缩     data/tool_shelves.json → compression.enabled = false
只回滚 P4    从 cmd_config.json 的 plugin_set 移除 astrbot_plugin_hub_tool_executor，
             并从 mcp_server.json 移除 hub-tools，重启 AstrBot
全部回滚     以上都关（权限 deny 仍然生效，这是故意的）
```

P4 同步用脚本，别手拷：

```powershell
python scripts\deploy_p4.py --restart
```

### 7.0.2 身份线索扫描

```text
GET  /api/identity/scan      # 罗列所有插件里的 UID（只读）
python scripts\scan_identities.py --db <生产库>
```

只罗列、不判断：不猜群/人/主人，不自动建档。人看完列表后自己去
「控制台 → 档案 → 添加身份」配置。详见 [IDENTITY_SCAN.md](IDENTITY_SCAN.md)。

### 7.0.1 一键连接 AstrBot

用户已装 AstrBot 时，不再手改 `cmd_config.json`：

```powershell
python scripts\connect_astrbot.py            # 自动找目录 + 写 provider + 备份
python scripts\connect_astrbot.py --p4 --restart   # 连薄壳一起装并重启
```

详见 [CONNECT_ASTRBOT.md](CONNECT_ASTRBOT.md)。改的是 `provider_sources` / `provider` /
`agent_runner.config.model.provider_id`，幂等、有备份、可回滚。

### 7.1 OpenAI 兼容

```text
GET  /v1/models
POST /v1/chat/completions
```

AstrBot 通过：

```text
http://192.168.3.4:18895/v1
```

### 7.2 控制台/账本

```text
GET  /api/tokens              # 首页轻量统计 + combined / accounting_groups / daily_activity / credits.balances / cost_breakdown
GET  /api/ledger              # 账本明细 + combined / accounting_groups / daily_activity / cost_breakdown
POST /api/ledger/clear        # 清空 token_usage + 归零额度池/凭证余额（管理员）
GET  /api/models_config
POST /api/models_config
GET  /api/access_policy
POST /api/access_policy
POST /api/credits             # 主余额充值
```

鉴权：

```text
X-Session-Token: <session>
```

`/api/ledger/clear` 行为：

```sql
DELETE FROM token_usage
```

同时归零 `data/models_config.json`：

```text
credits.total_quota = 0
credits.balance = 0
credits.used_quota = 0
所有 master/function 凭证的 balance = 0
存在 allocated_quota 的凭证也归零
```

会清空全部流水、费用和总金额；不可恢复。新初始化（没有配置文件）默认金额也是 0。

### 7.3 档案/记忆

```text
/api/archives
/api/timeline
/api/memory/*
```

具体路由以 `console/server.py` 为准。

---

## 8. Web 控制台入口

侧边栏：

```text
仪表盘
记忆库时间轴
人格设定
档案
账单流水
AstrBot 控制台
设置
```

### 8.1 仪表盘

当前 4 项：

```text
当前模型
今日调用
今日 Token 吞吐
今日费用
```

余额：

```text
主控凭证币种 == 执行凭证币种 -> 一个余额
主控凭证币种 != 执行凭证币种 -> 主余额 + 副余额
副余额可单独充值
```

Hero 区域为放大卡片（`balance-hero-strip hero-card-v2`）：

```text
max-width: 1180px / min-height: 360px
padding: 38px 44px / radius: 24px
顶部：TOTAL AVAILABLE + 主控单位 badge（如“主控 USD”）
      主余额 = 所有同单位金额之和（额度池 + 各凭证 balance/allocated_quota - 该单位消耗）
      副余额最多 3 个，每个也是同单位金额之和；字号为主余额的 75%
      主金额与副金额同一行，副金额放在主金额右侧；3 个副金额时在右侧区域自动换行
      主控币种永远排第一；副金额点击弹窗标题为“添加金额”
      副余额显示条件：任一主控/执行凭证存在该币种（不管有没有消耗）
下方：四项指标一排（当前模型 / 今日调用 / 今日 Token 吞吐 / 今日费用）
      无玻璃内卡、无背景容器，直接排在渐变卡片上
      四项数值与模型名同字号；第一列 1.45fr 加宽，模型名保持单行
<=1024px：指标 2 列；<=760px：紧凑卡片；<=520px：余额上下堆叠；<=480px：指标 1 列
```

注意：

- `overview.js` 切换主题时必须保留 `hero-card-v2` 类，否则会退回旧长条布局。
- `fetchTokenStats` 拉到真实余额后会重置 `__balanceRendered`，保证主/副金额每次首屏都有数字滚动效果。
- 主/副金额来自 `credits.balances`，不是前端自己按凭证猜的；`primary_currency` 来自当前启用的主控凭证。

主题色：

```text
Gemini     保留原来的动态彩色流光
OpenAI     #10A37F 静态纯色填充
Claude     #D97757 静态纯色填充
DeepSeek   #1D63FF 静态纯色填充
```

OpenAI / Claude / DeepSeek 三种主题不再使用 `::before` / `::after` 渐变和动画，只保留纯色背景与主题色阴影；Gemini 不动。

Hero 下方是当前月热力图（无标题）：

```text
7 列日历格（周一至周日）
综合分 = Tokens + 调用次数 × 5000
按当月最大综合分分 0~4 档
接口字段：/api/tokens -> daily_activity
```

### 8.2 凭证编辑弹窗

- 左上角只有一个 `✕`。
- 右上角：
  - 启用凭证：`保存` / `停用`
  - 停用凭证：`启用` / `删除`
- 停用凭证所有字段只读。
- 删除只允许停用凭证。
- 至少保留一个启用凭证。
- 第一个启用的凭证是主控；停用后下一个启用凭证自动成为主控。
- 停用不会保存未提交的表单修改。
- 价格单位只标记 USD / CNY，不做汇率换算。

### 8.3 账本页

顶部“合计”只保留 Token 与调用次数：

```text
今日调用总笔数
今日消耗 Tokens
累计调用总笔数
累计消耗 Tokens
```

下方“金额核算”按主控 / 执行分两行，每行一个小标题 + 4 列小白卡：

```text
主控凭证
  消耗总金额(本组每个币种各一张)
  今日消耗金额(本组每个币种各一张)
  今日消耗 Tokens / 今日调用次数

执行凭证
  同上；主币种为执行凭证自己的币种（当前 DeepSeek 为 CNY）
```

排列规则：

- 容器固定 4 列，满 4 张自动换行，不会把 6 张挤成一排；
- 所有币种的“消耗总金额”先列，再列所有币种的“今日消耗金额”，最后是 Tokens / 调用次数；
- 卡片高度、宽度、圆角、内边距、字号与顶部 4 张指标卡一致。

第二币种规则（按组判断，不是全局判断）：

- 只看本组凭证：主控组只看 `master_credentials`，执行组只看 `function_credentials`；
- 本组存在第二币种凭证时，该币种即使没有消耗也显示 0；
- 本组完全没有第二币种凭证时，第二币种金额卡自动隐藏；
- 另一组有别的币种，不会给本组补第二币种；
- 第三、第四币种同样各自成卡，按 4 列自动换行；
- 金额不跨币种相加。

明细表新增“凭证归属”列，显示主控 / 执行 / 充值以及 USD / CNY。

后端 `/api/tokens`、`/api/ledger` 同步返回：

```text
combined          # 只有 today/overall 的 calls 与 tokens
accounting_groups # master / function 分组；组内 costs[] 按币种，
                  # 缺失但存在凭证的币种补 0，并带 has_secondary_currency
credits.today_cost_by_currency
credits.overall_cost_by_currency
```

`cost_breakdown` 保留作兼容字段。

### 8.4 账本设置

设置 → 常规与系统 → 账本设置：

```text
清空账本
```

有二次确认。清空会同时：

```text
删除 token_usage 全部流水
额度池 total_quota / balance / used_quota = 0
所有主控/执行凭证 balance 归零
存在 allocated_quota 的凭证也归零
前端配额/余额/弹窗金额立即显示 0
```

新初始化默认金额为 0，不再是 50。

---

## 9. 当前生产快照

交接时 `/api/tokens`：

```text
余额: $49.53
总配额: $50.00
今日调用: 54
今日输入: 373,449
今日输出: 1,763
今日 Tokens: 375,212
今日费用: $0.2977
累计调用: 135
累计 Tokens: 605,854
累计费用: $0.4749
```

账本两行四卡（当前每组单一币种）：

```text
合计（只统计 Token 与调用次数）
  今日 54 次 / 375,212 Tokens
  累计 135 次 / 605,854 Tokens

主控凭证
  消耗总金额 USD $0.4749
  今日消耗金额 USD $0.2977
  今日消耗 Tokens 375,212 / 今日调用次数 54

执行凭证
  消耗总金额 CNY ¥0.0000
  今日消耗金额 CNY ¥0.0000
  今日消耗 Tokens 0 / 今日调用次数 0
```

当前主控组只有 USD 凭证、执行组只有 CNY 凭证，所以两组都只显示本组币种，
第二币种卡自动隐藏；执行凭证暂无调用，所以执行行全是 0。

---

## 10. 最近完成的关键改动

1. 档案 ID 从 QQ 号迁移为 `arc_master`，并保留旧 ID 别名。
2. 访客默认关闭；新增 `data/access_policy.json` 策略。
3. 主控/功能凭证池、价格覆盖、模型路由。
4. Gemini 3.8 价格纠正为官方：

```text
input 0.75 / cache 0.075 / output 3.75
```

5. 首页从“累计调用/累计 Token”改为“当前模型/今日调用/今日 Token/今日费用”。
6. 凭证弹窗去掉滑动动画、重复叉、外部保存；改为右上角操作。
7. iPad / 窄屏弹窗适配。
8. 价格单位 USD / CNY 选择，不换算。
9. 账本 schema v24，按 `credential_type + currency` 分桶；v26 再补 `tools` 列（通道 + 工具归因）。
10. 账本页增加分组卡片。
11. 设置增加“清空账本”。
12. 首页容器加宽、详情换行、iPad 两列布局。
13. 账本页口径重构：合计只保留 Token 与调用次数；金额 / Token / 调用按主控、执行分成两行；每行金额再按本组凭证单位分别核算，不跨币种相加。本组存在第二币种凭证但无消耗时显示 0，本组没有第二币种凭证时隐藏；另一组有别的币种不会补进来。容器固定 4 列，第三/第四币种自动换行，不挤成一排。后端新增 `combined` / `accounting_groups`，明细流水返回 `credential_type` / `currency`。
14. 仪表盘 Hero 放大为卡片比例：顶部主控单位 badge + 大余额 + 最多 3 个副余额（主余额 75% 字号），主/副金额都按“同单位所有金额”汇总；主控币种永远第一，副金额弹窗标题为“添加金额”。下方四项指标一排、无玻璃内卡；`overview.js` 主题切换保留 `hero-card-v2`，并恢复余额数字滚动。Hero 下方新增无标题的当前月热力图：综合分 = Tokens + 调用次数 × 5000，按当月最大值分 4 档。
15. 清空账本改为“流水 + 金额”一起归零：删除 token_usage，额度池 total_quota/balance/used_quota 置 0，所有主控/执行凭证的 balance/allocated_quota 置 0；初始化默认金额也改为 0。
16. 非 Gemini 主题改为静态纯色：OpenAI `#10A37F`、Claude `#D97757`、DeepSeek `#1D63FF`，关闭渐变伪元素和流光动画；Gemini 动态主题保持不变。
17. 项目品牌更名为 **Stellaris**：页面标题、侧边栏、登录页、控制台/启动脚本、文档标题、拓扑节点名同步更新；`anan_*` 技术标识（Cookie、数据库名、`AnanAgent` 类名、`anan_hub_core` 配置 ID）保留，避免破坏现有数据与登录会话。
18. 人格与项目解耦：`data/persona.md` 成为唯一人格卡，角色名从人设卡动态解析（`agent_core/persona_identity.py`），运行时代码不再硬编码“助手/阿南”；旧 `data/anan_persona.md` 会在缺失时自动迁移/回退，`agent_core/persona_template.md` 改为通用兜底模板。
19. 人格设定拆成四段：AI 名称（小输入框）+ 人格设定 / 对话范例 / 其他（三个大文本域），保存写入 `data/persona.md` 的 `# 人设` + `## 人格设定` / `## 对话范例` / `## 其他` 结构；好感度的阶段、区间和态度指引统一放到「好感度」子页，不再写进人格文本。
20. 余额允许为负数：账本扣费不再 `max(0)` 截断；负数按 `-$12.34` / `-¥5.00` 显示；负的次级币种也保留展示；充值后从负值继续累加回正。

---

## 11. 已知限制与风险

### 11.1 多币种不是严格会计

当前价格单位和账本分组是“分桶展示”，但**不做汇率换算**。

风险：

```text
USD 价格 + CNY 价格
```

如果直接相加，会得到没有意义的总数。现在账本页按主控 / 执行分组，组内再按 `currency` 分列，不跨币种相加：

- `credits.balance` 只扣“额度币种”的消费，不再把 CNY 当 USD 扣；
- 首页大余额仍以主控币种为主；
- 执行凭证余额仍由凭证自身的 `balance / allocated_quota` 管理；
- 尚未实现汇率换算和跨币种总资产折算，这是有意的。

### 11.2 同一模型多凭证的归属歧义

新流水通过模型绑定推断 `master/function + currency`。

如果同一个模型同时出现在多条主控/执行凭证里，且币种不同，当前只能按配置中第一条匹配，可能归错。

彻底解决需要：

```text
token_usage 记录 credential_id
```

而不仅是 model。

### 11.3 清空账本不可恢复

`/api/ledger/clear` 直接 `DELETE`，没有单独的“清空前自动备份”。

建议清空前手动备份：

```powershell
Copy-Item 'D:\Stellaris_test\data\memory.db' `
  'D:\Stellaris_test\backups\memory_before_clear_$(Get-Date -Format yyyyMMdd_HHmmss).db'
```

### 11.4 停用凭证的语义

- 停用 = 只改状态，不保存未提交字段。
- 启用后可以继续编辑。
- 停用凭证不可修改。
- 最后一个启用凭证不能停用。
- 主控自动顺延到下一个启用凭证。

### 11.5 价格 0 = 免费

价格输入为空或 0 时，账本会按 0 计算，不会自动套用官方价。

新凭证不会自动带价，必须手动填。

### 11.6 AstrBot 知识库/embedding

AstrBot 的 embedding/知识库当前是关闭状态。

不要在没有明确需求时重新开启：

```text
gemini_embedding_inst.enable=false
```

### 11.7 开发/生产数据分离

开发库可能是测试数据；生产库才是真实档案。

不要：

```text
Copy-Item D:\Stellaris\data\memory.db D:\Stellaris_test\data\memory.db
```

### 11.8 遗留兼容层

以下模块仍可能有兼容代码，但主流程已不依赖：

```text
agent_core/bridge/
agent_core/execution/
agent_core/mcp/
agent_core/source/
agent_core/plugins/
agent_core/connectors/
agent_core/adapters/
agent_core/connection.py
agent_core/topology.py
agent_core/knowledge.py
```

删除前先确认 CLI/旧客户端不使用。

### 11.9 tool_shelves 的依赖与既有测试失败

**依赖**：执行 AI 分类器走 `logic_slot` 的第一个候选凭证（当前 DeepSeek）。
开发端 `data/models_config.json` 没有 `function_credentials`，分类器会因缺 Key 报
`request_failed:HTTPError` 并全量放行——这是预期的降级，不是 bug。
测试端有执行凭证，闸门正常工作。

### 11.9.1 P4 薄壳的坑（务必看）

1. **插件 `on_astrbot_loaded` 早于 MCP 连接**：`you-search` 工具在钩子执行时还没注册。
   现在的做法是"延迟重试 30 秒 + 日志提示"，日志出现
   `已从模型工具表隐藏: you-search` 才算成功。
2. **插件配置不持久化**：`metadata.yaml` 的 `config:` 字段依赖 AstrBot 版本，可能不生效。
   已改成"代码内置默认白名单 + 配置只作覆盖"，不依赖配置加载。
3. **`initialize()` 与 `on_astrbot_loaded()` 的区别**：前者在插件加载时触发、MCP 尚未连接；
   必须用后者（`@filter.on_astrbot_loaded()`）。
4. **`astrbot_plugin_hub_tool_executor` 的 `call_raw_tool` 只对管理员开放**，
   非管理员调用会直接返回错误——这是有意的第二道闸。

### 11.10 部署踩过的坑（务必看）

1. **PowerShell `Copy-Item -Recurse` 到已存在目录曾把整个包拷空**，导致中枢 import 失败。
   现在 `console/server.py` 对 `agent_core.tool_shelves` 的 import 做了 try/except：
   包缺失时打印告警并降级为"全量透传"，中枢仍能启动。部署后务必核对两件事：

```powershell
# 1) 包内文件数（应为 10）
(Get-ChildItem 'D:\Stellaris_test\agent_core\tool_shelves' -File).Count
# 2) 开发/生产关键文件 hash 一致
Get-FileHash 'D:\Stellaris\console\server.py','D:\Stellaris_test\console\server.py'
```

2. **同步必须单向 dev → prod**。曾误用反向覆盖，把生产旧版 `server.py` 冲回开发目录。
   建议每次同步前先建 `backups\tool_shelves_*` 快照，同步后立刻比对 hash。
3. **配置由 PowerShell 写出会带 BOM**。`ShelfConfig` 用 `utf-8-sig` 读取，但其他读 JSON
   的模块不一定兼容；手改配置建议用编辑器另存为 UTF-8 无 BOM。

### 11.15 架构边界：中枢不挂载功能，工具全归 AstrBot

**定死的分工**：

```text
中枢（18895）   大脑：人格、记忆、事实、时间轴、模型路由、账本、工具分类与裁剪
AstrBot         手：平台接入（QQ/B站）、插件、MCP、真正的工具执行
```

中枢**不再有**功能模块。`modules/bilibili` 已归档到
`archive/modules_bilibili_20261031/`，中枢没有任何地方会加载它
（本来就只在手动 `python -m modules.bilibili.main run` 时才会跑）。

**工具来源全部是 AstrBot**，工具页会标注：

```text
future_task                      AstrBot 内置      [直接使用]
generate_image                   image-generator   [筛选 · 影音类]
you-search                       you-search        [筛选 · 搜索类]
you_search_clean                 hub-tools         [筛选 · 搜索类]
call_raw_tool                    hub_tool_executor [筛选 · 搜索类]
control_host_pc_via_antigravity  MCP（executor 隐藏）[筛选 · 服务器类]
```

**清掉的幽灵工具**（配置里登记、实际没有任何实现）：

```text
get_weather     ← 全项目找不到实现
bilibili_hub    ← 只在 executor 白名单里，没有注册
```

**B 站后续**：能力迁到 AstrBot 的 `astrbot_plugin_bilibili`，
但那个插件现在只有壳（`main.py` 用 `python -m modules.bilibili.main run`
启动子进程），需要把模块代码放进插件目录 + 补 `metadata.yaml` + 改
`hub.base_url` 为容器可访问的宿主地址，才能启用。

### 11.14 工具页（分类 + 提权）

侧边栏「工具」，或直接访问 `/#tools`。

```text
14 个工具 · 直接 2 · 筛选 12

future_task                    [直接使用]
send_message_to_user           [直接使用]
generate_image                 [筛选 · 影音类]
control_host_pc_via_antigravity [筛选 · 服务器类] [需提权]
...
```

点任意一行打开弹窗，两个开关：

| 开关 | 值 | 落点 |
|---|---|---|
| 调用方式 | 主模型直接使用 | `always_available`（每轮都给） |
| 调用方式 | 由执行模型筛选 | `categories[类目].tools`（分类器判类才给） |
| 权限 | 自动调用需要向管理员提权 | `approval_tools`（非管理员拿不到） |

**提权的运行时效果**：`gate.py` 里对非 admin 客户端直接拒绝：

```python
if not is_admin:
    denied |= self._tools_in(tools, self.cfg.approval_tools())
```

**工具从哪来**：配置里登记的（`tool_shelves.json`）+ 网关见过的
（`data/tool_registry.json`，由 `gate.decide` 被动记录）。没见过的新工具
会以「未归类」出现，方便补归类。

**API**：

```text
GET  /api/tools          → {tools, categories, always_available, approval_tools}
POST /api/tools          {name, mode: direct|filtered, approval: bool, category}
```

**已废弃**：`/api/tool-permissions` 的实现依赖 `agent_core/tool_permissions.py`
和 `capabilities.py`，这两个模块早已不存在，调用会 ImportError。已改成
返回 410 + 指向 `/api/tools`。

### 11.13 运行日志与控制台排查

**启动方式**：`scripts\start_hub.ps1`（日志轮转，重启不丢历史）

```text
hub-18895.out.log         当前 stdout（启动信息 + 访问日志）
hub-18895.out.log.prev    上一轮
hub-18895.err.log         当前 stderr（只留真正的异常）
hub-18895.err.log.prev    上一轮
```

**控制台**：

```text
设置 → 运行日志   （也可直接访问 /#logs）
├─ 只看错误 / 含上一轮 两个开关
├─ 行数选择 200/500/1500
└─ 深色等宽日志区

项目名右边是消息图标（铃铛），点开是消息弹窗，只显示**本次启动以后**的消息：

```text
● 00:21:00  日志发现 1 条错误：...
● 00:20:49  中枢已启动 · 端口 18895
 ↑ 紫=普通 黄=警告 红=错误
```

消息队列在进程内存里（`agent_core/messages.py`），重启即清空，
不会把历史噪音带给用户。当前来源：

| 级别 | 来源 |
|---|---|
| info | 启动 |
| warn | 模型凭证调用失败/切备用 |
| error | HTTP 5xx、日志里检测到错误 |
```

**API**：

```text
GET /api/logs?tail=500&filter=error&include_prev=1
GET /api/logs/alerts        → {count, last}
GET /api/messages           → {total, counts, messages}（本次启动后）
```

**踩过的坑**：`BaseHTTPRequestHandler.log_message` 默认写 stderr，导致
访问日志混进 `err.log`，徽章把 404/500 当错误误报。已改成访问日志走
stdout、`err.log` 只留异常；同时过滤 `/api/tokens`、`/api/overview` 等
高频轮询，避免刷屏。

### 11.11 已修复：test_ledger_migrated

原测试依赖夹具库里恰好有「充值」流水行，生产库清过账本后必然失败。
已改成**自己造一个旧 schema 的临时库**测迁移逻辑本身，并补了幂等用例：

- `test_ledger_migrated`：旧 `token_usage_ledger` 表 → 迁移进 `token_usage` + 替换成视图
- `test_ledger_migration_idempotent`：重复迁移不会重复插入

### 11.12 空凭证挤掉有效凭证 → 全线 401（务必看）

**现象**：`logic_slot` 调用一直 `HTTP Error 401: Authorization Required`，
工具分类器、结果压缩、好感度评估全部静默走兜底。

**根因**：`compute_providers.json` 的 `deepseek_official.api_key` 是空字符串，
但 `_candidate_providers()` 无条件把它 append 进候选列表：

```python
candidate_providers.append(p_def)   # 空 key 也塞进去
```

轮换时有效凭证失败一次就落到这个空 key 上，直接 401，后面的备用也没机会。

**修复**：空 key 的 provider 跳过（`ollama` / `ide_bridge` 除外，它们本来就不带 key）：

```python
if p_def.get("api_key") or p_def.get("auth_type") in ("ollama", "ide_bridge"):
    candidate_providers.append(p_def)
```

**验证**：`logic_slot` 候选从 2 个降到 1 个（`执行 AI（DeepSeek）`），
分类器返回 `OK`，好感度评估正常。

### 12.1 修改代码后

```text
开发目录改 -> 测试 -> 同步生产 -> 重启生产 -> 验证端口/API -> 浏览器硬刷新
```

同步示例：

```powershell
$files = @(
  'console\server.py',
  'console\index.html',
  'console\static\js\models.js',
  'console\static\js\overview.js',
  'console\static\js\ledger.js',
  'console\static\css\overview.css',
  'console\static\css\ledger.css',
  'console\modules\model_service.py',
  'console\modules\system_service.py'
)

foreach ($f in $files) {
  Copy-Item "D:\Stellaris\$f" "D:\Stellaris_test\$f" -Force
}
```

### 12.2 迁移前备份

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
Copy-Item 'D:\Stellaris_test\data\memory.db' `
  "D:\Stellaris_test\backups\memory_before_migration_$stamp.db"
Copy-Item 'D:\Stellaris_test\data\memory_graph.db' `
  "D:\Stellaris_test\backups\graph_before_migration_$stamp.db"
```

迁移会在服务启动时自动执行；`apply_migrations` 也会尝试自动备份。

### 12.3 验证账本

```powershell
$token = '<登录后的 session token>'
$headers = @{ 'X-Session-Token' = $token }

Invoke-RestMethod 'http://127.0.0.1:18895/api/tokens' -Headers $headers |
  ConvertTo-Json -Depth 4
```

重点看：

```text
credits.balance
today.calls
today.total_tokens
today_cost
cost_breakdown
```

### 12.4 502 / 页面打不开

检查：

```powershell
Get-NetTCPConnection -LocalPort 18895 -State Listen
Get-Content 'D:\Stellaris_test\logs\hub-18895.err.log' -Tail 50
```

然后：

```text
重启 18895
nginx -t && nginx -s reload
```

### 12.5 首页数字为 0

可能原因：

```text
1. 没有当天调用
2. 数据库路径不对（dev/prod 混用）
3. /api/tokens 超时
4. AstrBot 没有指向 18895
```

先检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:18895/api/tokens' -Headers $headers
```

### 12.6 余额不对

检查：

```text
data/models_config.json -> credits.total_quota
data/models_config.json -> credits.balance
token_usage 累计 cost_usd
```

注意：余额计算使用 `total_quota - 累计费用`，不是直接使用 `credits.balance`。

### 12.7 凭证价格不生效

优先级：

```text
凭证手动价格 > models 列表 > preset_templates
```

检查该凭证的：

```text
input_price / cache_price / output_price / currency
```

以及流水里的 `model` 是否匹配。

---

## 13. 接手后的建议顺序

1. 先确认 `18895`、nginx、AstrBot、NapCat 各自状态。
2. 用浏览器登录，确认首页、凭证、账本三个页面正常。
3. 跑一次 `310` 测试。
4. 备份 `memory.db` / `memory_graph.db` / `models_config.json` / `compute_providers.json`。
5. 确认 AstrBot 的 provider 指向 `http://192.168.3.4:18895/v1`，模型为 `gemini-3.8-flash`。
6. 确认 AstrBot embedding/知识库仍关闭。
7. 如果要继续做多币种，优先补 `credential_id` 到账本，而不是继续在展示层修。
8. 清理遗留兼容层前，先确认 CLI 是否还要用。

---

## 14. 关键文件清单

| 文件 | 作用 |
|---|---|
| `console/server.py` | HTTP / Web / `/v1` 网关 |
| `console/index.html` | 控制台页面 |
| `console/modules/model_service.py` | 凭证/定价/账本聚合 |
| `console/modules/system_service.py` | 账本写入/归属推断/回填 |
| `agent_core/storage/schema.py` | 数据库迁移（当前 v26） |
| `agent_core/storage/sqlite_store.py` | SQLite 读写 |
| `agent_core/memory/__init__.py` | 记忆门面 |
| `agent_core/profile/archives.py` | 档案/身份 |
| `agent_core/access_policy.py` | 访客策略 |
| `agent_core/tool_shelves/` | 工具按需加载（闸门/分类器/清洗器/会话缓存/指标） |
| `console/mcp_hub_tools.py` | P4 薄壳 MCP 服务（容器内运行，回灌中枢清洗） |
| `astrbot_plugins/astrbot_plugin_hub_tool_executor/` | P4 执行器插件（薄工具 + 隐藏原始工具） |
| `scripts/deploy_p4.py` | P4 一键同步/校验/重启 |
| `scripts/connect_astrbot.py` | AstrBot 反向连接器（自动写 provider 配置） |
| `agent_core/identity_scan.py` + `scripts/scan_identities.py` | 身份线索扫描（只罗列 UID，不判断） |
| `docs/IDENTITY_SCAN.md` | 身份扫描说明（规则/输出/边界） |
| `docs/ARCHIVE_PROFILE.md` | 档案画像说明（事实来源/好感度窗口/自定义设定） |
| `scripts/merge_legacy_identities.py` | 合并旧 QQ 号画像到档案 ID |
| `scripts/start_hub.ps1` | 启动中枢（日志轮转，重启不丢历史） |
| `console/static/js/logs.js` | 控制台「运行日志」页 |
| `console/static/js/messages.js` | 顶部消息图标 + 弹窗（只显示本次启动后） |
| `agent_core/messages.py` | 进程内消息队列（info/warn/error） |
| `agent_core/tool_shelves/admin.py` | 工具设置读写（direct/filtered/提权） |
| `agent_core/tool_shelves/registry.py` | 网关见过的工具注册表 |
| `archive/modules_bilibili_20261031/` | 已归档的中枢 B 站模块（不再加载） |
| `console/static/js/tools_page.js` | 控制台「工具」页 + 设置弹窗 |
| `console/static/js/identity_map.js` + `css/identity_map.css` | 控制台侧边栏「身份映射」页（#identity 可直达） |
| `docs/CONNECT_ASTRBOT.md` | 一键接入说明（参数/改动/回滚/排错） |
| `data/clients.json` | 客户端 Key 与 tool_permissions（含宿主机工具 deny） |
| `data/tool_shelves.json` | 工具大类与裁剪策略（热加载，可一键关闸） |
| `docs/TOOL_SHELVES.md` | 工具按需加载方案与运维 |
| `data/models_config.json` | 凭证/价格/额度 |
| `data/compute_providers.json` | 槽位/provider |
| `data/memory.db` | 主数据 |
| `data/memory_graph.db` | 图数据 |
| `docs/ASTRBOT_SETUP.md` | AstrBot/NapCat 接入 |
| `docs/IDENTITY_ACCESS.md` | 身份/白名单/访客 |
| `docs/MEMORY_LAYERS.md` | 记忆层 |
| `tests/` | 回归测试 |

---

## 15. 交接检查表

- [ ] 18895 监听正常
- [ ] nginx 18890/18891/53353 反代正常
- [ ] AstrBot 6185 已启动（如需要）
- [ ] NapCat 6099 已启动（如需要）
- [ ] AstrBot provider 指向中枢 `/v1`
- [ ] AstrBot 模型为 `gemini-3.8-flash`
- [ ] AstrBot embedding 关闭
- [ ] 首页余额/今日指标正常
- [ ] 凭证弹窗只有一个叉，操作在右上角
- [ ] 停用凭证只读
- [ ] 账本分桶卡片正常
- [ ] 清空账本已备份
- [ ] 310 测试通过
- [ ] 生产数据库 schema 26
- [ ] 档案只有 `arc_master`，没有多余自动档案
- [ ] 访客策略仍为默认关闭
- [ ] 不在文档/聊天中泄露 API Key













