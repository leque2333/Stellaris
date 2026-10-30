# 档案画像：事实 / 好感度 / 设定

> 档案是"一个人"的画像。这里定清楚三样东西的边界：
> **长效事实**（AI 总结）、**好感度**（浮动窗口）、**自定义设定**（用户手填）。

---

## 1. 长效事实

### 唯一事实表：`user_facts`

```text
事实只有一张表，不再有第二份拷贝。
写入：FactManager.add_fact() / ProfileManager.add_fact()
读取：ProfileManager.get_facts_for_prompt() → 网关提示词
      ProfileManager.get_active_facts()    → 档案页
```

### 来源（`source` 列）

| 值 | 含义 | 谁写 |
|---|---|---|
| `ai_extract` | AI 从对话里总结 | 网关异步抽取 |
| `manual` | 人工在控制台填的 | 控制台 API |
| `legacy` | 历史迁移过来的 | 迁移脚本 |

```json
// 事实行长这样
{"fact_id": "fact_...", "user_id": "arc_master", "category": "preference",
 "content": "喜欢二次元与技术探索", "status": "active", "source": "legacy"}
```

### 抽取链路

```text
消息结束
  → FactStore.store_extracted(use_ai=False)   规则抽取
  → 没抽到 → 异步 FactStore.store_extracted(use_ai=True)   AI 抽取
       ↓
  ProfileManager.add_fact(source="ai_extract")
       ↓
  user_facts（status=active）
```

> 修复前：抽取结果写进 `memory_facts`，但那表**没有任何人读**，所以 AI 总结从没生效过。
> 现在统一写 `user_facts`，网关也从这里读。

### 冲突处理

```text
同一用户同一 category 出现新事实：
  旧事实 status='superseded'，新事实 supersedes_id 指向旧的
  历史仍可查（status != active）
```

---

## 2. 好感度：每档案一个浮动窗口

### 存储

```sql
archives.affection_min INTEGER DEFAULT 0
archives.affection_max INTEGER DEFAULT 100
user_profiles.affection          当前值
user_profiles.affection_history  变化历史
```

### 规则

| 点 | 行为 |
|---|---|
| 范围 | `0 ≤ min ≤ max ≤ 100`，不满足直接拒绝 |
| AI 加减 | `new = clamp(old + delta, min, max)` |
| 到边界 | delta 无效，但历史仍记一条（`range: [lo, hi]`） |
| 收窄窗口 | 保存范围后自动把当前值夹回新窗口 |
| 新档案 | **未定（None）**，显示 `?`，不预设 50/90 |
| 首次交流后 | AI 根据这次对话给一个初始值，clamp 到窗口 |
| 手动设定 | 好感度模块里可以直接填值（`set_affection`） |

### 在档案卡里

好感度直接在档案详情里（不单独开页面）：

```text
好感度
[░░░░████████████████] ●
[60] ~ [100]  [保存范围]

0~60 灰   60~100 紫   94 处圆点
```

- 改数字**实时预览**，点「保存范围」落库
- 还没交流过的档案：条上不画圆点，右侧显示 `?`
- 好感度的**规则**（什么行为加多少分、等级描述）属于好感度系统，后续放进「人格设定」

### 好感度自动评估（方案 A）

不写死规则、不限制模型表现：每轮交流结束后，用一个轻量调用判断这次交流的
好感变化，delta 由模型自己给。

```text
消息结束
  → 异步（线程，不阻塞回复）
  → 档案未定值：给初始好感 0-100 → set_affection
  → 已有值：给 delta(-10~10) + 理由 → update_affection（clamp 到窗口）
  → delta = 0 或解析失败 → 不写库
```

节流与开关：

```text
HUB_AFFECTION_AI=0        关闭自动评估
HUB_AFFECTION_AI_SLOT     指定模型槽，默认 logic_slot（DeepSeek 执行凭证）
min_interval              默认 60 秒内最多评估一次（防连发刷好感）
```

实测（真实模型）：

