# 记忆库导出 / 导入

> 更新：2026-11-01  
> 入口：设置 → 记忆调参 → 记忆库维护

## 1. 原生格式

```json
{
  "format": "stellaris.memory.v1",
  "exported_at": 1793465000,
  "tables": {
    "agent_timeline": [],
    "memory_details": [],
    "user_facts": [],
    "memory_digests": [],
    "timeline_buffer": [],
    "user_profiles": []
  }
}
```

- 保留事件、L3 原文、长期事实、摘要、缓冲区和档案画像
- 不导出 embedding BLOB（导入后由检索侧重新生成/留空）
- 不含 API Key、客户端配置、日志
- 合并导入：`INSERT OR IGNORE`，ID 冲突跳过
- 替换导入：先清空记忆表再写入

## 2. 外部格式（尽力转换）

| 格式 | 识别 | 处理 |
|---|---|---|
| `jsonl` | 每行一个 JSON | `role/content` 或 `text` → 时间轴事件 |
| `chatgpt` | `conversations.json` 带 `mapping` | 每个消息 → 时间轴事件，最多 2000 条 |
| `text` | 普通文本 / Markdown | 空行分段 → 时间轴事件 |

外部格式导入的事件：

```text
source_module = import_*
tag_primary   = import
tag_secondary = import
tag_permission = system
```

不承诺保留原始会话结构、工具调用或图片。

## 3. 安全

- 导入前自动备份：`data/backups/memory_before_import_*.db`
- 单次上限 50MB
- 原生表名白名单
- 导入在 SQLite 事务内执行
- 写审计事件 `memory_import`
- `replace` 模式需要前端二次确认

## 4. 占用空间

「记忆库现状」显示：

```text
数据库 = memory.db + -wal + -shm
```

WAL 文件可能临时变大，导入/大量写入后会回落。
