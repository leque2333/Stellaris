# 身份传输层契约

> 更新：2026-10-29  
> 适用：所有通过 OpenAI 兼容 `/v1` 接入中枢的模块/宿主。  
> 实现：`console/server.py` 顶部契约注释、`_bound_archive_id`、`_hub_identity_payload/headers`；
> 绑定解析：`agent_core/identity_binding.py`。

---

## 1. 目标

让中枢知道「这句话是谁说的」，并把「中枢认成了谁」原样带回模块。

只依赖 HTTP，不依赖模块是否开源、是否可改：

- 模块能带 `user` / `X-User-Id` → 直接用；
- 不能带 → 边缘代理或一人一 Key；
- 不想读回传 → 只做入口，人设/记忆/好感仍然生效。

---

## 2. 入口：模块 → 中枢

### 2.1 最省事的方式（OpenAI 原生字段）

```json
POST /v1/chat/completions
{
  "model": "任意",
  "user": "<你的QQ>",
  "messages": [{"role": "user", "content": "在吗"}]
}
```

`user` 必须是**发送者的底层用户 UID**，不是群号、会话 ID、昵称。

### 2.2 显式头部（推荐）

```http
POST /v1/chat/completions
X-User-Id: <你的QQ>
X-User-Name: LeQue
X-Platform: qq
X-Channel: qq_group:<群号>
X-Channel-Type: group
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `user` / `X-User-Id` | 是 | 发送者底层 UID；没有就按 master/默认处理 |
| `X-User-Name` | 否 | 展示名，支持 URL 编码 |
| `X-Platform` | 否 | qq / telegram / discord…；缺省按 `X-Channel` 前缀推断，最后 qq |
| `X-Channel` | 群聊建议 | 群/会话 ID，只用于记忆隔离 |
| `X-Channel-Type` | 否 | group / private |

### 2.3 优先级

```text
body.user  →  X-User-Id  →  X-Actor-Id  →  正文 "昵称(uid):" 提取  →  Key 的 default_actor
```

只认底层 UID；群号只进 `channel_id`，不参与身份匹配。

---

## 3. 出口：中枢 → 模块

### 3.1 非流式响应体

```json
{
  "id": "chatcmpl-...",
  "choices": [{ "message": { "role": "assistant", "content": "..." } }],
  "hub": {
    "actor_id": "master",
    "actor_name": "主人",
    "archive_id": "arc_master",
    "platform": "qq",
    "sender_uid": "<你的QQ>",
    "affection": 90,
    "bound_by": "binding"
  }
}
```

`bound_by`：`binding`（命中绑定）/ `master`（主人）/ `actor`（按 actor 档案）/ `default`。

### 3.2 响应头（流式、非流式都有）

```http
X-Hub-Actor-Id: master
X-Hub-Actor-Name: %E4%B9%90%E9%98%99
X-Hub-Archive-Id: arc_master
X-Hub-Platform: qq
X-Hub-Sender-Uid: <你的QQ>
X-Hub-Affection: 90
X-Hub-Bound-By: binding
```

`X-Hub-Actor-Name` 是 URL 编码；浏览器跨域读取需要 `Access-Control-Expose-Headers`（服务端已带）。

### 3.3 流式限制

SSE 在响应头阶段就把 `X-Hub-*` 发出，响应体分块里不重复身份。  
如果客户端只解析 `data:` 且拿不到响应头，就读不到 `hub`；此时可以：
- 改用非流式；或
- 在客户端 HTTP 层读 header（Python/JS 都支持）。

---

## 4. 身份匹配

1. 中枢按 `X-User-Id` 拿到底层 UID；
2. `resolve_archive_for_sender(platform, sender_id, external_id)` 精确查 `platform_bindings`；
3. 命中 → 用该档案的人设；未命中 → 按 actor 默认（主人 master / 普通 user_<uid>）；
4. 好感按 actor 读取：主人 90，其他人默认 50。

绑定在控制台模块详情「身份匹配」里完成：

- 数字 UID + 名称 → 执行 AI 读取该模块项目内的数据，整合成信息卡；
- 匹配不到 → 全量扫描；
- 绑定键写底层 UID，不再用 `FriendMessage:<uid>` 复合键。

---

## 5. 模块不可改代码时

| 方案 | 做法 | 适用 |
|---|---|---|
| 边缘代理 | 读 `body.user` / session / 正文 `(uid):`，注入 `X-User-Id` | 能改 Base URL 的闭源客户端 |
| 一人一 Key | 每个用户/实例发不同中枢 Key，Key → 档案 | 一人一实例 |
| 会话绑定 | 控制台把稳定 session/channel 绑到档案 | 有稳定会话 ID |
| 正文提取 | 模块已输出 `昵称(uid):`，中枢自动提取 | AstrBot provider 等 |

如果模块既不能带 UID、也没有稳定会话、也没有独立 Key，中枢无法区分用户——这是信息不存在，不是代码问题。

---

## 6. 直接照抄的示例

### curl

```bash
curl http://127.0.0.1:18895/v1/chat/completions \
  -H "Authorization: Bearer hub_sk_xxx" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: <你的QQ>" \
  -H "X-Platform: qq" \
  -H "X-Channel: qq_group:<群号>" \
  -d '{"model":"gemini-3.8-flash","messages":[{"role":"user","content":"在吗"}]}'
```

### Python（openai SDK）

```python
client = OpenAI(base_url="http://127.0.0.1:18895/v1", api_key="hub_sk_xxx")
resp = client.chat.completions.create(
    model="gemini-3.8-flash",
    messages=[{"role": "user", "content": "在吗"}],
    extra_body={"user": "<你的QQ>"},       # 最省事
    extra_headers={"X-Channel": "qq_group:<群号>"},
)
print(resp.choices[0].message.content)
# resp.model_extra.get("hub") 视 SDK 版本而定；拿不到就读响应头
```

### 只改请求、不读回传

```text
人格、记忆、好感、绑定都会正确；模块界面不需要知道 hub 元数据。
```

---

## 7. 兼容性

| 场景 | 支持 |
|---|---|
| OpenAI 兼容 + 带 `user` | ✅ 零改动 |
| OpenAI 兼容 + 能加请求头 | ✅ 一次接线 |
| OpenAI 兼容 + 不能改请求 | 🟡 边缘代理 / 一人一 Key |
| 多用户共享一个入口、无稳定 UID | ❌ 无法区分 |
| 流式读响应头 | ✅（客户端需支持读 header） |
| MCP / HTTP 工具服务 | 不需要身份，身份留在中枢 |

---

## 8. 非目标

- 不要求模块开源；
- 不要求模块读源码扫描；
- 不把群号/会话 ID 当人；
- 不在响应正文里塞身份文本（避免污染回复）；
- 不替模块做权限判断，只传事实。
