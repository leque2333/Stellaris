# 从下载项目到连接 AstrBot

本文按“先启动中枢，再启动 AstrBot，最后把 QQ / B站接进来”的顺序写。

---

## 1. 准备

### 中枢

- Python 3.10+（推荐 3.11/3.12）
- 一个可被 AstrBot 容器访问到的地址
- 端口示例：`18895`

### AstrBot

- AstrBot（Docker 或宿主安装）
- NapCat（QQ / OneBot v11）
- 可选：B站账号

### 端口规划

```text
18895   中枢 Web / OpenAI /v1
6185    AstrBot WebUI
6099    NapCat WebUI
3001    NapCat OneBot HTTP/WS（本机调试）
```

> 中枢和 AstrBot 分开部署时，AstrBot 里不能写 `127.0.0.1:18895`，那指向容器自己。  
> 要用中枢所在机器的局域网 IP，例如 `http://192.168.3.4:18895/v1`。

---

## 2. 下载并启动中枢

```bash
git clone https://github.com/leque2333/agent-.git
cd agent-
```

### 本机运行

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements-agent.txt
python console/server.py 18895
```

打开：

```text
http://127.0.0.1:18895
```

首次登录后修改默认密码，配置在：

```text
data/auth_config.json
```

### Docker 运行

```bash
docker compose up -d --build
```

默认中枢端口 `18890`。  
如果前面有 nginx，建议外部只暴露一个入口，反代到中枢实际端口，避免 18890 / 18895 同时跑两个实例。

### 验证中枢

```bash
curl http://127.0.0.1:18895/v1/models
```

应返回 OpenAI 格式的模型列表。

---

## 3. 配置中枢模型

打开中枢 Web：

```text
设置 → 模型配置
```

至少保留：

```text
主控凭证：Gemini / DeepSeek / 其他 OpenAI 兼容凭证
功能凭证：复杂任务可用的后台模型
```

当前推荐：

```text
active_model = gemini-3.8-flash
主控凭证 model = gemini-3.8-flash
persona_slot = google_gemini_official
logic_slot = deepseek_official
```

注意：

- Web 里修改主控凭证模型，会改变中枢实际调用；
- AstrBot 里填的 `model` 只是请求标签，中枢不会拿它做真实路由；
- 不要在 AstrBot 里再写 `gemini-3.1-pro-preview-customtools`，除非你确实想用 3.1。

---

## 4. 创建 AstrBot 访问 Key（可选）

默认 `data/clients.json` 不存在时，中枢不强制校验 Key。  
生产环境建议创建：

```bash
copy data\clients.example.json data\clients.json
```

把 `hub_sk_astrbot_change_me` 改成随机值，并设置：

```json
{
  "require_key": true,
  "clients": [
    {
      "id": "astrbot",
      "name": "AstrBot QQ 通道",
      "keys": ["你的随机Key"],
      "channel": "qq",
      "default_actor": "master",
      "allow_recall_memory": true,
      "allow_ingest_timeline": true,
      "rate_limit_per_minute": 120
    }
  ]
}
```

AstrBot 里填这个 Key。

---

## 5. 安装 AstrBot

Docker 示例：

```bash
docker run -d \
  --name astrbot \
  -p 6185:6185 \
  -v /path/to/astrbot/data:/AstrBot/data \
  --restart unless-stopped \
  soulteary/astrbot:latest
