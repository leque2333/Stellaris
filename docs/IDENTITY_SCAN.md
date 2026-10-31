# 身份线索扫描（只罗列，不判断）

> 目的：把所有插件里像 UID 的值**全部找出来罗列好**，由人决定哪个 UID 是谁。
> 中枢不猜群/人/主人，不自动建档，不自动绑定。

---

## 1. 为什么这么设计

不同插件的身份格式完全不一样：

```text
astrbot_plugin_bilibili   config.json → bilibili.dedeuserid / master_uid
AstrBot 本体              cmd_config.json → admins_id / id_whitelist
AstrBot 数据库            data_v4.db → umo_aliases / conversations / platform_message_history
第三方插件                什么都可能有：yaml / toml / 自己的 sqlite / 环境变量
```

要求所有插件作者写统一身份卡不现实，所以反过来：**中枢用宽口径把可疑的 ID 都挖出来，人来做最后判断**。

---

## 2. 怎么用

### 命令行

```powershell
# 表格（默认扫描项目 modules/、astrbot_plugins/、AstrBot data/）
python scripts\scan_identities.py

# 用生产库判断"哪些已经配过"
python scripts\scan_identities.py --db "D:\Stellaris_test\data\memory.db"

# 只看指定目录，多个用分号
python scripts\scan_identities.py --roots "D:\AstrBot\data;D:\my_plugin"

# 输出 JSON（方便接前端或脚本处理）
python scripts\scan_identities.py --json
python scripts\scan_identities.py --out ids.json
```

### 控制台 API

```text
GET /api/identity/scan            # 需登录，返回和命令行一致的 JSON
GET /api/identity/scan?roots=...  # 指定扫描目录
```

返回结构：

```json
{
  "ok": true,
  "roots": ["D:\\Stellaris\\modules", "..."],
  "scanned_files": 25,
  "total_sources": 27,
  "total_uids": 11,
  "configured": 2,
  "unconfigured": 9,
  "hits": [
    {
      "uid": "<B站UID>",
      "platform_hint": "bilibili",
      "source_count": 8,
      "configured": true,
      "archive_id": "arc_master",
      "configured_via": "identity",
      "sources": [
        {"kind": "file", "path": "...\\config.json", "field": "bilibili.master_uid",
         "raw": "<B站UID>", "plugin": "astrbot_plugin_bilibili"}
      ]
    }
  ]
}
```

---

## 3. 输出怎么读

| 字段 | 含义 |
|---|---|
| `uid` | 规范化后的纯 ID（`onebot_napcat:FriendMessage:<你的QQ>` → `<你的QQ>`） |
| `platform_hint` | **只是提示**，从插件名/路径猜的，`unknown` 很正常，以来源字段为准 |
| `source_count` | 这个 UID 在几处出现过；越多越可信 |
| `configured` | 中枢的档案/绑定里是否已经有它（查的是 `--db` 指定的库） |
| `sources[]` | 每条线索的出处：文件 / 数据库表列 / 字段名 / 原始值 / 所属插件 |

---

## 4. 扫描规则（宽口径）

**扫什么**

```text
文本配置：.json .yaml .yml .toml .env .ini
数据库：  .db .sqlite .sqlite3
```

**怎么算候选**（键名 + 值形态两个条件同时满足）

```text
键名含：uid / user_id / qq / uin / account / owner / master / admin /
        whitelist / allow_list / friend / group / member / contact / sender / umo
值形态：5~12 位纯数字，或 onebot_napcat:FriendMessage:<你的QQ> 这种带前缀 ID
```

**明确排除的噪声**（避免 `max_tokens: 12000` 被当成 UID）

```text
token / timeout / port / size / length / limit / count / max / min /
interval / version / days / hours / pages / score / temp / price / quota
```

> 排除规则用下划线边界匹配——否则 `admin` 里的 `min` 会被误伤成噪声。

---

## 5. 控制台「身份映射」页（推荐）

```text
侧边栏 → 身份映射（也可以直接访问 /#identity）
```

