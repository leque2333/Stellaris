# 参与开发

## 环境

```bash
git clone https://github.com/leque2333/Stellaris.git
cd Stellaris
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

需要 Python 3.10+。

## 跑起来

```powershell
# 控制台 + 网关（默认 18895）
python console/server.py 18895
```

首次启动会自动建库并跑 schema 迁移，备份落在 `backups/`。

## 测试

```bash
python -m unittest discover -s tests -t .
```

提 PR 前必须全绿。测试全部使用假身份（见 `tests/__init__.py`），**不要**为了图省事把真实号码填进去。

## 目录

```text
agent_core/     中枢内核：人格、记忆、档案、路由、工具货架
console/        HTTP / Web / OpenAI 兼容网关 + 前端
tests/          测试（假数据）
docs/           设计文档
scripts/        运维脚本
```

## 代码约定

1. **单向同步**：开发端 → 生产端，禁止反向覆盖。
2. **失败降级**：工具裁剪、模型路由、好感度评估都走 fail-open，不能把对话弄挂。
3. **不加启发式判断**：身份、角色、关系一律显式配置，不自动推断。
4. **迁移必备份**：`apply_migrations` 会自动快照到 `backups/`。
5. **身份与密钥不写进源码**：真实值只放 `data/identity.json`、`data/clients.json`（已被 `.gitignore` 忽略），源码里只留占位符和环境变量。

## 提交信息

一句话说清改了什么，用中文。不要写 `update` / `fix bug` 这种。

```text
工具货架、档案系统、好感度、性能监控
剥离第三方框架，中枢只做大脑
```

## 提 PR

1. fork → 开分支
2. 改代码 + 补测试
3. 跑全量测试
4. 开 PR，填模板

## 许可证

贡献的代码按 [AGPL-3.0](LICENSE) 授权。
