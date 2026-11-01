# 宿主机电脑控制

> 更新：2026-10-31  
> 范围：Windows 宿主机；仅管理员可用；当前允许完整 PowerShell。

## 1. 两个入口

```text
AstrBot（主入口）
  control_host_pc_via_antigravity
    → host_bridge /execute
    → Windows PowerShell / 截图 / 锁屏

中枢（备用入口）
  hub_host_control
    → 中枢进程直接执行 PowerShell / 截图 / 锁屏
```

主入口不可用时，主 AI 可以改用中枢备用；两边都不依赖同一个进程。

## 2. AstrBot 主入口

插件目录：

```text
astrbot/data/plugins/astrbot_plugin_host_control/
```

配置项：

| 配置 | 默认 | 说明 |
|---|---|---|
| `bridge_url` | `http://192.168.3.4:8765` | AstrBot 在 Docker 里，不能写 127.0.0.1 |
| `bridge_token` | 与 bridge 配置一致 | `X-Bridge-Token` |
| `admin_id` | 管理员 QQ | 双重校验 |
| `bridge_timeout` | 70 秒 | HTTP 超时 |

工具参数：

```json
{"action": "status | powershell | screenshot | lock | auto",
 "command": "PowerShell 命令",
 "instruction": "自然语言操作（auto）",
 "timeout": "60"}
```

## 3. 宿主机 Bridge

目录：

```text
host_bridge/
```

启动：

```powershell
cd D:\Stellaris_test\host_bridge
py server.py
```

或双击 `start_bridge.bat`。健康检查：

```text
GET http://127.0.0.1:8765/health
```

配置在 `host_bridge_config.json`；`data_temp_dir` 指向测试端 AstrBot 的 `data/temp`。
Bridge 用 `X-Bridge-Token` + `admin_id` 两道校验；当前允许完整 PowerShell。

## 4. 中枢备用入口

- 工具名：`hub_host_control`
- 仅 `tool_permissions.role=admin` 且客户端在 `admin_clients` 里时注入
- 动作：`status / powershell / screenshot / lock`
- 每轮默认最多 2 次，`HUB_HOST_CONTROL_ROUNDS` 可调，最高 4
- 关闭：`HUB_HOST_CONTROL=0`
- 每次调用写 `audit_log` 的 `host_control` 事件：人、客户端、action、命令、结果

实现：`agent_core/host_control.py`；网关注入与拦截在 `console/server.py`。

## 5. 安全边界

- **管理员身份优先于客户端角色**：请求里的发送者 UID 必须通过身份映射绑定到 `archives.role=admin` 的档案（当前是 `arc_master`）；没有 UID 或映射到 guest 一律不注入宿主机工具
- 主入口 `control_host_pc_via_antigravity` 也会在工具货架层按档案身份再过滤一次；客户端 role=admin 但人不是管理员，同样看不到
- 中枢备用只对「管理员客户端 + 映射到 admin 档案的人」注入；访客看不到 `hub_host_control`
- AstrBot 侧再校验 `event.is_admin()` 和 `admin_id`
- 身份映射格式支持 `用户ID: 298...`、`(298...):`、`X-User-Id`；没有显式 UID 时不会把默认 master 当成管理员
- Bridge 监听 `0.0.0.0:8765` 时，Token 就是边界；不要外网暴露端口
- 当前是完整 PowerShell，不是白名单；误操作/提示注入都可能造成真实影响
- 建议只在受信网络开放 8765，并定期更换 `secret_token`

## 6. 验证

```powershell
# Bridge 健康
Invoke-WebRequest http://127.0.0.1:8765/health

# Bridge 执行
Invoke-WebRequest http://127.0.0.1:8765/execute -Method Post `
  -Headers @{"X-Bridge-Token"="<token>"; "Content-Type"="application/json"} `
  -Body '{"admin_id":"<admin_qq>","action":"powershell","command":"Write-Output ok"}'

# 中枢备用
py -c "from agent_core import host_control; print(host_control.execute('powershell','Write-Output ok'))"
```

