# 记忆模块架构

> 更新：2026-11-01  
> 代码：`agent_core/memory/`、`agent_core/storage/`、`console/modules/system_service.py`  
> 页面：设置 → 记忆库

## 1. 分层

```text
L1  invocations        单次请求 / 调用的原始上下文
L2  agent_timeline     可读的连续经历，带插件 / 档案 / 权限标签
L3  memory_details     长原文、附件、工具结果等大块内容
    user_facts         长期事实
    memory_digests     按天 / 按段的摘要
    timeline_buffer    超出常驻预算后移出的旧事件
    archives           人物档案与角色
    platform_bindings  平台 UID → 档案
```

L1 是原始层，L2 是主 AI 读到的“发生过什么”，L3 是原文与事实；四者不是同一张表。

## 2. 写入链路

```text
客户端 /v1 请求
  → 鉴权 + 平台 UID
  → 身份解析：platform + uid → archive_id
  → 模型回复
  → 写 agent_timeline（L2）
  → 需要时写 memory_details（L3）
  → 抽取 user_facts
  → 后台聚合器生成 memory_digests
```

时间轴事件至少包含：

```text
event_id
timestamp / time_str
source_module
actor_id / actor_name
narrative
person_id
metadata
tag_primary      插件 / 工具名
tag_secondary    发起人档案 ID；没有档案时才用 UID
tag_permission   admin / guest / system / unknown
visibility
```

## 3. 标签与身份

存储层保留三列：

| 列 | 用途 |
|---|---|
| `tag_primary` | 插件 / 工具名 |
| `tag_secondary` | 档案 ID（`arc_master`）或回落的 UID |
| `tag_permission` | 档案 role |

展示层拆成独立标签：

```text
[web] [qq] [乐阙]        插件 / 平台 / 人员
```

- 有档案：人员标签用 `archives.display_name`，按 role 着色
- 无档案：人员标签回落 UID
- 权限不再单独挂徽标，直接体现在人员颜色
- 平台标签有值才显示

身份解析顺序：

```text
platform_bindings（显式绑定）
  → archive_identities（平台身份）
  → archives.role
  → guest / unknown
```

`metadata` 里同时写 `platform`、`sender_uid`、`archive_id`，老数据靠来源和会话推断。

## 4. 读取链路

```text
请求
  → 取当前档案
  → 时间轴按预算注入（HUB_TIMELINE_BUDGET）
  → 超出常驻上限的旧事件移入 timeline_buffer
  → 需要更早记忆时走召回 / deep_recall
  → 事实、画像、关系按需拼接
```

读取原则：

- 时间轴优先，事实是补充
- 预算由 `memory_settings` 控制
- 召回结果可以回写时间轴，但不会覆盖原始事件
- 归档事件（`visibility=archived`）不进活跃统计

## 5. 聚合与摘要

`agent_core/memory/aggregator.py` 定期扫描：

- 有要点的片段 → `memory_digests`
- 纯模块调用记录 → 审计 / 聚合，不强行进时间轴
- 摘要保留来源事件数量、时间范围、关键事实

聚合器读取 `timeline_window` 等设置；修改记忆参数下一轮扫描生效。

## 6. 记忆库维护

页面：设置 → 记忆库 → 记忆库维护。

- 当前占用 = `memory.db + -wal + -shm`
- 导出原生 JSON：`stellaris.memory.v1`
- 导入：原生 / JSONL / ChatGPT 导出 / 纯文本
- 合并或清空后导入
- 导入前自动备份到 `data/backups/memory_before_import_*.db`

原生格式保留：

```text
agent_timeline
memory_details
user_facts
memory_digests
timeline_buffer
user_profiles
```

不导出 embedding、密钥、客户端配置。外部格式只做尽力转换。

## 7. 设置项

`data/memory_settings.json`：

| 键 | 作用 |
|---|---|
| `timeline_budget_chars` | 每轮时间轴注入预算 |
| `timeline_keep_chars` | 常驻时间轴上限，超出搬进缓冲区 |
| `recall_max_chars` | 单次回忆最多注入多少字 |
| `candidate_chars` | 召回候选池字数 |
| `candidate_limit` | 召回候选条数安全阀 |
| `lookback_days` | 召回回看天数，0 表示不限 |
| `half_life_hours` | 近因衰减半衰期 |
| `aggregator_idle_seconds` | 聚合器空闲间隔 |
| `aggregator_max_duration_seconds` | 单轮聚合最长执行时间 |
| `aggregator_interval_seconds` | 聚合器轮询间隔 |
| `memory_tool_enabled` | 是否允许模型调用记忆工具 |
| `prefetch_enabled` | 是否预取记忆块 |

环境变量优先于面板；被环境变量接管的项会显示为锁定。

## 8. 相关文件

```text
agent_core/models.py                    TimelineEvent
agent_core/storage/schema.py            schema 与迁移
agent_core/storage/repos/timeline.py    时间轴读写
agent_core/memory/aggregator.py         摘要聚合
agent_core/memory/facts.py              事实 / FTS
agent_core/memory/portability.py        导出 / 导入
agent_core/memory/settings.py           预算与统计
console/modules/system_service.py       控制台时间轴 API
console/modules/token_alerts.py         Token 预警
console/static/js/timeline.js           时间轴渲染
console/static/js/memory_tuning.js      记忆库页面
```

## 9. 边界

- 中枢只负责记忆结构与读取，不负责平台协议
- 工具执行结果仍归外部宿主；中枢只记录“请求了什么工具”
- 记忆库导入不合并档案、客户端、密钥，避免覆盖身份配置
- 任何写入失败都降级，不阻塞对话
