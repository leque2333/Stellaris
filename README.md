# 🌟 Agent Core: Persona-Centric Autonomous Agent & LifeStream Hub

一个以**人格灵魂（Persona Kernel）**与**连续自传式时间轴（Chronological LifeStream）**为核心的独立智能体架构系统。

解耦了传统“聊天机器人即核心”的束缚，将 QQ、Telegram、Web 控制台与终端 CLI 作为外设通道，向下统筹 PC 宿主机系统操控、B 站拟真巡检、多模态工具调用与连续经历流沉淀。

---

## 🏛️ 核心架构

```mermaid
flowchart TD
    subgraph Channels ["多端通信与接入通道 (IO Adapters)"]
        QQ["QQ 通道 (NapCat / OneBot v11)"]
        TG["Telegram 通道"]
        WebConsole["Web 调控中枢控制台 (8890)"]
        CLI["本地终端交互 (agent_core.cli)"]
    end

    subgraph AgentCore ["智能体大脑与状态机 (AnanAgent Core)"]
        direction TB
        Identity["统一实体解析 (IdentityResolver)"]
        
        subgraph Soul ["人设与认知规范 (Persona Kernel)"]
            Persona["黄金人设与台词法则<br>(自然口语 / 纯净台词 / 句末去句号)"]
            Affection["亲疏温差体系 (0~100 好感动态指引)"]
        end

        subgraph MemoryHub ["连续自传时间轴中枢 (LifeStream Hub)"]
            Timeline["连续经历时间轴 (agent_timeline)<br>三维近因召回 + 时序顺藤摸瓜"]
            Profile["长效偏好事实库 (user_profiles)"]
        end

        Orchestrator["智能体决策器 (ReAct Tool Loop)"]
        PostProcessor["拟人口语清洗器 (去句号 / 剥离神态描写)"]
    end

    subgraph Tools ["统一工具与技能总线 (Tool & Skill Engine)"]
        T_PC["💻 宿主机 PC 操控 (状态/截屏/锁屏/脚本)"]
        T_Bili["🎬 B站拟真探索与见闻检索"]
        T_Aff["❤️ 情绪与好感度自主浮动"]
        T_Ext["🧩 声明式自定义扩展工具 (@tool_registry.register)"]
    end

    Channels -->|AgentInput| Identity
    Identity --> Orchestrator
    Orchestrator <--> Soul & MemoryHub
    Orchestrator <--> Tools
    Orchestrator --> PostProcessor
    PostProcessor -->|AgentOutput| Channels
    PostProcessor -.->|经历流回灌 (Experience Stream)| Timeline
```

---

## ✨ 核心特性

1. **以人设为中心（Persona-Centric）**：
   * 严格的人设与口语质感控制，杜绝客服腔与翻译腔；
   * 遵循输出规范：句末绝不带句号（。）、剥离任何括号描写（神态/心理说明）；
   * 动态亲疏温差指引，依据好感度（0~100）自调节亲昵度与表达尺度。

2. **自传式时间轴生命流（Chronological LifeStream）**：
   * 各模块（B站巡视、PC控制、多端对话）统一汇入一条时序时间轴；
   * **三维拟人召回模型**：
     $$\text{Score} = \alpha \cdot \text{Similarity} + \beta \cdot e^{-\Delta t / \tau} + \gamma \cdot \text{Importance}$$
     融合语义相似度、艾宾浩斯时间衰减（近因效应）与重大事件锚点，解决传统 AI 失去时间感知与因果断裂的问题。

3. **声明式统一工具总线（Tool Registry）**：
   * 仅需使用 `@tool_registry.register` 装饰器，即可一键挂载任意 Python 异步/同步函数；
   * 自动推导 JSON Schema，由大模型自主规划并执行 Function Calling 闭环。

4. **多端解耦接入**：
   * QQ (NapCat/OneBot)、Telegram、WebUI、本地 CLI 终端均作为无状态适配器接入。

---

## 📁 目录结构

```
.
├── agent_core/                       # 核心独立智能体引擎
│   ├── agent.py                      # AnanAgent 主控调度器
│   ├── persona.py                    # 人设管理器与输出清洗器
│   ├── tools.py                      # 统一工具注册中心
│   ├── llm.py                        # 大模型 Function Calling 网关
│   ├── timeline_hub.py               # 时间轴生命流总线
│   ├── identity.py                   # 统一身份对齐解析器
│   ├── models.py                     # 数据模型契约
│   └── cli.py                        # 本地终端交互外壳
├── antigravity_bridge/               # 宿主机反重力 PC 控制网关
├── console/                          # 容器化调控中枢 WebUI (端口 8890)
├── astrbot/                          # QQ 协议接入与扩展插件目录
│   └── data/
│       ├── plugins/                  # 巡检、轮次防抖、长效记忆等自研插件
│       └── cmd_config.example.json   # 核心配置模板
├── docker-compose.yml                # 容器化编排定义
├── telegram_monitor.py               # Telegram 监控与通知通道
└── README.md
```

---

## 🚀 快速启动

### 1. 本地命令行终端体验（无需 Docker）
直接在终端与智能体展开对话、调试工具调用：
```bash
python -m agent_core.cli
```

### 2. 配置文件初始化
复制模板文件并填入凭据：
```bash
cp git_repo_config.example.json git_repo_config.json
cp tg_monitor_config.example.json tg_monitor_config.json
cp antigravity_bridge/bridge_config.example.json antigravity_bridge/bridge_config.json
cp astrbot/data/cmd_config.example.json astrbot/data/cmd_config.json
cp astrbot/data/plugins/astrbot_plugin_human_patrol/config.example.json astrbot/data/plugins/astrbot_plugin_human_patrol/config.json
```

### 3. Docker 容器编排部署
```bash
# 启动所有服务 (QQ协议端 NapCat + AstrBot + 调控中枢 Console)
docker compose up -d

# 查看运行日志
docker compose logs -f
```

---

## 🛠️ 自定义工具扩展

只需 3 行代码即可为智能体扩展任意新功能：

```python
from agent_core import tool_registry

@tool_registry.register(
    name="my_custom_tool",
    description="当用户询问自定义业务或需要执行特定任务时调用"
)
async def my_custom_tool(query: str) -> str:
    # 你的业务执行逻辑
    return f"任务执行结果: {query}"
```

---

## 📄 开源与隐私声明

本项目仓库已脱敏：
- 剥离了特定私人人设卡与核心私密设定集；
- 剥离了个人数据库（`*.db`）、会话记录与观测信笺；
- 剥离了所有真实 API Key、Cookie、QQ 会话缓存与敏感 Token。