```

打开：

```text
http://<主机>:6185
```

具体镜像和版本以你当前部署为准。

---

## 6. 在 AstrBot 添加中枢 Provider

AstrBot Web：

```text
配置 → 服务提供商 → 新增
```

选择：

```text
类型：OpenAI Chat Completion
```

填写：

```text
ID:        anan_hub_source
Base URL:  http://<中枢主机>:18895/v1
API Key:   你的中枢 Key
```

再新增一个 Provider 实例：

```text
ID:        anan_hub_main
来源:      anan_hub_source
模型:      gemini-3.8-flash
启用:      是
```

把它设为默认聊天模型。

### 对应 JSON

如果你直接改 `cmd_config.json`，核心结构是：

```json
{
  "provider_sources": [
    {
      "id": "anan_hub_source",
      "type": "openai_chat_completion",
      "api_base": "http://<中枢主机>:18895/v1",
      "api_key": ["你的中枢 Key"]
    }
  ],
  "provider": [
    {
      "id": "anan_hub_main",
      "enable": true,
      "model": "gemini-3.8-flash",
      "provider_source_id": "anan_hub_source"
    }
  ]
}
```

### 不要做的事

```text
不要填 AstrBot 的模型为 3.1，除非你明确要 3.1
不要把 Base URL 写成 127.0.0.1（AstrBot 在容器里）
不要开启 NapCat 到中枢的直连 HTTP
```

---

## 7. 连接 NapCat（QQ）

NapCat WebUI → 网络配置 → OneBot v11：

```text
WebSocket 服务端：开启
WebSocket 客户端：连接到 AstrBot 的 ws 地址
HTTP 服务端：关闭（中枢不再直连 NapCat）
HTTP 客户端：关闭
```

AstrBot 侧选择 `aiocqhttp` / OneBot v11 适配器，连接 NapCat 的 WebSocket。

验证：

```text
QQ 私聊机器人
→ AstrBot 调用 anan_hub_main
→ 中枢 /v1
→ 返回人格回复
```

中枢日志应出现：

```text
POST /v1/chat/completions
```

---

## 8. 群观察与 B站

### 群观察插件

`astrbot_plugin_group_observer` 配置：

```json
{
  "hub_base_url": "http://<中枢主机>:18895",
  "enabled": true,
  "reply_mode": "probability",
  "probability": 0.1,
  "cooldown_seconds": 120,
  "max_per_hour": 6,
  "ignore_prefixes": ["/"]
}
```

它负责“是否插话”的策略，实际回复仍走中枢。

### B站插件

`astrbot_plugin_bilibili` 的 `config.json`：

```json
{
  "hub": {
    "base_url": "http://<中枢主机>:18895/v1"
  },
  "model": "gemini-3.8-flash"
}
```

B站登录、巡检、评论回复由 AstrBot 插件执行；人格和记忆在中枢。

---

## 9. 关闭 AstrBot 自带知识库 / Embedding

如果不用 AstrBot 原生知识库：

```json
{
  "id": "gemini_embedding_inst",
  "enable": false,
  "provider_source_id": "gemini_embedding_source"
}
```

影响范围只有：

```text
知识库向量化
知识库语义检索
```

不影响：

```text
QQ 聊天
B站
群观察
MCP 工具
中枢记忆
```

知识库建议放中枢：

```text
data/knowledge/arknights/
data/knowledge/limbus/
```

---

## 10. 验证

### 聊天

```bash
curl http://<中枢主机>:18895/v1/chat/completions \
  -H "Authorization: Bearer 你的Key" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-3.8-flash","messages":[{"role":"user","content":"用一句话说明你是谁"}]}'
```

### 账本

```bash
curl http://<中枢主机>:18895/api/tokens
```

### 事实

```bash
curl http://<中枢主机>:18895/api/memory/facts?status=active
```

---

## 11. 常见问题

### 账本出现 3.1

检查两处：

```text
AstrBot cmd_config.json 的 anan_hub_main.model
中枢 compute_providers.json 的 persona_slot / provider model
```

两处都应该是 3.8（或你明确选择的模型）。

### AstrBot 连不上中枢

在 AstrBot 容器内测试：

```bash
docker exec -it astrbot curl http://<中枢主机>:18895/v1/models
```

不要用 `127.0.0.1`。

### 今日统计是 0

跨天后“今日”会归零，累计不会。  
看 `/api/ledger` 的 `overall` 字段。

### 页面数字很久才出来

确认使用的是统一入口，而不是旧的 18890 容器。  
当前 `/api/tokens` 是轻量统计，`/api/overview` 才包含时间轴等重数据。

---

## 12. 升级

```bash
git pull
pip install -r requirements-agent.txt
# 重启中枢
```

升级前备份：

```text
data/memory.db
data/memory_graph.db
data/persona.md
data/models_config.json
data/compute_providers.json
data/clients.json
```

---

## 13. 白名单与临时访客

默认只允许白名单身份。

非白名单 QQ / B站用户调用 `/v1` 会返回：

```text
403 guest_disabled
```

如果希望陌生人也能对话，在中枢 Web：

```text
设置 → 常规与系统 → 访问策略
```

打开：

```text
临时访客
```

再决定是否打开：

```text
访客写入时间轴
```

临时访客：

```text
没有档案
不写长期事实
不写图关系
好感度默认 50
```

访客时间轴默认关闭，关闭时访客对话不落库。

添加白名单：

```text
档案 → 选择档案 → 添加身份
```

只有白名单身份会进入档案记忆。

## 流式响应默认关闭

`connect_astrbot.py` 接入时会把 `provider_settings.streaming_response` 设成 `False`。

原因：中枢的「记忆检索」需要等整轮回复、发现检索指令、查库后再重答一次；
流式下字已经吐出去了，做不到。关掉流式不影响 QQ 的观感（消息本来就是整条发送），
换来的能力是「我前天说过什么」这类问题能真的翻到旧记录。

想开回来：

```json
{"provider_settings": {"streaming_response": true}}
```

**现在可以放心的开回来了**：中枢加了「时间锚点预取」——用户话里带
前天/昨天/上次/记得/说过这类词时，中枢在调模型前就先查库并联结果，
流式与非流式都生效。流式下只是不再挂「[[记忆检索]]」那条精确指令。
