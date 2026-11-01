# 账本查询系统工具

> 更新：2026-11-01  
> 工具名：`hub_ledger_query`  
> 位置：工具页 → 系统工具

## 1. 定位

把账本只读地暴露给 AI，让主 AI 能回答：

- 今天花了多少、用了多少 token
- 最近几笔调用是什么
- 最近几天的消耗趋势
- 哪个模型/凭证花得多

不提供改价、删除、充值能力；那些仍走控制台。

## 2. 动作

```json
{"action": "summary"}
{"action": "recent", "limit": 10}
{"action": "daily", "days": 7}
```

返回：

- `summary`：today / overall / 最近 7 天
- `recent`：时间、模型、输入/缓存/输出 token、金额、来源、凭证类型
- `daily`：日期、调用数、tokens、金额

## 3. 权限

- 仅管理员档案身份
- 系统工具页可单独停用
- 只读 SQLite 连接，不写账本
- 每次调用写审计 `ledger_query`

## 4. 注入方式

和 `hub_host_control` 一样：

1. 网关在工具列表末尾追加 `hub_ledger_query`
2. 模型返回 tool_call
3. 中枢本地执行，把结果作为 tool 消息回给模型
4. 最多 2 轮，可用 `HUB_LEDGER_TOOL_ROUNDS` 调整
5. `HUB_LEDGER_TOOL=0` 可整体关闭

## 5. 兼容

- 不依赖 AstrBot
- 工具页自动显示为「系统工具 / hub-native」
- 非管理员客户端看不到这个工具
