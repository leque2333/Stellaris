# 存储层说明（Storage Layer）

> 目标是让「数据库」不再和中枢业务代码焊死：SQL 只出现在 `agent_core/storage/`，
> 业务层只依赖 `Store` 接口，未来可平滑换 PostgreSQL 或独立数据库服务。

## 1. 目录

```
agent_core/storage/
├── __init__.py        # get_store() / create_store() / reset_stores()
├── config.py          # HUB_STORAGE_URL 解析、默认路径探测
├── repositories.py    # Store 抽象接口（业务层只认它）
├── schema.py          # 版本化迁移 + 迁移前自动备份
├── sqlite_store.py    # SQLite 实现（唯一允许出现 SQL 的地方）
└── migrations/        # 预留：后续可放独立 SQL 文件
```

## 2. 配置

默认：`<项目根>/data/memory.db`。

```powershell
# 指定库文件
$env:HUB_STORAGE_URL = "sqlite:///D:/机器人/data/memory.db"

# 只读模式（控制台/分析进程）
$env:HUB_STORAGE_URL = "sqlite:///D:/机器人/data/memory.db?mode=ro"

# 兼容旧变量（仅当 HUB_STORAGE_URL 未设置时生效）
$env:MEMORY_HUB_DB_PATH = "D:/机器人/data/memory.db"
```

业务代码不要自己拼 SQLite 路径，统一：

```python
from agent_core.storage import get_store

store = get_store()
store.append_event(...)
```

## 3. 数据库版本

| 版本 | 内容 |
|---|---|
| v1 | 基线五表：`agent_timeline` / `user_profiles` / `user_facts` / `memory_digests` / `token_usage` |
| v2 | 时间线加 `plugin_id`、`tag_primary`、`tag_secondary`、`segment_id`、`started_at`、`ended_at`、`visibility`；新增 `plugin_activities`、`timeline_segments`；`token_usage` 加 `cost_usd/source`；`token_usage_ledger` 表迁移为兼容视图 |
| v3 | 老事件按 `source_module + metadata` 回填两级标签（QQ / B站 / 宿主机 / 终端） |

- 启动时自动执行未应用的迁移；**迁移前自动 `backup()` 到 `backups/pre_migrate_*.db`**。
- 只做加法：不删列、不重写历史数据。
- 重复执行安全（`schema_migrations` 记录版本 + checksum）。
- 查看版本：`store.schema_version()`。

## 4. 两级标签

`agent_timeline.tag_primary` 是一级标签（插件展示名），`tag_secondary` 是二级标签（由插件性质决定）：

| 来源 | 一级 | 二级 |
|---|---|---|
| QQ | `QQ` | 群聊名称；私聊为对方昵称 |
| B站 | `B站` | UP 主名 / 「巡检」 |
| 宿主机 | `宿主机` | 任务名 / 动作 |
| CLI | `终端` | `CLI` |
| 通用网关 | `通用` | 通道名 |

新插件写入时显式带 `plugin_id / tag_primary / tag_secondary`；老数据由 `classify_source()` 回填。

## 5. 活动与会话段（复杂事件聚合）

- 插件细粒度动作先写 `plugin_activities`（暂存区，不进时间轴）。
- 聚合器把同一 `(plugin_id, context_id)` 的一段持续活动结算成 **一条** `agent_timeline` 事件，带 `segment_id` 与起止时间。
- 会话段状态在 `timeline_segments`（`open` / `closed`），聚合规则：
  - 显式 `session_end`
  - 静默超时（默认 10 分钟）
  - 最大时长（默认 2 小时）
  - 上下文切换
- 聚合器尚未实现（下一步），存储接口已就绪：
  `create_segment / touch_segment / close_segment / list_stale_segments`。

## 6. Token 账本

统一写 `token_usage`（`cost_usd`、`source` 字段）。`token_usage_ledger` 是只读兼容视图，
列名与旧控制台一致（`model_id / input_tokens / output_tokens / cache_tokens / source / cost_usd`）。

## 7. 测试

```powershell
cd D:\机器人
python -m unittest tests.test_storage -v
```

用例全部先复制 `D:\机器人_完整版\data\memory.db` 到临时目录再迁移，不会改动生产库。

## 8. 换后端（预留）

实现 `Store` 接口即可，例如 `agent_core/storage/postgres_store.py`：

1. 新建类 `PostgresStore(Store)`，实现 `repositories.py` 中的方法；
2. 在 `storage/__init__.py` 的 `create_store()` 按 `backend` 分派；
3. 用 `HUB_STORAGE_URL=postgresql://...` 切换；
4. 业务代码、控制台、插件均无需改动。

SQLite 的方言（`PRAGMA`、`INSERT OR REPLACE`、`AUTOINCREMENT`）只存在于 `sqlite_store.py` / `schema.py`，
新后端不受其影响。
