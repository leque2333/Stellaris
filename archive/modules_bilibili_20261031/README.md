# B 站独立模块

这是**独立模块**，不依赖 AstrBot，也不再是 `astrbot_plugin_bilibili` / `astrbot_plugin_human_patrol` 的插件。

它通过中枢保留的接口之一接入：

> **模块接口 = OpenAI 兼容 `/v1`**

B 站模块自己负责登录、刷视频、读评论、发评论、回回复；所有 AI 生成都调用中枢 `/v1`。  
模型 Key、人格、记忆、身份映射都在中枢，不在这个模块里。

## 自动变成中枢工具

模块提供 action server：

```text
GET  /actions
GET  /actions/status
POST /actions/recommend
POST /actions/patrol
```

`module.json` 里声明：

```json
"actions": [
  {
    "name": "recommend",
    "description": "从 B站找几个当前可能合适的视频或话题，返回标题、链接和推荐理由。只读，不评论。",
    "endpoint": "http://127.0.0.1:18901/actions/recommend",
    "risk": "read"
  }
]
```

中枢会自动生成：

```text
module__bilibili__recommend
module__bilibili__patrol
module__bilibili__status
```

模型通过 function calling 自己决定是否调用，不需要写关键词规则。

启动 action server：

```powershell
python -m modules.bilibili.main run
```

`run` 会同时启动巡检循环和 action server。

## 模块清单 module.json

登录 / 启动时模块会生成：

```text
modules/bilibili/identity/module.json
```

内容只包含公开身份，不含 Cookie：

```json
{
  "id": "bilibili",
  "name": "B站",
  "platform": "bilibili",
  "account": {"uid": "3000000002", "name": "主人2333"},
  "capabilities": ["patrol", "comment", "reply"],
  "events": ["video.comment", "video.reply"]
}
```

中枢「全量扫描」优先读这份清单；没有清单时，才用 AI 扫模块根目录，再没有才回退档案身份。  
AI 扫描不是凭空猜，它需要一个可读的目录/数据源；独立模块用 `module.json` 是最稳的声明方式。

## 怎么加入中枢

正常流程（对中枢来说，第一次它就是一个陌生模块）：

1. 配置本模块：`config.json` 里填中枢 Base URL / Key，并登录 B站。
2. 启动本模块：
   ```powershell
   python -m modules.bilibili.main run
   ```
3. 模块第一次调中枢 `/v1` 时带：
   ```http
   X-Host-Id: bilibili
   X-Host-Name: B站
   X-Host-UID: <B站 UID>
   X-Platform: bilibili
   ```
4. 中枢自动把它登记成模块，并生成信息卡（B站 UID / 昵称）。
5. 回连接中心 → B站 →「身份映射」→ 在信息卡那一行选档案 →「保存」。

不需要在控制台先点一个“B站模块”按钮，也不需要在页面里手填模块 ID 或 UID。  
信息卡必须来自模块内部；没有信息卡时点「身份匹配」或「全量扫描」。

## 对应连接中心的哪一类

| 类型 | 用不用 | 说明 |
|---|---|---|
| 模块 | ✅ | B 站模块通过 `/v1` 接入，并带 `X-Host-Id: bilibili` 自动登记为模块 |
| MCP | ❌ | 不暴露 MCP 工具 |
| 服务 | ❌ | 不注册成中枢直连服务 |

所以它出现在连接中心是：

```text
模块
└─ B站
```

## 为什么不是 AstrBot 插件

- 不导入 `astrbot`；
- 不读 AstrBot 的 `cmd_config.json` / `data_v4.db`；
- 不需要 AstrBot 运行；
- 停止 AstrBot 不影响 B 站模块；
- 以后 B 站模块可以单独部署到另一台机器，只要它能访问中枢 `/v1`。

## 文件

```text
modules/bilibili/
├─ main.py            # 入口：run / once / status
├─ bili_api.py        # B 站 API（热门 / 排行 / 分区新视频 / 详情 / 评论 / 回复）
├─ hub_client.py      # 中枢 /v1 客户端（模块身份 + 用户身份请求头）
├─ patrol.py          # 刷视频、评估、发评、回回复
├─ state.py           # 去重、配额、追踪、回复去重
└─ config.example.json
```

