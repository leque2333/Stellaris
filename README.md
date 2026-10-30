# Stellaris

一个只负责 **人格、档案、记忆、模型路由与 Token 账本** 的智能体中枢。

QQ、B站、群聊、工具调用、消息发送都交给 AstrBot；中枢通过 OpenAI 兼容的 `/v1` 接口向 AstrBot 提供统一的大脑。

```text
QQ / B站 / 群聊 / 其他客户端
            │
            ▼
        AstrBot
   （平台接入、工具执行）
            │  OpenAI /v1
            ▼
        记忆中枢
  ┌─────────────────────┐
  │ 人格 / 档案          │
  │ 时间轴 / 事实 / 图    │
  │ 模型路由 / Token 账本 │
  └─────────────────────┘
```

---

## 三个目标

1. **所有 AI 入口统一走中枢**
   - 不再每个平台一套模型、Prompt、Key、记忆。
2. **跨项目、跨平台共享同一段记忆**
   - QQ、B站、CLI、其他客户端最终落到同一个 `person_id` / `archive_id`。
3. **只有一套人格**
   - 平台只决定“在哪说话”，不决定“她是谁”。

---

## 中枢负责什么

| 能力 | 说明 |
|---|---|
| 人格 | 全局人设卡、语气、关系温度、输出规则 |
| 档案 | 人物档案、平台身份绑定 |
| 时间轴记忆 | 事件流、语义召回、精确子串召回 |
| 事实层 | 长效事实、有效期、冲突待确认、回滚 |
| 图关系 | 独立 `memory_graph.db`，人物—关系—事物 |
| 模型路由 | 主控凭证 / 功能凭证 / 兜底 |
| Token 账本 | 调用次数、输入/输出/缓存、成本 |

## 中枢不负责什么

- QQ 协议、NapCat、B站登录与巡检
- 平台限流、群聊插话策略
- 工具执行（由 AstrBot 执行并回传）
- 独立图数据库服务（当前使用独立 SQLite 图文件）
- 向量数据库服务（当前使用本地 SQLite + 本地 embedding）

---

## 快速启动

### 方式一：本机 Python

```bash
git clone https://github.com/leque2333/agent-.git
cd agent-
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

pip install -r requirements-agent.txt
python console/server.py 18895
```

打开：

```text
http://127.0.0.1:18895
```

默认管理员账号在 `data/auth_config.json` 中初始化；首次部署后请立即修改密码。

### 方式二：Docker

```bash
docker compose up -d --build
```

默认映射：

```text
18890 -> 18890   中枢 Web / API
18888 -> 18888   预留内部端口
```

> 生产环境如果使用 nginx，建议统一把外部入口反代到同一个中枢端口，避免同时运行多个旧容器。

### 数据目录

```text
data/
├── memory.db             # 时间轴、事实、档案、账本
├── memory_graph.db       # 独立图库
├── persona.md       # 当前人格卡
├── models_config.json    # 主控/功能凭证
├── compute_providers.json# 槽位与 provider
├── auth_config.json      # 控制台登录
└── clients.json          # 可选：客户端 Key / 权限
```

`data/` 不应提交到 Git。

`data/persona.md` 是唯一人格卡：角色名、语气、世界观都从这里读取，替换文件即换角色；旧 `data/anan_persona.md` 会在 `persona.md` 缺失时自动迁移/回退。项目代码不硬编码具体角色名。

控制台的「人格设定 → 基底设定」拆成 **AI 名称 / 人格设定 / 对话范例 / 其他** 四个字段；好感度的阶段、区间和态度指引统一在「好感度」子页配置，不再写进人格文本。

---

## 连接 AstrBot（一键）

已经装好 AstrBot 的话，不用手改配置：

```powershell
# 1) 启动中枢
python console/server.py 18895

# 2) 另开窗口，一键连接
python scripts\connect_astrbot.py
```

Windows 也可以直接双击 `scripts\连接AstrBot.bat`。

脚本会自动找到 AstrBot 的 data 目录、确认中枢在线、备份并改写 `cmd_config.json`，
把中枢设为默认模型提供商；`--p4 --restart` 还能把薄壳工具一起装好并重启 AstrBot。

完整说明（参数、改了哪些字段、幂等、回滚、排错）见
[docs/CONNECT_ASTRBOT.md](docs/CONNECT_ASTRBOT.md)；手工接入见
[docs/ASTRBOT_SETUP.md](docs/ASTRBOT_SETUP.md)。

手工最短版本（不想用脚本时）：

1. 启动中枢：`python console/server.py 18895`
2. 在 AstrBot 添加 OpenAI Chat Completion Provider：
   ```text
   Base URL: http://<中枢主机>:18895/v1
   API Key:  hub-sk-<你的中枢 Key>
   Model:    gemini-3.8-flash
   ```
3. 把该 Provider 设为默认聊天模型。
4. NapCat 通过 OneBot v11 WebSocket 连 AstrBot。
5. AstrBot 的群观察 / B站插件填中枢地址。

模型名只是请求标签；中枢实际用 `models_config.json` 里的主控凭证和 `persona_slot` 路由。Web 修改主控模型后，以 Web 配置为准。

> 中枢与 AstrBot 是"标准 OpenAI 协议"关系，不是代码依赖：换任何框架，把它的
> OpenAI Provider 指到同一个 `/v1` 即可，中枢零改动。

---

## 关键接口

| 接口 | 用途 |
|---|---|
| `POST /v1/chat/completions` | OpenAI 兼容聊天，供 AstrBot 等客户端调用 |
| `GET /v1/models` | 模型列表 |
| `GET /api/overview` | 控制台概览（含时间轴、资源、统计） |
| `GET /api/tokens` | 轻量 Token 统计 |
| `GET /api/ledger` | 账本流水 |
| `GET /api/memory/facts` | 事实列表 / 时间点查询 |
| `POST /api/memory/facts` | 新增 / 修改事实 |
| `POST /api/memory/facts/rollback` | 回滚事实 |
| `GET /api/memory/graph` | 实体关系 |
| `GET /api/archives` | 档案列表 |

连接管理、模块导入、服务注册、MCP 面板等旧接口已下线。

---

## 测试

```bash
python -m unittest discover -s tests -t .
```

当前测试数以实际运行结果为准；清理旧连接层后不再保留桥接、服务注册、OneBot 直连等测试。

---

## 文档

- [项目方案](PROJECT_SPECIFICATION.md)
- [连接 AstrBot（一键）](docs/CONNECT_ASTRBOT.md)
- [工具按需加载](docs/TOOL_SHELVES.md)
- [身份线索扫描](docs/IDENTITY_SCAN.md)
- [档案画像](docs/ARCHIVE_PROFILE.md)
- [AstrBot 接入指南（手工）](docs/ASTRBOT_SETUP.md)
- [记忆分层](docs/MEMORY_LAYERS.md)
- [存储结构](docs/STORAGE.md)
- [身份、白名单与访问策略](docs/IDENTITY_ACCESS.md)
- [身份传输层](docs/TRANSPORT_LAYER.md)
- [清理记录](docs/CLEANUP_REPORT.md)
- [交接文档](docs/HANDOFF.md)

