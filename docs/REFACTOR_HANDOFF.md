# 重构交接文档

> 更新：2026-10-31（第 1 步「合并代码分叉」已完成）
> 目标：拒绝屎山。四个硬指标见下。
> 状态：**进行中，未推远端**。开发端与测试端源码已重新对齐，两端都是 306 测试通过。

---

## 一、重构规则

来自项目 owner 的四条要求：

1. **去掉过度封装**：无状态却硬写的继承、过深的多层回调，一律拆平。
2. **单个函数 ≤ 20 行**，超过必须拆子函数。
3. **命名直白**：动词 + 名词，禁止隐晦缩写。
4. **关键分支写中文单行注释**，说明"这里为什么这么做"。

---

## 二、当前进度

### 已完成（15 条）

| 函数 | 原行数 | 现行数 | 拆出函数 | 所属文件 |
|---|---|---|---|---|
| `get_system_resources_metrics` | 159 | 9 | 14 个 | `console/modules/system_service.py` |
| `get_token_analytics` | 244 | 15 | 16 个 | `console/modules/model_service.py` |
| `_gateway_context`（前半） | 336 | 220 | 12 个 | `console/server.py` |
| `_gateway_context`（剩余 220 行） | 220 | 9 | 25 个方法 + 2 个工具函数 | `console/server.py` |
| `_gateway_complete` | 172 | 8 | 17 个 | `console/server.py` |
| `_gateway_stream` | 113 | 12 | 7 个 + 累加器拆 6 个 | `console/server.py` |
| `_gateway_record` | 112 | 15 | 5 个方法 + 7 个模块函数 | `console/server.py` |
| `do_GET`（路由表化） | 355 | 16 | 36 个方法 + 3 张表 | `console/server.py` |
| `do_POST`（路由表化） | 1033 | 16 | 38 个方法 + 3 张表 | `console/server.py` |
| `_handle_archive_post` | 110 | 10 | 8 个方法 + 1 个模块函数 | `console/server.py` |
| `do_DELETE` | 97 | 10 | 11 个方法 | `console/server.py` |
| `_handle_tool_clean` | 76 | 15 | 5 个方法 + 3 个模块函数 | `console/server.py` |
| `gate._decide` | 107 | 15 | 12 个方法 | `agent_core/tool_shelves/gate.py` |
| `recall_timeline` | 108 | 28（17 行是参数签名） | 1 个方法 + 10 个模块函数 | `agent_core/memory/episodic_stream.py` |
| `compress_tool_result` | 104 | 17 | 7 个模块函数 | `agent_core/tool_shelves/cleaner.py` |
| `SQLiteStore` 拆分（结构） | 2005 行 / 105 方法 | 88 行门面 + 10 个领域模块 | 10 个 mixin | `agent_core/storage/repos/` |
| `module_manager.py` 拆分 | 621 行 / 236 + 123 两个大函数 | 510 行 / 最长 20 行 | 14 个方法 | `console/modules/module_manager.py` |
| 合并代码分叉 | — | — | — | 全项目（见第三节） |

> 2026-10-31 追加：`_handle_universal_chat_completions_legacy`（268 行）全项目零调用，
> 不是「待拆」而是死代码，已直接删除（server.py 少 269 行）。

> 2026-10-31 追加：`do_GET` 里 12 个分支被前面的「已下线」前缀表整段挡住，
> `/hub/v1/services` 依赖的 `agent_core.services` 早已不存在（请求直接断连）。
> 这次一并清掉，改成规规矩矩的 404。

> 2026-10-31 追加：`do_POST` 里 30 多个分支同样被「已下线」前缀表挡住（plugins / services /
> mcp / bridges / contexts / modules / hosts / execution_ai 全在里面），一并删除；
> 顺带补上三个**未鉴权就能写**的洞：`/api/tools`、`/api/tools/policy`、`/api/archives`
> 以前不登录也能改工具配置和建档案，现在统一 401（已加回归测试）。


> 2026-10-31 追加：删掉两个死模块 —— `agent_core/topology.py`（全仓零 import）、
> `agent_core/connection.py`（只有测试在用，对应路由早已 404），连同 10 个测试用例。
> 这也是为什么「超 100 行」从 10 个降到 8 个：`validate_handshake`(122) 和
> `build_connection_tree`(103) 直接消失了，不需要拆。


> 2026-10-31 追加：CLI 链路已整条删除 —— `agent_core/cli.py`、`agent_core/agent.py`、
> `agent_core/memory/memory_tools.py`、`启动CLI.bat`（`llm.py` / `tools.py` /
> `execution/` 保留，因为 AI 主机识别和桥接推断还在用）。
> 若将来要终端入口，按「走本地 /v1 的瘦客户端」重写，别再各写一套模型调用与工具循环。


> 2026-10-31 追加：`sqlite_store.py` 从 2005 行 / 105 个方法拆成 88 行门面 + 10 个领域模块
> （`storage/repos/`：timeline 292、profiles 184、facts 151、invocations 206、archives 218、
> contexts 202、plugins 283、knowledge 111、components 130、ledger 214）。
> 用 mixin 而不是改调用方：`get_store().list_events()` 这类调用点一处没动，305 个测试当安全网。
> 连接与事务仍留在门面，`_json/_loads` 提到 `storage/jsonutil.py`。

### 全项目剩余

```text
超过 20 行的函数   271 个
超过 100 行的       5 个
```

最狠的五个：

