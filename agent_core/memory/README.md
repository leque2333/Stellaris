# 助手智能体记忆中枢体系 (Agent Core Memory Architecture)

> 本模块为助手（Anan）独立智能体内核的专属记忆中枢系统，彻底摒弃传统单一流水账或粗暴向量匹配的屎山代码模式，借鉴学术界与开源前沿（Stanford Generative Agents、Mem0、Letta/MemGPT、Zep/Graphiti）的最佳实践，建立清晰分工、高内聚低耦合的现代化模块架构。

---

## 一、 架构理念与解决的核心痛点

在拟人智能体的长期交互中，传统记忆系统通常存在三大致命缺陷：
1. **上下文爆破与遗忘曲线倒挂**：所有交互流水账不加节制地塞入 Prompt，导致上下文 Token 迅速耗尽；或者仅靠相似度检索，把半年前发生的历史事件当成刚才发生的幻觉。
2. **过时事实无法自愈覆盖**：用户一旦变更偏好（例如“以前用 Windows，现在全面换成了 Mac”），传统系统会在 Prompt 中同时注入矛盾的两个事实，引发模型精神分裂。
3. **缺乏宏观经历视野**：为了防止 Prompt 超长，SQL 往往强加 `LIMIT 200` 截断，导致数周前的宏观记忆彻底丢失。

本记忆中枢通过**四层三维架构**优雅解决上述痛点：

```
                ┌──────────────────────────────────────────────┐
                │          AnanAgent 智能体内核 / 外部通道       │
                └──────────────────────┬───────────────────────┘
                                       │
                         [ 统一门面 Facade: MemoryHub ]
                                       │
        ┌──────────────┬───────────────┴───────────────┬──────────────┐
        ▼              ▼                               ▼              ▼
 ┌─────────────┐ ┌─────────────┐               ┌───────────────┐ ┌──────────────┐
 │ Episodic    │ │ FactManager │               │ Reflection    │ │ MemoryTools  │
 │ Stream      │ │ (Mem0 风格)  │               │ Engine (GA)   │ │ (Letta 风格) │
 │ 24~48h经历流│ │ 语义原子事实  │               │ 闲时反思提炼   │ │ 自主记忆器官 │
 └──────┬──────┘ └──────┬──────┘               └───────┬───────┘ └──────┬───────┘
        │               │                              │                │
        └───────────────┴──────────────┬───────────────┴────────────────┘
                                       │
                     [ 纯数据持久化底座 DAL: StorageDAL ]
                                       │
                               SQLite (WAL 模式)
        ┌──────────────────────────────────────────────────────────────┐
        │  agent_timeline  |  user_facts  |  memory_digests            │
        │  user_profiles   |  token_usage                              │
        └──────────────────────────────────────────────────────────────┘
```

---

## 二、 模块划分与职责契约

整个体系严格遵循单一职责原则（SRP），每个功能均独立成文件，严禁业务代码交叉污染：

### 1. `storage.py` (底层持久化存储底座 DAL)
* **职责定位**：纯数据访问层（Data Access Layer），专注数据库连接池、WAL 读写分离模式、事务安全及表结构创建。
* **零污染原则**：严禁引入任何 LLM 或上层业务逻辑，仅负责纯 SQL 执行与字典行映射。
* **数据迁移**：内置自动平滑迁移引擎，保障旧版数据（`anan_memory.db`、`episodic_memories`）自动并入新架构。

### 2. `episodic_stream.py` (情境经历时间轴流)
* **职责定位**：记录 24~48 小时内的连续自传经历流水（`agent_timeline`）。
* **高频模块防淹没机制 (Anti-Drowning)**：
  * **写入端会话防抖折叠 (Session Throttle)**：短时间内高频发生的同源操作（如刷短视频、连续巡查），通过 `session_window_seconds` 自动聚合到单条运行会话节点中，杜绝无意义碎数据刷屏。
  * **检索端单模块配额限制 (Max-Per-Module Quota)**：召回时每个来源渠道（`qq_chat`、`bilibili_patrol`、`pc_control`）默认最多允许占用 1 个 Top-K 槽位，彻底防止刷屏渠道霸榜。
  * **近期流跨通道多样性均衡 (Diverse Collapse)**：提取近期流背景时，自动对连续同模块经历进行情境折叠（如“连续进行了 8 次巡视”），空出名额确保不同通道（主人指令、系统状态）均有露出机会。
* **时序链前驱关联**：每条新经历自动寻找前 30 分钟内的经历切片，形成因果推进链（`prev_event_id`），支持“顺藤摸瓜”还原上下文。
* **三维拟人召回模型**：
  $$\text{Score} = \alpha \cdot \text{Similarity} + \beta \cdot e^{-\frac{\Delta t}{\tau}} + \gamma \cdot \text{Importance}$$
  * $\alpha$ 语义相关度：向量余弦距离 + 文本启发式重叠
  * $\beta$ 艾宾浩斯时间衰减：按近因半衰期（如 36 小时）指数衰减，越近发生权重越高
  * $\gamma$ 重要度加权：重大事件衰减后仍能唤醒

