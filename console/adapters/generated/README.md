# 生成的插件适配器

放置执行 AI 生成的 `*.py`。

要求：

- 实现 `Adapter` 接口
- 模块里设置 `ENABLED = False`
- 人工 review、补测试后改成 `ENABLED = True` 才会被加载
- 不允许联网、执行命令、写业务库
- 文件名不要以 `_` 开头（模板/草稿用 `_` 前缀）

生成命令见：

```text
python scripts/generate_plugin_adapter.py --help
```
