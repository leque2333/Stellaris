# 架构总览

> 更新：2026-11-01
> 记忆模块：[MEMORY_MODULE.md](MEMORY_MODULE.md)
> 功能挂载：[FUNCTION_MOUNTING.md](FUNCTION_MOUNTING.md)
> 接入步骤：[ASTRBOT_SETUP.md](ASTRBOT_SETUP.md)

## 一句话

```text
AstrBot / 其他插件是平台与工具层
中枢是人格、档案、记忆、模型、账本与系统工具层
```

## 运行拓扑

```text
QQ / B站 / 群聊 / 其他客户端
        │
        ▼
   插件 / 宿主
（NapCat、B站插件、MCP、普通 HTTP 插件）
        │ OpenAI /v1
        ▼
     记忆中枢
（人格、档案、时间轴、事实、图、模型路由、账本、系统工具）
        │
        ├─ data/memory.db
        ├─ data/memory_graph.db
        └─ data/backups/
```

## 请求生命周期

```text
1. /v1 鉴权 + 客户端识别
2. 插件适配器归一化 platform / uid / host_id
3. 身份解析 platform + uid → archive_id
4. 记忆读取（时间轴 / 召回 / 事实 / 画像）
5. 工具闸门（分类裁剪 / 权限 / 压缩）
6. 注入系统工具（host / ledger）
7. 模型生成
8. 工具调用：
   - 外部工具 → 宿主执行
   - 系统工具 → 中枢本地执行
9. 写时间轴 / 详情 / 事实 / 账本
10. 聚合器后台生成摘要
```

## 中枢边界

```text
保留：
  人格
  档案 / 身份绑定
  时间轴记忆
  事实层 / 图关系
  模型凭证与路由
  Token 账本 / 预警
  系统工具（宿主机、账本查询）
  Web 控制台

不做：
  平台协议
  平台限流
  外部工具的实际执行
  连接中心 / 服务注册 / 模块市场
```

## 记忆模块

详细见 [MEMORY_MODULE.md](MEMORY_MODULE.md)。

```text
L1 invocations        单次请求上下文
L2 agent_timeline     连续经历 + 标签
L3 memory_details     长原文
   user_facts         长期事实
   memory_digests     摘要
   timeline_buffer    超出常驻预算的旧事件
   archives           人物档案
```

时间轴标签：

```text
tag_primary     插件 / 工具
tag_secondary   档案 ID；无档案才用 UID
tag_permission  admin / guest / system / unknown
```

展示时拆成 `插件 / 平台 / 人员` 三个标签，人员按管理员/访客着色。

## 功能挂载

详细见 [FUNCTION_MOUNTING.md](FUNCTION_MOUNTING.md)。

```text
外部工具  → tools 字段 → 工具闸门 → 宿主执行
系统工具  → system_tools 元数据 → 中枢本地执行
插件适配  → console/adapters（通用头 / JSON / 生成 Python）
宿主机桥  → host_bridge（HTTP）
```

当前系统工具：

```text
control_host_pc_via_antigravity   AstrBot 插件 / host_bridge
hub_host_control                 中枢本地
hub_ledger_query                 中枢本地只读账本
```

## 数据

```text
data/memory.db              时间轴、事实、档案、账本、缓存
data/memory_graph.db        独立图库
data/persona.md             人格
data/models_config.json     凭证 / 模型 / 分时价格
data/compute_providers.json 算力路由
data/tool_shelves.json      工具分类与权限
data/system_tools.json      系统工具开关
data/token_alerts.json      Token 预警设置
data/auth_config.json       控制台会话
data/clients.json           外部客户端与 Key
```

## 关键入口

```text
console/server.py                    HTTP / Web / OpenAI 兼容网关
console/adapters/                    插件适配器
agent_core/memory/                   记忆读写、聚合、导出
agent_core/tool_shelves/             工具闸门与系统工具
agent_core/host_control.py           宿主机备用控制
agent_core/ledger_tool.py            账本查询系统工具
agent_core/compute_providers.py      模型路由
console/modules/model_service.py     凭证、定价、账本
console/modules/token_alerts.py      余额 / Token 预警
```

## 2026-11-01 变更

- 时间轴标签拆分：插件 / 平台 / 人员独立展示，档案名按角色着色
- 记忆库设置页：占用空间、原生导出、一键导入（JSONL / ChatGPT / 文本）
- Token 预警：按币种余额阈值 + 最近 N 天动态 Token 上限，每天提醒一次
- 分时价格支持周几区间，工作日/休息日可分开定价
- 账单支持按输入/缓存/输出三项单价修正
- 插件适配器：统一接口、声明式 JSON、执行 AI 生成器
- 系统工具新增 `hub_ledger_query`

## 已下线

```text
连接中心
模块导入
服务注册
桥接器
MCP 面板
OneBot 直连
```

旧文件与清理记录见 [CLEANUP_REPORT.md](CLEANUP_REPORT.md)。
