# 重构交接文档

> 更新：2026-10-31（第 1 步「合并代码分叉」已完成）
> 目标：拒绝屎山。四个硬指标见下。
> 状态：**进行中，未推远端**。开发端与生产端源码已重新对齐，两端都是 310 测试通过。

---

## 一、重构规则

来自项目 owner 的四条要求：

1. **去掉过度封装**：无状态却硬写的继承、过深的多层回调，一律拆平。
2. **单个函数 ≤ 20 行**，超过必须拆子函数。
3. **命名直白**：动词 + 名词，禁止隐晦缩写。
4. **关键分支写中文单行注释**，说明"这里为什么这么做"。

---

## 二、当前进度

### 已完成（4 个）

| 函数 | 原行数 | 现行数 | 拆出函数 | 所属文件 |
|---|---|---|---|---|
| `get_system_resources_metrics` | 159 | 9 | 14 个 | `console/modules/system_service.py` |
| `get_token_analytics` | 244 | 15 | 16 个 | `console/modules/model_service.py` |
| `_gateway_context`（前半） | 336 | 220 | 12 个 | `console/server.py` |
| 合并代码分叉 | — | — | — | 全项目（见第三节） |

### 全项目剩余

```text
超过 20 行的函数   267 个
超过 100 行的       18 个
```

最狠的五个：

```text
1033 行  console/server.py  do_POST
 355 行  console/server.py  do_GET
 268 行  console/server.py  _handle_universal_chat_completions_legacy
 220 行  console/server.py  _gateway_context            ← 下一步
 172 行  console/server.py  _gateway_complete
```

---

## 三、代码分叉已合并（2026-10-31）

**结论：开发端（`D:\机器人`）与生产端（`D:\机器人_完整版`）的源码已完全一致**，
逐文件 hash 比对：142 个 .py 全部相同，59 个前端/文档文件只有 `docker-compose.yml` 不同
（生产端那份多了它自己的服务定义，故意的）。

### 3.1 生产端 → 开发端（补功能）

| 内容 | 落点 |
|---|---|
| 档案级工具策略（不同档案给不同工具，陌生访客走 `_guest`） | `tool_shelves/config.py`、`admin.py`、`gate.py` |
| 策略接口与页面 | `server.py`（`/api/tools/policies`、`/api/tools/policy`、`/api/tools/policy/delete` + 3 个 handler）、`tools_page.js`、`index.html`、`components.css` |
| `_gateway_context` 拆分 | `server.py`（16 个 helper，335 → 220 行） |
| 账本「来源」列（通道 + 本轮调用的工具） | `sqlite_store.py`（`tools` 列）、`system_service.py`、`server.py`（`_gateway_record`）、`ledger.js`、`ledger.css`、`model_service.py` |
| `attitude_hint` 去掉句末标点 | `persona.py` |

### 3.2 开发端 → 生产端（补重构与卫生）

- `system_service.py` / `model_service.py` 的拆分版本
- 测试全套更新（E2E 裁剪断言改冒烟、fixture 不读真实配置、`test_ledger_migrated` 自造旧库）
- 隐私清洗：`identity.py` 改读 `data/identity.json`，源码里不再有真实 QQ / 姓名 / Key；
  `connect_astrbot.py` 不再硬编码 `hub-sk-astrbot`
- ruff 整理、CI、`pyproject.toml`、`.pre-commit-config.yaml`、`.github/`

### 3.3 顺带修掉的 bug（重要）

1. **工具闸门在生产端其实一直没生效**：
   `_gateway_context` 里把 `archive_id` 传成了 `ctx.get("archive_id")`，而 `ctx` 这时还没构建，
   抛 `NameError` 后被闸门的 fail-open 吞掉 → 每轮都"全量透传"。
   现在改成传本地变量 `archive_id`，并加了 E2E 断言：策略命中时上游工具表必须被裁剪。
2. **账本 tools 列只有一半**：schema 有 v26 迁移、写入端有 `tools`，但明细 SQL 没 SELECT，
   页面「来源」列永远只有通道、看不到工具。已补 `session_type` / `tools` 两个字段。

### 3.4 数据库

```text
开发库   schema 25 → 启动时自动迁到 26
生产库   schema 26（已有 tools 列）
```

---

## 四、测试与验证

### 测试

```powershell
cd D:\机器人
python -m unittest discover -s tests -t .
# 期望：310 通过，1 skip（test_bilibili_module，模块已归档）

cd D:\机器人_完整版
python -m unittest discover -s tests -t .
# 期望：同上，310 通过
```

本轮新增 12 个用例：

```text
ArchivePolicyGateTest           5 个（allow / deny / _guest / actor 回退 / 无策略）
ArchivePolicyAdminTest          5 个（读写往返 / 空档案写 _guest / 非法参数拒绝）
GatewayE2E.test_archive_policy_applies_to_gateway   1 个（回归 3.3 的 NameError）
test_recent_ledger_carries_channel_and_tools        1 个（账本来源列有数据）
```