```text
"你真是个笨蛋，什么都做不好"  → (-6, "对方言辞攻击并否定贬低")
"今天好累，不过想到你在就还好" → (+2, "表达了对自己的依赖与想念")
"在吗"                      → 0（不写库）
```

### 首次交流的初始好感

```text
消息结束 → 发现 affection 为 None
        → 异步调模型："你对这个人的初始好感度是多少？只回 0-100"
        → set_affection(value, reason="首次交流，AI 给出初始好感")
        → 失败就保持未定，下次再试
```

只用 `fact_slot` 模型，单个档案只做一次。

### 为什么要有窗口

```text
主人（主人）  60 ──────── 100   当前 94
                AI 再怎么闹也不会掉到 60 以下

新客人        0 ─── 50          当前 10
                聊得再好也不会一夜变知己
```

进度条读法：

```text
[░░░░░░░░████████████████████] ●
 └── 灰 ─┘└──── 紫色（可用窗口）────┘
   0~60            60~100            94 处一个圆点 = 当前值
```

- **灰色** = 窗口外，AI 改不动
- **紫色** = 窗口内，AI 可以在这个区间浮动
- **圆点** = 当前好感度落在窗口里的位置
- 改上面的数字会**实时预览**，点「保存范围」才落库

好感度系统（AI 自主加减）只负责 `delta`，窗口由档案管，两边不抢。

### 历史记录

```json
{"time": 1793327818.6, "delta": 1, "old": 90, "new": 91,
 "reason": "辛苦调试一天后向我示弱诉苦", "range": [60, 100]}
```

---

## 3. 自定义设定

### 和长效事实的区别

| | 长效事实 | 自定义设定 |
|---|---|---|
| 来源 | AI 从对话总结 | 你手填 |
| 会变吗 | 会，AI 可 supersede | 不会，只有你改 |
| 谁写 | `ai_extract` | 只有控制台 |
| 冲突时 | 服从设定 | 优先 |

### 存储

```sql
CREATE TABLE archive_settings (
    setting_id TEXT PRIMARY KEY,
    archive_id TEXT NOT NULL,
    label TEXT NOT NULL,        -- 标签，如「称呼」
    content TEXT NOT NULL,      -- 内容，如「主人」
    sort_order INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
```

### 用法

档案页里是一个**大文本框**，每行一条：

```text
称呼：主人
雷点：别用敬语
喜欢深夜写代码
```

- 有冒号 → 冒号前当标签，后面当内容
- 没冒号 → 整行当内容（标签记为「设定」）
- 点「保存设定」整段替换（不是追加）
- **允许留空**：清空文本框保存 = 删掉这个档案的全部设定

API：

```text
# 整段替换（推荐，前端就用这个）
POST /api/archives/<archive_id>/settings
{"text": "称呼：主人\n雷点：别用敬语"}

# 单条增改
POST /api/archives/<archive_id>/settings
{"label": "称呼", "content": "主人"}

# 删除单条
DELETE /api/archives/<archive_id>/settings?setting_id=set_xxx
```

---

## 4. 数据清理

旧的 QQ 号画像和档案 ID 画像会存两份。合并脚本：

```powershell
# 预览
python scripts\merge_legacy_identities.py --db data\memory.db

# 执行（自动备份）
python scripts\merge_legacy_identities.py --db data\memory.db --apply
```

做的事：

```text
1. 旧 QQ 号下的事实 → status='superseded'（不删，保留痕迹）
2. 旧 QQ 号的 profile → 好感度取较高值合并，删除旧行
```

---

## 5. 迁移

| 版本 | 内容 |
|---|---|
| v25 | `archives.affection_min/max`、`user_facts.source`、`archive_settings` 表 |

迁移是幂等的，重复跑不会出错。

---

## 6. 测试

```powershell
python -m unittest tests.test_archive_profile -v
```

18 项：默认窗口、clamp、越界拒绝、历史记录、收窄窗口、设定增删改/隔离/排序、
事实来源、supersede 保留来源、迁移列存在、迁移幂等。
