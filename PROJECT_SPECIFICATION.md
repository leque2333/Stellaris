# 项目方案 / Stellaris Specification

> 版本：v8.0-AstrBot  
> 更新时间：2026-10-30  
> 定位：人格、档案、记忆、模型路由、Token 账本

---

## 1. 一句话

**中枢是大脑，AstrBot 是手脚。**

```text
AstrBot 负责：QQ / B站 / 群聊 / 工具 / 消息发送
中枢负责：人格 / 档案 / 记忆 / 事实 / 图 / 模型 / 账本
```

中枢不重新实现 NapCat、OneBot、平台协议，也不做平台限流。

---

## 2. 目标

### 2.1 统一 AI 入口

所有会调用大模型的入口都走中枢 `/v1`：

```text
AstrBot
B站插件
群观察插件
CLI
其他 OpenAI 兼容客户端
```

入口只负责输入、展示、发送；不各自维护 Prompt、Key、记忆。

### 2.2 统一连续记忆

同一个人在不同平台落到同一个档案：

```text
QQ <你的QQ> ─┐
B站 <B站UID> ─┼─→ archive/person arc_master
其他平台 UID  ─┘
```

因此：

```text
QQ 说过的暗号
B站 再问
→ 能从同一套事实层召回
```

### 2.3 统一人格

人格只有一份：

```text
data/persona.md
```

平台只决定语气温度、称呼、场景，不决定“她是谁”。

---

## 3. 非目标

```text
不做 QQ / B站 / NapCat 协议实现
不做平台群控、主动发送、限流策略
不做通用 RPA / 桌面控制
不做连接中心、模块市场、服务注册
不做独立图数据库服务
不做独立向量数据库服务
```

---

## 4. 系统架构

```text
┌──────────────────────────────────────────────────┐
│                  接入层（AstrBot）                │
│  QQ/NapCat │ B站插件 │ 群观察 │ MCP 工具          │
└──────────────────────┬───────────────────────────┘
                       │ OpenAI /v1
┌──────────────────────▼───────────────────────────┐
│                  中枢（本项目）                   │
│                                                  │
│  /v1 网关                                        │
│   ├─ 身份解析 / 档案绑定                          │
│   ├─ 人格上下文                                   │
│   ├─ 事实注入                                     │
│   ├─ 图关系注入                                   │
│   ├─ 时间轴召回                                   │
│   └─ 模型路由 / 账本                              │
│                                                  │
│  Web 控制台                                       │
│   ├─ 仪表盘 / Token 账本                          │
│   ├─ 时间轴 + 事实 + 图                           │
│   ├─ 人格 / 档案                                  │
│   └─ 设置 / 模型凭证                              │
└──────────────────────┬───────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────┐
│                    存储层                         │
│  data/memory.db         时间轴/事实/档案/账本     │
│  data/memory_graph.db   独立图库                  │
│  data/*.json            模型/凭证/鉴权             │
│  data/persona.md   人格卡                    │
└──────────────────────────────────────────────────┘
```

---

## 5. 目录

```text
.
├── agent_core/
│   ├── persona.py              # 人格上下文
│   ├── identity.py             # 身份解析
│   ├── timeline_hub.py         # 时间轴/档案/召回
│   ├── compute_providers.py    # 模型槽位与路由
│   ├── clients.py              # 客户端 Key / 权限
│   ├── memory/
│   │   ├── facts.py            # 事实层、时间点、回滚、AI 抽取
│   │   ├── graph_store.py      # 独立图库
│   │   ├── episodic_stream.py  # 时间轴召回
│   │   ├── fact_manager.py     # 旧画像事实兼容
│   │   ├── embedding.py        # 本地向量
│   │   └── reflection_engine.py
│   ├── storage/                # SQLite schema / repositories
│   └── profile/                # 档案
├── console/
│   ├── server.py               # HTTP + /v1 + Web API
│   ├── backend.py              # 服务聚合
│   ├── index.html / login.html
│   ├── static/js               # 模块化前端
│   └── modules/                # 控制台后端服务
├── data/                       # 运行时数据（不提交）
├── docs/                       # 文档
├── tests/                      # 单元/接口测试
├── docker-compose.yml
├── Dockerfile
└── requirements-agent.txt
```

---

## 6. 核心模块

### 6.1 身份、白名单与档案

```text
platform + platform_user_id
→ archive_identities
→ archive_id / person_id
```

`archive_identities` 是唯一白名单表：

```text
UNIQUE(platform, platform_user_id)
```

只有表里的身份才有档案、长期记忆和自定义好感度。

档案角色：

```text
admin    管理员
guest    访客档案（在白名单里，但不是管理员）
```

非白名单：

```text
guest_enabled=false → 403
guest_enabled=true  → 临时访客，无档案，默认 50
```

策略文件：