### 重构后的手工验证（必做）

```powershell
# 1. 账单页数据口径
curl -X GET http://127.0.0.1:18895/api/ledger -H "X-Session-Token: <token>"

# 2. 资源监控
curl -X GET http://127.0.0.1:18895/api/system/resources -H "X-Session-Token: <token>"

# 3. 真实对话（含记忆召回）
curl -X POST http://127.0.0.1:18895/v1/chat/completions \
  -H "Authorization: Bearer hub-sk-astrbot" \
  -H "Content-Type: application/json" \
  -H "X-User-Id: 2986371638" -H "X-Channel: verify" \
  -d '{"model":"gemini-3.8-flash","messages":[{"role":"user","content":"在吗"}]}'
```

`X-User-Id` 必须是白名单里的（`1000000001` 会被 403 `guest_disabled` 挡掉）。

---

## 五、下一步

按风险从低到高：

```text
第 1 步  合并代码分叉                                     ✅ 已完成（310 通过）
第 2 步  继续拆 _gateway_context 剩余 220 行
          剩余部分是「工具闸门 + 构建 ctx dict」，见 server.py 2997 行之后
第 3 步  _gateway_complete（172 行）
第 4 步  _handle_universal_chat_completions_legacy（268 行）
第 5 步  _gateway_record（112 行）+ _gateway_stream（113 行）
第 6 步  do_GET（355 行）→ 路由表化
第 7 步  do_POST（1033 行）→ 路由表化，工作量最大
第 8 步  剩余 260 多个 20~100 行的
```

---

## 六、已经踩过的坑

1. **测试 fixture 别读真实配置**
   `test_tool_shelves.py` 原本从 `data/tool_shelves.json` 读，本机配置一改测试就挂。
   已改成只用内置 `BASE_SETTINGS`。

2. **测试改配置必须恢复**
   用 `@contextlib.contextmanager` 包一层（参考 `GatewayE2E._with_shelves`），
   否则污染后续测试。

3. **E2E 不要断言裁剪细节**
   裁剪逻辑受 fastpath + 分类器 + 策略三个开关影响，E2E 环境不可控。
   裁剪正确性交给 `GateDecideTest`（直接调 `gate.decide`），E2E 只保留冒烟 + 策略生效。

4. **`psutil.cpu_percent()` 首次必然返回 0**
   必须给 `interval` 采样间隔，否则资源监控永远是 0%。

5. **`filter-repo` 重写历史后 remote 会被删**
   需要重新 `git remote add`，然后 `git push --force`。

6. **`ruff format` 会重排整棵树，不要顺手跑**
   本仓库现有代码只过了 `ruff check --fix`（import 排序、空白、f-string），
   没过 `ruff format`。直接 `ruff format .` 会改掉 100+ 个文件，
   把真正的改动埋掉。只对本次改到的文件跑 `ruff check --fix`。

7. **闸门异常会被 fail-open 吞掉**
   `gate.decide` 外层 `except` 会记一条 warning 后全量放行。
   写新参数时先确认变量真的存在，否则功能静默失效、测试还全绿。
   回归用例要断言"效果"（上游工具表），不能只断言"没报错"。

8. **同步目录用 robocopy，不要用 `Copy-Item -Recurse`**
   曾经把整个包拷空导致中枢 import 失败。robocopy `/E` 不会清空目标，
   同步完立刻比对 hash（见 HANDOFF §11.10）。

---

## 七、开发约定

1. **开发端先行，再复刻到测试端**：
   所有拆分、改动**先在 `D:\机器人` 做**，跑通测试后再整文件复刻到
   `D:\机器人_完整版`。
   **禁止在生产端直接改代码**——那样开发端会落后，最后两边合不回来。
   上一轮 `_gateway_context` 就是反例：改在了生产端，所以补了一次整树合并。
2. **迁移必备份**：`apply_migrations` 自动快照到 `backups/`。
3. **失败降级**：工具裁剪、模型路由、好感度评估都走 fail-open。
4. **不加启发式判断**：身份、角色、关系一律显式配置。
5. **身份与密钥不写进源码**：真实值只在 `data/` 下。
6. **测试一律在测试端进行**：源码 / 文档 / 测试里不能出现真实账号和 Key。
7. **两端测试数必须一致**：现在是 310 + 1 skip，不一致就说明又分叉了。

---

## 八、当前 git 状态

```text
分支     main
HEAD     合并提交（archive 策略 + _gateway_context 拆分 + 账本 tools 列）
工作区   干净
远端     github.com/leque2333/Stellaris（public，AGPL-3.0）
```

**owner 要求：全部重构完成后再推送。** 现在不要 push。

---

*接手建议：先跑一次测试确认基线（应 310 通过），再从 `_gateway_context` 剩余 220 行继续。*