**分组名从哪来**：优先用插件目录名；根目录文件（`cmd_config.json` / `data_v4.db`）没有插件名，
就读同目录 `cmd_config.json` 里的 `platform[].id`——当前是 `onebot_napcat`，显示成 **NapCat**。

```python
HOST_LABELS = {
    "onebot_napcat": "NapCat", "onebot": "OneBot", "aiocqhttp": "NapCat / OneBot",
    "bilibili": "B站", "telegram": "Telegram", "discord": "Discord", "lark": "飞书",
}
```

以后 AstrBot 加了 Telegram，会多出一个 `Telegram` 组，不用改代码。

页面结构：

```text
┌─────────────────────────────────────────────────────────┐
│ astrbot_plugin_bilibili   8 个 UID · 已配 0   [选档案 ▾] │  ← 整组选一个档案
├─────────────────────────────────────────────────────────┤
│ ▸ 展开                                                   │
│   <B站UID>  bilibili  8 处  待配        [选档案 ▾]      │  ← 单独调整
│   ...                                                   │
└─────────────────────────────────────────────────────────┘
```

操作：

1. 打开页面自动扫描；也可以点右上角「重新扫描」全量扫。
2. **整组配置**：右侧下拉选一个档案 → 该插件下所有 UID 都填成它。
3. **单独配置**：点 `▸` 展开，逐行改。
4. 点「保存映射」一次性提交。

### 隐藏 UID 与按插件重扫

每行末尾有「隐藏」按钮，隐藏后不再显示（摘要里会出现「已隐藏 N」和「显示已隐藏」开关）。

**隐藏只是展示层过滤，数据没丢**，两种方式能找回来：

```text
① 勾选「显示已隐藏」→ 立刻看到（灰色行，带「显示」按钮恢复）
② 点该插件右侧的「重扫」→ 只重扫这个插件，并把该插件下隐藏的 UID 全部恢复
```

「重扫」是**以插件为单位**的：只重新扫描这一个插件的目录，不影响其它插件，也不会动你还没保存的映射选择。

隐藏记录存在 `data/identity_hidden.json`：

```json
{"version": 1, "hidden": ["<群号>", "<QQ>"]}
```

想一次性全部恢复，删掉这个文件或清空 `hidden` 数组即可。

写入两处，保证两条链路都生效：

```text
archive_identities   档案页可见 + 白名单生效
platform_bindings    请求期 resolve_archive_for_sender 用
```

平台字段取扫描的 `platform_hint`，`unknown` 时按 `qq` 处理（页面会显示）。

### 直接调 API

```text
# 绑定
POST /api/identity/bind
{"items": [{"platform": "qq", "uid": "<你的QQ>",
            "archive_id": "arc_master", "display_name": "主人"}]}

# 隐藏 / 显示
POST /api/identity/hide
{"uids": ["<群号>"], "hidden": true}

# 按插件重扫（并恢复该插件下隐藏的 UID）
GET /api/identity/scan?plugin=onebot_napcat&reset_hidden=1

# 只扫某个插件，不恢复隐藏
GET /api/identity/scan?plugin=bilibili
```

绑定返回逐条结果，失败的不会影响已成功的，可重跑。

---

## 6. 已知边界

1. **没有身份字段的插件扫不出来**。比如 `astrbot_plugin_group_observer` 的 `metadata.yaml` 里没有任何 UID——中枢不能凭空造，这类插件只登记为 host，身份靠运行时观测或人工补。
2. **`platform_hint` 会经常是 `unknown`**。根目录的 `cmd_config.json` / `data_v4.db` 路径里没有平台名。这是有意的：宁可标 unknown，也不瞎猜。
3. **只读**。扫描不写任何表，跑多少次都不会改数据。
4. **大文件跳过**（> 5MB），避免扫到二进制或缓存。

---

## 7. 测试

```powershell
python -m unittest tests.test_identity_scan -v
```

8 项：三种格式（JSON/YAML/SQLite）、噪声排除、同 UID 合并、前缀规范化、
已配置标记、空目录、只读性。