```text
data/access_policy.json
```

```json
{
  "guest_enabled": false,
  "guest_timeline_enabled": false,
  "guest_default_affection": 50
}
```

详见 [docs/IDENTITY_ACCESS.md](docs/IDENTITY_ACCESS.md)。

同一 `person_id` 跨平台共享事实和图关系。

### 6.2 人格

```text
data/persona.md
+ 档案好感度
+ 当前交互对象
+ 事实/图/记忆
→ system prompt
```

人格只在中枢，平台不保存第二份。

### 6.3 时间轴

表：

```text
agent_timeline
```

能力：

```text
写入事件
向量召回
jieba 分词 + FTS 精确召回
LIKE 兜底
```

### 6.4 事实层

表：

```text
memory_facts
```

字段：

```text
subject_id, predicate, value
valid_at, invalid_at
created_at, expired_at
confidence, status
source_event_id
```

状态：

```text
active
pending
superseded
deleted
```

能力：

```text
规则抽取
AI 隐性抽取（低置信度进 pending）
冲突待确认
确认 / 拒绝 / 回滚
按时间点查询
```

### 6.5 图关系

独立文件：

```text
data/memory_graph.db
```

节点：

```text
person / thing
```

边：

```text
subject --predicate--> object
valid_at / invalid_at / fact_id
```

图关系由事实自动生成，并注入 prompt。

### 6.6 模型路由

槽位：

```text
persona_slot   普通对话 / B站 / 群观察 / 事实抽取
logic_slot     复杂逻辑任务
fact_slot      AI 事实抽取（默认主控凭证）
```

候选顺序：

```text
主控凭证（models_config）
→ persona/logic provider 兜底
```

Web 修改主控凭证模型后，实际调用和账本都会跟随。

### 6.7 Token 账本

表：

```text
token_usage
```

记录：

```text
model（实际模型）
source（QQ群/私聊/B站/universal_proxy）
prompt_tokens
completion_tokens
cached_tokens
cost_usd
```

本地逻辑任务会单独记一条 `logic_slot` 的账，不再混进主模型。

---

## 7. OpenAI 兼容接口

### 请求

```http
POST /v1/chat/completions
Authorization: Bearer <key>
Content-Type: application/json
```

```json
{
  "model": "gemini-3.8-flash",
  "messages": [
    {"role": "user", "content": "你好"}
  ],
  "stream": false
}
```

可选身份头：

```text
X-User-Id
X-Platform
X-User-Name
X-Channel
X-Channel-Type
```

也支持请求体：

```json
"user": "<你的QQ>"
```

### 响应

标准 OpenAI：

```json
{
  "id": "chatcmpl_...",
  "object": "chat.completion",
  "choices": [{"message": {"role": "assistant", "content": "..."}}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0}
}
```

工具调用：

```text
中枢透传 tools
模型返回 tool_calls
AstrBot 执行工具
```

中枢不执行工具。

---

## 8. Web 控制台

页面：

```text
仪表盘
记忆库（时间轴 + 长期事实 + 实体关系）
人格设定
档案
账单流水
设置
```

已移除：

```text
连接中心
模块管理
桥接器
服务注册
MCP 面板
工具权限
```

---

## 9. 部署

### 本机

```bash
python console/server.py 18895
```

### Docker

```bash
docker compose up -d --build
```

### 反向代理

```text
18891 / 53353 → nginx → 18895
18890 → 应只保留一个入口，避免旧容器抢占
```

外部只需要一个稳定入口。

---

## 10. 安全

```text
data/auth_config.json       控制台登录
data/clients.json           客户端 Key / 权限 / 限流
```

生产环境：

```text
修改默认密码
开启 require_key
每个客户端独立 Key
不要把 data/ 提交 Git
不要把 GitHub Token 写进 remote URL
```

---

## 11. 测试

```bash
python -m unittest discover -s tests -t .
```

测试覆盖：

```text
/v1 网关
身份绑定
档案 API
记忆时间轴
事实层 / 时间点 / 回滚
图关系
Token 账本
静态资源
```

旧连接层测试已移除。

---

## 12. 演进路线

### 已完成

```text
AstrBot 作为唯一平台/工具层
中枢 /v1 统一模型入口
跨平台档案
事实层 + 时间点 + 回滚
独立图库
jieba 中文召回
Token 实际模型记账
Web 统计与账本
```

### 下一步

```text
中枢知识库（docs/chunks/fts/embedding）
AstrBot embedding 完全关闭
多跳图查询
记忆冷热分层 / 摘要
部署地址配置化
```

---

## 13. 设计原则

```text
1. 平台能力放 AstrBot
2. 大脑能力放中枢
3. 一个事实只写一处
4. 账本记实际模型，不记请求标签
5. 能被回滚的记忆才可信
6. 不为了接平台而复制第二套人格/记忆
```