## 安装

不需要 `bilibili_api`，只用 Python 标准库；扫码登录需要 `qrcode`：

```powershell
cd D:\机器人
python -m pip install -r modules\bilibili\requirements.txt
Copy-Item modules\bilibili\config.example.json modules\bilibili\config.json
```

扫码登录：

```powershell
python -m modules.bilibili.main login
```

会生成 `modules/bilibili/data/login_qr.png`，用 B 站手机客户端扫码，登录成功后 Cookie 自动写回 `config.json`。

也可以手动填 Cookie：

```json
"bilibili": {
  "sessdata": "你的 SESSDATA",
  "bili_jct": "你的 bili_jct",
  "buvid3": "你的 buvid3",
  "dedeuserid": "你的 DedeUserID",
  "bot_uid": 123456,
  "master_uid": 123456,
  "master_name": "你的昵称"
}
```

Cookie 也可以走环境变量，避免写进文件：

```powershell
$env:BILI_SESSDATA="..."
$env:BILI_JCT="..."
$env:BILI_BUVID3="..."
$env:BILI_DEDEUSERID="..."
```

## 运行

```powershell
# 扫码登录（生成二维码，手机确认后自动写 Cookie）
python -m modules.bilibili.main login

# 只查登录 / 配额 / 状态
python -m modules.bilibili.main status

# 跑一轮，只评估不发表
python -m modules.bilibili.main once --dry-run

# 跑一轮，真正发表（覆盖配置里的 dry_run）
python -m modules.bilibili.main once --live

# 循环运行（默认 dry_run=true，先在日志里看行为）
python -m modules.bilibili.main run
```

确认没问题后，把 `config.json` 里的：

```json
"dry_run": false
```

才会真正发评论 / 回复。

## 中枢对接方式

每次 AI 调用都是：

```http
POST http://127.0.0.1:18895/v1/chat/completions
Authorization: Bearer hub-sk-bilibili
X-Host-Id: bilibili
X-Host-Name: B%E7%AB%99
X-Host-UID: <bot_uid>
X-Platform: bilibili
X-User-Id: <B站用户 UID>
X-User-Name: <B站昵称，URL 编码>
X-Channel: bilibili:video:<bvid>
```

- `X-Host-Id` 让中枢把 B 站登记成**模块**；
- `X-User-Id` 让 B 站用户走中枢的身份映射 / 档案 / 记忆；
- `X-Channel` 只做会话隔离；
- 模块不保存模型 Key，也不保存人格；人格来自中枢「人格设定」。

## 筛选机制

已恢复旧版三层筛选：

- **L1 必须命中「大肥鱼」**（标题 / 标签 / 简介）；
- **L2 必须命中「动画」或「二次元」**；
- **L3 AI 词可选**：`gemini / claude / gpt / ai / deepseek`，只做记录。

配置项：

```json
"filter_mode": "legacy",
"must_keywords": ["大肥鱼"],
"anime_keywords": ["动画", "二次元"],
"extra_keywords": ["gemini", "claude", "gpt", "ai", "deepseek"],
"search_all_keywords": true,
"fallback_generic": false
```

搜索仍按旧配置的关键词轮询，按发布时间倒序找新视频。

## 行为

- 按关键词搜索最新视频（WBI 签名），执行旧版三层筛选；
- 热门、配置的分区新视频作为通用兜底（仅在关闭 legacy 时使用）；
- 关键词 / 屏蔽词 / 时长 / 已看去重；
- 调中枢评估是否值得评论，并直接生成评论；
- 按每日 / 每轮 / 最小间隔配额发评；
- 追踪自己发过的评论，轮询楼中楼回复并生成回复；
- 状态保存在 `data/state.json`。

## 安全默认值

`config.example.json` 默认：

```json
"dry_run": true,
"auto_comment": true,
"auto_reply": true
```

也就是“会评估、会生成，但不真的发”。  
确认日志和行为后再关掉 `dry_run`。

## 后续可加

- 动态 / 直播 / 私信；
- 视频关键帧多模态；
- 更多搜索维度（分区 / 时长 / 粉丝数）；
- 模块自己的本地看板。
