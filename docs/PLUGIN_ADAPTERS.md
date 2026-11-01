# 插件接入契约：中枢适配插件

> 更新：2026-11-01  
> 原则：**中枢去适配插件，不要求插件为中枢改协议。**

## 1. 总原则

插件只需要暴露它本来就有的东西：

- 平台名
- 用户 UID
- 会话 / 群 / 频道
- 插件自己的名字
- 工具名和参数

中枢负责把这些归一化成：

```text
身份：platform + uid → archive_id
时间轴：plugin · archive_id · permission
工具：真实工具名 + 发起人档案
```

插件没有的字段，中枢用嗅探、manifest、历史流量或生成的适配器补，不反过来要求插件新增协议。

## 2. 三层适配

### L0：通用请求（零代码）

插件只要能发 HTTP，带上以下任意字段即可：

```http
X-Platform: telegram / discord / bilibili / ...
X-User-Id: 平台底层 UID
X-User-Name: 昵称
X-External-Id: 平台账号 / 会话 ID
X-Host-Id: 插件标识
X-Channel: 会话
X-Channel-Type: group / private
```

没有头的插件也可以接：中枢会从正文、路径、User-Agent、manifest 和历史流量推断。

### L1：声明式适配器（少量配置）

适合字段名固定、但和通用头不一致的插件：

```json
{
  "plugin_id": "demo_plugin",
  "match": {"path_prefix": "/demo", "user_agent": "demo/"},
  "fields": {
    "platform": "headers.X-Platform | body.platform | 'demo'",
    "uid": "body.sender.id | headers.X-User-Id",
    "name": "body.sender.name",
    "session": "body.chat_id",
    "group": "body.group_id",
    "host_id": "headers.X-Host-Id | 'demo_plugin'"
  },
  "identity": {
    "platform": "demo",
    "external_prefix": "demo:"
  }
}
```

声明式适配器只做字段映射，不执行任意代码。

### L2：生成的 Python 适配器

适合结构复杂、需要解析嵌套消息、加解密或特殊 UID 规则的插件。

生成物放在：

```text
console/adapters/generated/<plugin_id>.py
```

必须实现统一接口：

```python
class Adapter:
    plugin_id = "demo_plugin"

    def detect(self, request) -> float:
        """0~1，越大越确定是这个插件。"""

    def context(self, request) -> dict:
        """返回 platform / uid / name / session / group / host_id。"""

    def timeline(self, ctx) -> dict:
        """可选：覆盖 tag_primary / tag_secondary / tag_permission。"""
```

适配器只做**归一化**，不联网、不执行工具、不读写业务数据库。

## 3. 用执行 AI 生成适配器

新插件可以走这条流水线：

```text
插件目录 / manifest / 工具 schema / 样例请求
  → 执行 AI 阅读
  → 生成 console/adapters/generated/<plugin_id>.py
  → 同时生成 tests/test_adapter_<plugin_id>.py
  → 静态检查 + 单测
  → 人工 review
  → 启用
```

执行 AI 的输入应包含：

- 本契约
- 一个现有适配器示例（AstrBot）
- 插件的 `metadata.yaml` / manifest / `package.json` / 工具定义
- 1~3 条真实请求样例（脱敏）
- 明确的输出路径和测试要求

执行 AI 的输出必须：

- 只依赖标准库
- 不包含网络请求
- 不执行 shell
- 不写业务数据
- 有 fixture 测试：UID、群聊、私聊、缺字段降级
- 通过 `ruff` 和 `unittest`

**生成不等于启用。** 默认只写文件，不自动加载；需要人工 review 后在适配器注册表里启用。

## 4. 身份映射

适配器输出统一字段后，中枢按：

```text
platform + uid → platform_bindings → archive_id
```

匹配成功：

```text
时间轴：plugin · archive_id · archives.role
```

没有匹配档案：

```text
时间轴：plugin · platform:uid · guest/unknown
```

多管理员不需要改适配器：每个管理员一个 archive，适配器只负责把 UID 交上来。

## 5. 时间轴标签

| 标签 | 来源 |
|---|---|
| 主标签 | 插件名 / 工具名 |
| 发起人标签 | `archive_id`；无档案才用 `platform:uid` |
| 权限标签 | `archives.role`：admin / guest / system / unknown |

适配器可以覆盖主标签，但不能绕过身份映射伪造 archive 或 role。

## 6. 适配器发现顺序

```text
1. 显式 X-Host-Id / X-Plugin-Chain
2. 已启用的声明式适配器
3. 已启用的生成适配器
4. 内置 AstrBot 适配器
5. 通用嗅探（platform / UID / 正文前缀）
6. 兜底：unknown
```

每一步失败都继续降级，不把请求打回。

## 7. 安全边界

- 生成适配器必须 review 后才启用
- 适配器不能拿到宿主机控制权限
- 适配器不能修改 `archives`、`platform_bindings`
- 适配器异常只影响自己的识别，不影响网关主流程
- 适配器日志不记录完整密钥和原始消息

## 8. 当前进度

已完成：

1. `console/adapters/base.py`：`Adapter` / `RequestView` 统一接口
2. `console/adapters/registry.py`：内置 + 声明式 + `generated/*.py` 加载、置信度选择
3. AstrBot 适配器已迁到统一接口，群名查询也走注册表
4. 网关会先用适配器归一化 `platform` / `uid` / `host_id`，再进入身份映射
5. 执行 AI 生成脚本：`scripts/generate_plugin_adapter.py`
6. 生成物默认 `ENABLED = False`；注册表只加载 `ENABLED = True` 的适配器

声明式适配器放：

```text
console/adapters/declarative/*.json
```

只有 `"enabled": true` 才会加载；示例见 `example.json`。

生成命令：

```powershell
# 只看提示词，不花钱
python scripts/generate_plugin_adapter.py --plugin-dir <插件目录>

# 真正调用执行 AI，生成 adapter + test
python scripts/generate_plugin_adapter.py --plugin-dir <插件目录> --run
```

生成物：

```text
console/adapters/generated/<plugin_id>.py
tests/test_adapter_<plugin_id>.py
```

生成器只写文件并做静态校验；**不会自动启用**。人工 review、跑测试后，把 `ENABLED` 改成 `True` 才会被注册表加载。
