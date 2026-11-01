# 功能挂载架构

> 更新：2026-11-01  
> 目标：插件、工具、宿主能力都通过统一挂载点进入中枢，不往核心塞宿主专属代码。

## 1. 总览

```text
外部插件 / 客户端
  │ OpenAI /v1 + tools
  ▼
中枢网关
  ├─ 工具闸门：分类裁剪 + 权限过滤 + 结果压缩
  ├─ 系统工具：中枢原生能力（host / ledger）
  ├─ 动态工具申请：broker
  └─ 插件适配器：把请求归一化成 platform / uid / host
  ▼
模型 / 记忆 / 账本
```

## 2. 工具挂载

### 2.1 外部工具

外部插件把自己的工具 schema 放进请求的 `tools`：

```json
{
  "type": "function",
  "function": {
    "name": "generate_image",
    "parameters": {"type": "object", "properties": {}}
  }
}
```

中枢不执行这些工具；模型返回 tool_call 后仍由宿主插件执行。

### 2.2 工具闸门

`agent_core/tool_shelves/`：

- `gate.py`：分类裁剪、客户端权限、档案权限、粘性类别
- `broker.py`：`hub_request_tools` 动态申请
- `cleaner.py`：工具结果压缩
- `config.py`：`data/tool_shelves.json`
- `admin.py`：工具页数据与开关

失败策略统一为放行：配置坏了、分类器挂了、清洗失败，都退回全量工具，绝不把机器人弄哑。

### 2.3 系统工具

`agent_core/tool_shelves/system_tools.py` 是通用元数据表，不认 AstrBot：

```python
"hub_host_control": {
    "label": "宿主机控制（中枢备用）",
    "kind": "host",
    "transport": "hub-native",
}
```

工具页只认：

```text
label / kind / transport / enabled
```

当前系统工具：

| 工具 | 类型 | 执行位置 |
|---|---|---|
| `control_host_pc_via_antigravity` | host | AstrBot 插件 → host_bridge |
| `hub_host_control` | host | 中枢本地 PowerShell / 截图 / 锁屏 |
| `hub_ledger_query` | ledger | 中枢本地只读 SQLite |

系统工具状态写入 `data/system_tools.json`，停用后网关不再注入。

### 2.4 中枢原生工具的执行

`hub_host_control` 和 `hub_ledger_query` 不经过宿主：

```text
网关在 tools 末尾追加 schema
  → 模型返回 tool_call
  → 中枢执行
  → 结果作为 role=tool 回给模型
  → 继续生成（最多 N 轮）
```

入口在 `console/server.py`：

```text
_maybe_host_control()
_maybe_ledger_tool()
```

权限：

- 必须是管理员档案身份
- 系统工具开关必须为启用
- 每次调用写审计：`host_control` / `ledger_query`

关闭开关：

```text
HUB_HOST_CONTROL=0
HUB_LEDGER_TOOL=0
```

轮数：

```text
HUB_HOST_CONTROL_ROUNDS=2
HUB_LEDGER_TOOL_ROUNDS=2
```

### 2.5 新增系统工具的步骤

1. 在 `agent_core/` 写 `tool_spec()` + `execute()`
2. 在 `system_tools.py` 注册元数据
3. 在 `server.py` 加注入开关和执行循环
4. 加审计事件
5. 加测试
6. 工具页自动显示，不需要改前端

## 3. 插件适配器

`console/adapters/` 解决“不同插件字段名不一样”：

```text
base.py          Adapter / RequestView
registry.py      内置 + 声明式 + generated 加载
astrbot.py       AstrBot 实现
declarative/     *.json 字段映射
generated/       执行 AI 生成的 Python 适配器
```

发现顺序：

```text
X-Host-Id / X-Plugin-Chain
  → 声明式 JSON
  → generated/*.py
  → AstrBot
  → 通用嗅探
```

契约：中枢适配插件，不要求插件改协议。新插件优先用声明式；结构复杂再生成 Python 适配器。

## 4. 宿主机桥

`host_bridge` 是独立 HTTP 服务（默认 `8765`）：

```text
AstrBot 插件 ──HTTP──► host_bridge ──► PowerShell / 截图 / 锁屏
```

中枢备用入口不依赖它，直接在本机执行；两者都只对管理员开放。

## 5. 工具页

- 只展示全局工具
- 系统工具置顶
- 插件工具按 `source_group` 折叠
- 系统工具可单独启用/停用
- 个人工具权限在「档案 → 工具权限」里
- 页面只读 `system / transport / source_group`，不写 AstrBot 分支

工具来源（`tool_shelves.admin.list_tools` 的合并顺序）：

```text
tool_shelves.json        管理员登记过 mode / approval 的工具（配置优先）
system_tools             中枢原生系统工具
plugin_scan              通用插件扫描（目录可配置；见下）
registry                 网关链路里实际出现过的工具（请求 tools 数组）
```

`plugin_scan` 是**通用**扫描器，不绑定宿主：

```text
AstrBot      metadata.yaml + @filter.llm_tool
NoneBot      pyproject.toml + @tool / @llm_tool
LangBot      manifest.yaml / manifest.json
Dify         manifest.yaml + provider|tools/*.yaml 的 tools / identity.name
通用 Python  @tool / @function_tool / @register_tool / @mcp_tool / @ai_function
```

扫描目录在「设置 → 常规与系统 → 插件工具扫描」里配置（每行一个目录），
留空且勾选「自动识别」时使用默认目录（`ASTRBOT_DATA_DIR/plugins`、项目下
`astrbot/data/plugins`、`plugins` 等）。配置存 `data/plugin_scan.json`；
不存在的路径会被忽略，扫描只读、不写插件目录。

## 6. 账本工具

`hub_ledger_query` 是只读系统工具：

```json
{"action": "summary"}
{"action": "recent", "limit": 10}
{"action": "daily", "days": 7}
```

只读连接 `memory.db`，不能改价、删除、充值；改价仍走账单页。

## 7. 新增能力检查清单

- 是否必须依赖某个宿主？如果是，放适配器，不进核心
- 是否有权限边界？管理员 / 档案 / 客户端
- 是否写审计？敏感操作必须写
- 是否只读？能只读就不要给写权限
- 失败是否降级？不能因为新工具挂掉主流程
- 是否有测试？至少覆盖 success / denied / missing config

## 8. 相关文档

- [工具货架](TOOL_SHELVES.md)
- [插件接入契约](PLUGIN_ADAPTERS.md)
- [宿主机控制](HOST_CONTROL.md)
- [账本查询工具](LEDGER_TOOL.md)
- [架构总览](ARCHITECTURE.md)
