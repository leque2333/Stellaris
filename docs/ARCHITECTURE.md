# 架构总览

> 详细方案见 [../PROJECT_SPECIFICATION.md](../PROJECT_SPECIFICATION.md)  
> 接入步骤见 [ASTRBOT_SETUP.md](ASTRBOT_SETUP.md)

## 一句话

```text
AstrBot 是平台与工具层
中枢是人格、档案、记忆、模型与账本层
```

## 运行拓扑

```text
QQ / B站 / 群聊 / 其他客户端
        │
        ▼
     AstrBot
（NapCat、B站插件、群观察、MCP 工具）
        │ OpenAI /v1
        ▼
     记忆中枢
（人格、档案、时间轴、事实、图、模型路由、账本）
```

## 中枢边界

```text
保留：
  人格
  档案 / 身份绑定
  时间轴记忆
  事实层 / 图关系
  模型凭证与路由
  Token 账本
  Web 控制台

不做：
  平台协议
  平台限流
  工具执行
  连接中心 / 服务注册 / 模块市场
```

## 数据

```text
data/memory.db         时间轴、事实、档案、账本
data/memory_graph.db   独立图库
data/persona.md   人格
data/models_config.json
data/compute_providers.json
data/auth_config.json
```

## 关键入口

```text
console/server.py           HTTP / Web / OpenAI 兼容网关
agent_core/persona.py       人格上下文
agent_core/timeline_hub.py  时间轴与档案
agent_core/memory/facts.py  事实层
agent_core/memory/graph_store.py 图库
agent_core/compute_providers.py 模型路由
```

## 已下线

```text
连接中心
模块导入
服务注册
桥接器
MCP 面板
工具权限
OneBot 直连
```

旧文件与清理记录见 [CLEANUP_REPORT.md](CLEANUP_REPORT.md)。

