# 连接 AstrBot（一键接入）

> 目标：用户已经装好 AstrBot，装上本项目后**一条命令**接上，不用手改配置。

---

## 1. 最快路径

```powershell
# 1) 先启动中枢（保持窗口开着）
python console/server.py 18895

# 2) 另开一个窗口，一键连接
python scripts\connect_astrbot.py
```

Windows 上也可以直接双击：`scripts\连接AstrBot.bat`

脚本会自动完成：

```text
① 找到 AstrBot 的 data 目录（env / 常见路径 / 同级目录）
② 确认中枢在线，读出模型名
③ 准备中枢客户端 Key（没有客户端注册表时用默认 key）
④ 备份 cmd_config.json，把中枢写成默认模型提供商
⑤ 提示你重启 AstrBot
```

重启后，QQ / B站的消息就会经 AstrBot → 中枢 → 模型，人格与记忆由中枢统一管理。

---

## 2. 常用参数

```powershell
# 只看会改什么，不动文件
python scripts\connect_astrbot.py --dry-run

# AstrBot 不在默认位置时手动指定
python scripts\connect_astrbot.py --astrbot-dir "D:\AstrBot\data"

# 中枢不在本机 / 端口不是 18895
python scripts\connect_astrbot.py --hub 192.168.3.4 --port 18895

# 同时装 P4 薄壳工具，并自动重启 AstrBot 容器
python scripts\connect_astrbot.py --p4 --restart
```

| 参数 | 作用 | 默认 |
|---|---|---|
| `--hub` | 中枢地址 | 自动挑一个容器可达的局域网 IP |
| `--port` | 中枢端口 | `18895` |
| `--model` | 写进 AstrBot 的模型名 | 取中枢 `/v1/models` 的第一个 |
| `--astrbot-dir` | AstrBot data 目录 | 自动查找 |
| `--p4` | 安装薄壳 MCP + 执行器插件 | 不装 |
| `--restart` | 改完重启 AstrBot 容器 | 不重启 |
| `--dry-run` | 只打印改动 | 关闭 |

---

## 3. 它到底改了什么

只碰三个地方，且**每一步都有备份**：

```text
data/clients.json                     # 中枢：登记 astrbot 客户端（没有则创建）
<AstrBot data>/cmd_config.json        # AstrBot：provider_sources / provider / 默认模型
<AstrBot data>/cmd_config.json.bak_connect_<时间戳>
```

写入的关键字段：

```json
"provider_sources": [
  {"id": "anan_hub_source", "type": "openai_chat_completion",
   "api_base": "http://<局域网IP>:18895/v1", "api_key": ["hub-sk-<你的中枢 Key>"]}
],
"provider": [
  {"id": "anan_hub_main", "enable": true, "model": "gemini-3.8-flash",
   "provider_source_id": "anan_hub_source"}
],
"provider_settings.provider_pool": ["anan_hub_main"],
"agent_runner.config.model.provider_id": "anan_hub_main"
```

`--p4` 时额外装：

```text
<AstrBot data>/mcp_hub_tools.py                      # 薄壳 MCP 服务
<AstrBot data>/plugins/astrbot_plugin_hub_tool_executor/   # 执行器插件
<AstrBot data>/mcp_server.json                       # 增加 hub-tools
<AstrBot data>/cmd_config.json → plugin_set          # 启用插件
```

---

## 4. 幂等与回滚

**幂等**：重复运行不会重复添加 provider，只会更新地址/Key/模型。

**回滚**（任选一种）：

```powershell
# 方式一：还原备份
Copy-Item "<AstrBot data>\cmd_config.json.bak_connect_<时间戳>" "<AstrBot data>\cmd_config.json"

# 方式二：WebUI 里把默认模型切回原来的 provider
```

中枢侧想撤销授权：从 `data/clients.json` 删掉 `astrbot` 客户端即可（但会同时关掉它的 Key 校验）。

---

## 5. 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| `中枢不可达` | 中枢没启动 / 端口不对 | 先 `python console/server.py 18895`；或 `--hub` 指定地址 |
| `没找到 AstrBot 的 data 目录` | 装在非常规位置 | `--astrbot-dir` 指定（找里面有 `cmd_config.json` 的目录） |
| 选了奇怪的 IP（如 `10.x`） | 有 ZeroTier/VPN 网卡 | `--hub 192.168.x.x` 手动指定局域网地址 |
| AstrBot 重启后仍用旧模型 | 配置未生效 | WebUI → 配置 → 服务提供商，确认 `anan_hub_main` 已启用并设为默认 |
| QQ 发消息没反应 | AstrBot 到中枢不通 | 容器内测：`docker exec -it astrbot curl http://<IP>:18895/v1/models` |
| 想用中文路径双击 | bat 已设 UTF-8 | 若仍乱码，改用 PowerShell 执行 py 脚本 |

---

## 6. 和"解耦"的关系

这个连接器解决的是**接入体验**：中枢本身就是标准 OpenAI 兼容网关，任何客户端只要把 `base_url` 指到 `/v1` 就能用；连接器只是替用户把这一步自动化。

所以：

```text
换掉 AstrBot = 把新框架的 OpenAI Provider 指向同一个 /v1，中枢零改动
多框架并存   = 各自指向 /v1，用 X-Channel / X-Platform 区分来源
```

唯一绑 AstrBot 的是两个可选件：`astrbot_plugin_hub_tool_executor`（执行器插件）和
`console/mcp_hub_tools.py`（薄壳 MCP）。它们都只通过 HTTP 与中枢通信，不带中枢内部依赖；
不装它们，P1–P3 的裁剪与清洗照样工作。
