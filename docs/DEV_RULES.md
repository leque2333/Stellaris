# 后续开发规范

> 适用：`D:\Stellaris`（开发端）与 `D:\Stellaris_test`（测试端）
> 前提：**本轮重构已结束**（2026-10-31，见 [REFACTOR_HANDOFF.md](REFACTOR_HANDOFF.md) 与
> [ACCEPTANCE_20261031.md](ACCEPTANCE_20261031.md)）。后续以加功能为主，**不要再来一轮大重构**。

---

## 一、四条硬指标（写新代码时照样要守）

1. **单个函数 ≤ 20 行**，超了必须拆子函数。
2. **命名直白**：动词 + 名词，禁止隐晦缩写。
3. **去掉过度封装**：无状态却硬写的继承、多层回调，一律拆平。
4. **关键分支写中文单行注释**，说明"这里为什么这么做"。

例外只有三类，写清楚理由即可：

```text
・一次性迁移函数（schema 里 _v2_xxx 这种）
・测试夹具（setUp / setUpClass）
・参数签名本身很长的函数（例如 recall_timeline 15 个参数）
```

## 二、什么时候才允许重构

只有这三个条件同时成立：

```text
1. 这段代码马上要改功能，不改结构就改不动
2. 有测试覆盖，或者能写出对拍基线
3. 改完能立刻用「开发端测试 + 测试端实测」验证
```

**不满足就别动。** 纯"看着不顺眼"的重构一律拒绝——上一轮已经把该拆的都拆了，
`>20 行` 的函数剩下 271 个，绝大多数是拼 SQL / 拼 JSON 的短函数，继续拆收益递减。

## 三、改代码的固定流程

```text
1. 在 D:\Stellaris 改（永远不在测试端直接改）
2. 跑测试：python -m unittest discover -s tests -t .
3. ruff 必须干净：ruff check console agent_core scripts tests
4. robocopy 复刻到 D:\Stellaris_test（见 REFACTOR_HANDOFF 第七节）
5. 重启测试端 18895，实测改动点（curl / 控制台）
6. 两端测试数一致（当前数字以 [README](../README.md) 为准，改测试后同步更新 README）
```

## 四、不要做的事

```text
✗ 不要用 ruff format 整树跑：本仓库只过 ruff check --fix，format 会改 100+ 个文件
✗ 不要给中枢加工具执行：工具归 AstrBot，中枢只决定"给不给"
  （唯一例外：记忆检索——它是中枢内部功能，不经过 AstrBot）
✗ 不要加启发式判断：身份、角色、关系一律显式配置
✗ 不要把密钥/QQ/UID 写进源码：真实值只在 data/ 下
✗ 不要在测试端直接改代码，再"同步回开发端"
✗ 不要为了兼容旧路由保留死分支：路由删了就把函数也删掉
✗ 不要把运维事件写进时间轴：时间轴是给 AI 的记忆，登录/配置变更走 audit_log
```

## 五、踩过的坑（都有代价）

1. **`_send_json()` 返回 None**：别把它的返回值当"已处理"信号，会发出双份响应。
   需要判断就写成 `if self._check(): return` 这种 bool 形式。
2. **网关闸门是 fail-open**：`except` 里吞掉异常还会继续对话，写了拼写错误的参数
   不会报错、只是静默降级。**改这块必须补断言"效果"的测试**（比如上游收到的工具表）。
3. **BOM 会让 `ast.parse` 跳过文件**：写统计脚本时用 `utf-8-sig`，否则 236 行的函数
   会从统计里消失（真的发生过）。
4. **搬函数要连装饰器一起搬**：`@contextmanager` 掉了会变成普通生成器。
5. **测试里 import `console/server.py` 会读 `sys.argv[1]` 当端口**：已改成宽松解析，
   但别在模块顶层再读 argv。
6. **反向覆盖事故**：曾经把生产端旧文件拷回开发端。同步只允许 dev → test，同步后要比 hash。

## 六、加新功能的检查表

- [ ] 新接口放进 `_CONSOLE_GET_ROUTES` / `_POST_ROUTES` 表，别写回 `if path ==` 长链
- [ ] 写接口一律要登录（`needs_auth=True`），别默认公开
- [ ] 涉及 `/v1` 或 `/hub/v1` 的改动，跑一遍签名开关（见 [API_SECURITY.md](API_SECURITY.md)）
- [ ] 涉及数据库的改动，加迁移（`schema.py` 里新增 vX），不要在代码里 ALTER
- [ ] 加完两端测试数一致、ruff 干净、测试端实测通过