### 3. `fact_manager.py` (语义原子事实与画像管理 - 借鉴 Mem0)
* **职责定位**：管理细粒度、强语义的用户与世界偏好档案（`user_facts` 与 `user_profiles`）。
* **语义冲突自愈覆盖 (Supersede)**：当用户状态更新时，标记旧事实为 `superseded`，创建新事实并建立 `supersedes_id` 指针，彻底杜绝提示词内事实冲突。
* **好感度闭环联动**：实时计算好感度（0~100）并追溯每一次变动原因（`affection_history`）。

### 4. `reflection_engine.py` (闲时反思与经历日记固化 - 借鉴 Stanford GA)
* **职责定位**：防止高频流水账无限膨胀导致上下文爆破。
* **定期聚类归档**：在闲时将发生超过 12 小时的琐碎交互聚类压缩为宏观经历日记（`memory_digests`），原事件元数据标记 `digested=True`。
* **跨周期长程检索**：当近期时间轴无法查到时，通过 `recall_digests` 跨越日/周/月视界检索，打通 `LIMIT 200` 的截断限制。

### 5. `memory_tools.py` (自主记忆维护工具箱 - 借鉴 Letta / MemGPT)
* **职责定位**：赋予大模型主动管理与自省记忆的器官能力，以原生 Tool 形式挂载。
* **核心工具集**：
  * `record_master_preference`：主人表达偏好时模型自发记录
  * `update_master_preference`：主人习惯变更时模型自发修正旧事实
  * `search_historical_digests`：面对宏观历史询问时模型自发调阅长程日记

### 6. `__init__.py` (统一中枢门面 MemoryHub)
* **职责定位**：对外部模块（`AnanAgent`、`console.backend`、`console.server`）提供一站式门面接口（Facade）。
* **100% 零修改兼容**：导出 `TimelineHub = MemoryHub` 别名，系统旧代码无需任何重构即可无缝享受新引擎红利。
* **跨通道 Token 账本**：提供 `record_token_usage` 与 `query_token_usage`，统一审计全系统推理开销。

---

## 三、 数据库表结构 (Database Schema)

| 表名 | 存储对象 | 核心字段说明 |
| :--- | :--- | :--- |
| `agent_timeline` | 高频自传经历切片 | `event_id`, `timestamp`, `source_module`, `actor_id`, `narrative`, `emotional_impact`, `importance`, `prev_event_id`, `metadata`, `embedding` |
| `user_facts` | 语义原子事实库 | `fact_id`, `user_id`, `category`, `content`, `status` (`active`/`superseded`), `supersedes_id`, `created_at`, `updated_at` |
| `memory_digests` | 周期反思经历日记 | `digest_id`, `date_str`, `timestamp`, `narrative`, `key_facts`, `events_covered_count`, `created_at` |
| `user_profiles` | 兼容长效画像档案 | `user_id`, `nickname`, `role_type`, `facts`, `updated_at`, `affection`, `affection_history` |
| `token_usage` | 算力与 Token 审计 | `id`, `timestamp`, `session_type`, `model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `call_duration_ms` |

---

## 四、 快速使用示例

### 1. 基础经历追加与拟人召回
```python
from agent_core.memory import MemoryHub

hub = MemoryHub()

# 1. 记录经历 (自动关联时序因果并联动好感度)
hub.append_event(
    source_module="qq_chat",
    actor_id="master",
    narrative="主人今天夸奖助手代码结构写得很清晰",
    emotional_impact=+2,
    importance=2.0
)

# 2. 三维拟人召回
recalls = hub.recall_timeline(query="代码结构", actor_id="master", limit=2)
for r in recalls:
    print(f"[{r.event.time_str}] 得分:{r.score} - {r.event.narrative}")
```

### 2. 原子事实覆盖与提示词生成
```python
# 1. 新增事实
fact_id = hub.facts.add_fact("master", "目前使用 Windows 运行宿主机", category="environment")

# 2. 偏好发生变动 -> 自动覆盖
hub.facts.supersede_fact(fact_id, "目前已全量迁移至 Linux 容器环境")

# 3. 提取用于 Prompt 的干净事实切片 (不会产生前后矛盾)
facts_for_prompt = hub.facts.get_facts_for_prompt("master")
print(facts_for_prompt) # 仅保留最新激活事实
```

### 3. 闲时经历反思聚类
```python
# 将 12 小时以前的碎片事件聚类沉淀为日记
digest = hub.consolidate_episodes(min_events=5, older_than_hours=12.0)
if digest:
    print(f"成功压缩沉淀: {digest['summary']}")
```