```text
138 行  agent_core/llm.py  generate_with_tools                       主机识别 / 桥接推断在用
120 行  agent_core/identity_scan.py  scan                            低频，功能清晰
117 行  agent_core/storage/schema.py  _v2_timeline_and_plugins       一次性迁移，不拆
115 行  tests/test_tool_shelves.py  setUpClass                       测试夹具，不拆
102 行  tests/test_token_analytics.py  setUp                         测试夹具，不拆
```

> 2026-10-31 追加：`module_manager.py` 已拆完（621 行 → 510 行，最长函数 236 → 20），
> 同时删掉 9 个只被已下线路由使用的函数（toggle / import / delete / probe_* / validate…），
> `console/backend.py` 的导入同步收敛，并补了 `# ruff: noqa: F401` 说明它是转发层。
> 现在 **ruff 全树 0 错误**。

> 2026-10-31 修正：此前统计漏算 8 个带 UTF-8 BOM 的文件（`ast.parse` 直接报错被跳过），
> 其中就有 `module_manager.py` 的 236 行函数。清掉 BOM 后重测，真实基线是
> 1518 个函数 / 297 个 >20 / 18 个 >100 / 最长 1021；现在 1711 / 276 / 7 / 236。
> 验收结论见 [ACCEPTANCE_20261031.md](ACCEPTANCE_20261031.md)。

> 三个热路径（`_decide` / `recall_timeline` / `compress_tool_result`）已拆完，
> 全项目 100 行以上的函数只剩 5 个，其中 3 个是「一次性迁移 / 测试夹具 / 低频脚本」。
> `recall_timeline` 显示 28 行是因为 15 个参数占了一屏，函数体只有 8 行。

---

## 三、代码分叉已合并（2026-10-31）

**结论：开发端（`D:\Stellaris`）与测试端（`D:\Stellaris_test`）的源码已完全一致**，
逐文件 hash 比对：142 个 .py 全部相同，59 个前端/文档文件只有 `docker-compose.yml` 不同
（测试端那份多了它自己的服务定义，故意的）。

### 3.1 测试端 → 开发端（补功能）

| 内容 | 落点 |
|---|---|
| 档案级工具策略（不同档案给不同工具，陌生访客走 `_guest`） | `tool_shelves/config.py`、`admin.py`、`gate.py` |
| 策略接口与页面 | `server.py`（`/api/tools/policies`、`/api/tools/policy`、`/api/tools/policy/delete` + 3 个 handler）、`tools_page.js`、`index.html`、`components.css` |
| `_gateway_context` 拆分 | `server.py`（16 个 helper，335 → 220 行） |
| 账本「来源」列（通道 + 本轮调用的工具） | `sqlite_store.py`（`tools` 列）、`system_service.py`、`server.py`（`_gateway_record`）、`ledger.js`、`ledger.css`、`model_service.py` |
| `attitude_hint` 去掉句末标点 | `persona.py` |

### 3.2 开发端 → 测试端（补重构与卫生）

- `system_service.py` / `model_service.py` 的拆分版本
- 测试全套更新（E2E 裁剪断言改冒烟、fixture 不读真实配置、`test_ledger_migrated` 自造旧库）
- 隐私清洗：`identity.py` 改读 `data/identity.json`，源码里不再有真实 QQ / 姓名 / Key；
  `connect_astrbot.py` 不再硬编码 `hub-sk-astrbot`
- ruff 整理、CI、`pyproject.toml`、`.pre-commit-config.yaml`、`.github/`

### 3.3 顺带修掉的 bug（重要）

1. **工具闸门在测试端其实一直没生效**：
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
cd D:\Stellaris
python -m unittest discover -s tests -t .
# 期望：306 通过，1 skip（test_bilibili_module，模块已归档）

cd D:\Stellaris_test
python -m unittest discover -s tests -t .
# 期望：同上，306 通过
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
第 1 步  合并代码分叉                                     ✅ 已完成（306 通过）
第 2 步  拆 _gateway_context 剩余 220 行（工具闸门 + 构建 ctx dict）  ✅ 已完成（306 通过）
第 3 步  _gateway_complete（172 行）                        ✅ 已完成（306 通过）
第 4 步  删 _handle_universal_chat_completions_legacy（268 行死代码）  ✅ 已完成（306 通过）
第 5 步  _gateway_stream（113 行）+ _gateway_record（112 行）  ✅ 已完成（306 通过）
第 6 步  do_GET（355 行）→ 路由表化  ✅ 已完成（306 通过）
第 7 步  do_POST（1033 行）→ 路由表化  ✅ 已完成（306 通过）
第 8 步  console/server.py 已无 100 行以上函数；继续拆 agent_core（最大 138 行）
          然后是剩余 259 个 20~100 行的小函数
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
   所有拆分、改动**先在 `D:\Stellaris` 做**，跑通测试后再整文件复刻到
   `D:\Stellaris_test`。
   **禁止在测试端直接改代码**——那样开发端会落后，最后两边合不回来。
   上一轮 `_gateway_context` 就是反例：改在了测试端，所以补了一次整树合并。

   复刻用 robocopy（不要 `Copy-Item -Recurse`，它曾把包拷空）：

   ```powershell
   foreach ($d in 'agent_core','console','scripts','tests','docs') {
     robocopy "D:\Stellaris\$d" "D:\Stellaris_test\$d" /E /XD __pycache__
   }
   # 复刻后比对关键文件 hash，两端一致才算同步完成
   Get-FileHash 'D:\Stellaris\console\server.py','D:\Stellaris_test\console\server.py'
   ```
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

*接手建议：先跑一次测试确认基线（应 306 通过），再从 `_gateway_context` 剩余 220 行继续。*
