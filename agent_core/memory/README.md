# 记忆子系统（现状说明）

> 本文件描述**代码实际行为**，不是设计愿景。2026-11-01 审计后按真实调用链重写。
> 设计讨论与历史方案见 [../../docs/MEMORY_LAYERS.md](../../docs/MEMORY_LAYERS.md)；
> 审计结论见 [../../docs/FUNCTION_REVIEW_20261101.md](../../docs/FUNCTION_REVIEW_20261101.md)。

## 一、/v1 请求实际用到什么

```text
请求
 ├─ 身份 / 档案解析        profile/archives.py
 ├─ 时间轴整段注入          server._collect_recall_memories → memory/timeline_curve.py
 ├─ 长期事实 + 图关系       memory/facts.py::FactStore.prompt_lines / graph
 ├─ 指令式深时检索          server._run_memory_lookup → store.search_narratives（LIKE）
 ├─ 回复写回               server._gateway_record → store.append_event / save_detail / 好感度
 └─ 换页                  store.release_tail（超出 timeline_keep_chars 的旧事件搬进 buffer）
```

深时检索（`store.search_narratives`）默认排除 `visibility='archived'`，
但中枢自己的检索路径统一带 `include_archived=True`——换页搬走的旧事件仍然能翻到，
只是不再占用常驻上下文预算。

## 二、分层与表

| 层 | 存储 | 现状 |
|---|---|---|
| L1 调用索引 | `invocations` / `plugin_activities` | `invocations` 每轮都写；`plugin_activities` **无生产写入方** |
| L2 经历 | `agent_timeline` | 主路径；`segment_id` 由聚合器补 |
| L3 原文 | `memory_details` | 只写不读（没有详情查询接口，也没有清理） |
| 事实 | `user_facts` | 主路径；`FactStore` 与 `FactManager` 两套读写 |
| 摘要 | `memory_digests` | 只写不读（仅归档） |
| 缓冲 | `timeline_buffer` | 换页用；回填条件很少成立 |

旧表 `memory_facts` / `memory_entities` / `memory_relations` 仍会被 `FactStore._ensure_schema()`
创建，但从未读写，属于待清理的 schema 噪音。

## 三、哪些能力没有接线（不要再按旧文档开发）

| 能力 | 代码 | 状态 |
|---|---|---|
| 三维拟人召回 + 单模块配额 | `episodic_stream.recall_timeline` | 只有测试调用；主路径不用 |
| 向量相似度 | `embedding.py` + `episodic_stream._score_candidates` | 写入侧算 embedding，查询侧无调用 |
| jieba + FTS5 检索 | `facts.FactStore.search_episodes` | 只有测试调用；索引仍在写入时维护 |
| 长程日记检索 | `reflection_engine.recall_digests` | 无生产调用 |
| MemoryTools（Letta/MemGPT 风格工具） | `memory_tools.py` | **不存在** |

如果要把其中任何一项接回主路径，先补「真实调用点 + 端到端测试」，再更新本文档。

## 四、后台线程

- `aggregator.run_forever`：默认 30s 一轮，把未归段的 invocation 压成一条 L2 摘要。
- `MemoryHub().consolidate_episodes`：每小时一轮，写 `memory_digests`。
- 向量回填：每小时把未嵌入事件补一次 embedding。

## 五、改代码时的约定

1. 一个事实只写一处：新代码统一走 `FactStore`（`user_facts`）。
2. 召回走 `search_narratives`，需要旧事件就传 `include_archived=True`；
   不要另起一套打分/检索，除非同时替换主路径。
3. 换页只动 `timeline_buffer`，不要再引入第二套 `timeline_swap` 纯函数。
4. 新增设置项必须同时有消费方，避免再出现「面板能调、代码不读」的参数。
