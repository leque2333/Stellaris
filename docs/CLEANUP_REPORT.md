# 代码清理记录 / Cleanup Report

日期：2026-10-30  
目标：把已下线的连接中心、旧模块体系、旧前端资源从主路径清掉，文档与代码边界一致。

---

## 已删除

### 前端

```text
console/static/js/app.js             单文件旧版入口，不再被 index 引用
console/static/js/connectors.js      连接中心
console/static/js/bridges.js         桥接器
console/static/js/mod_manager.js     模块管理 / 导入 / 身份匹配
console/static/css/connectors.css
console/static/css/bridges.css
console/static/css/mod_manager.css
console/static/css/console.css
```

同时从 `index.html` 删除：

```text
连接中心 / 桥接器 / 模块管理 CSS
“接入外部组件”弹窗
“身份双填匹配”弹窗
旧模块导入表单
```

从 `core.js` / `main.js` 删除：

```text
tab-connectors / tab-bridges / tab-modules 分支
#connectors / #bridges 路由
```

### 后端

```text
agent_core/health_probe.py
agent_core/hub_tools.py
agent_core/capability_sources.py
agent_core/capabilities.py
agent_core/tool_permissions.py
agent_core/onebot_service.py
agent_core/connectors/onebot_events.py
agent_core/connectors/module_manifest.py
agent_core/module_resolver.py
agent_core/services.py
```

这些模块只服务于已下线的：

```text
连接中心
服务注册
工具权限
OneBot 直连
模块清单导入
```

### 测试

删除对应旧测试：

```text
test_hub_tools.py
test_capabilities.py
test_cli_action.py
test_openapi_source.py
test_tool_permissions.py
test_onebot.py
test_module_manifest.py
test_services.py
test_layers_integrity.py
```

### 文档 / 数据

已移到备份：

```text
anan_core_settings_v5_3.md
hakimi_core_settings_v5_2.md
PROJECT_ARCHIVE.md
项目档案.md
agent_hub.db（空文件）
docs/CONNECTION_LAYER.md
docs/INTEGRATION_TEST_2026-10-29.md
docs/NEXT_WINDOW.md
```

备份目录：

```text
backups/code_cleanup_20261030/
backups/legacy_docs_20261030/
```

### AstrBot 知识库

已移出：

```text
astrbot/data/knowledge_base/明日方舟世界观与社区黑话梗百科.md
astrbot/data/knowledge_base/英雄联盟峡谷机制与电竞圣经大全.md
```

备份：

```text
backups/knowledge_base_removed_20261030/
```

同时关闭：

```text
gemini_embedding_inst
```

---

## 清理后测试

```text
python -m unittest discover -s tests -t .
Ran 179 tests
OK
```

---

## 仍然保留的兼容层

以下代码仍存在，但已经不在当前 Web 主路径上：

```text
agent_core/bridge/
agent_core/execution/
agent_core/mcp/
agent_core/plugins/
agent_core/source/
agent_core/connectors/
agent_core/adapters/
agent_core/connection.py
agent_core/topology.py
agent_core/knowledge.py
```

保留原因：

- CLI / 旧测试仍可能引用；
- 部分存储/路径工具被现有模块间接依赖；
- 一次性删除会牵连大量历史路由。

这些模块不应再新增功能；如果确认 CLI 与旧接口不再使用，下一轮可以整体删除。

---

## 当前主路径

```text
console/server.py
console/backend.py
console/modules/{common,persona_service,model_service,system_service,compute_service}.py
agent_core/{persona,identity,timeline_hub,compute_providers,clients}.py
agent_core/memory/
agent_core/storage/
agent_core/profile/
```

---

## 原则

```text
1. 控制台只保留：仪表盘 / 记忆 / 人格 / 档案 / 账本 / 设置
2. 平台接入只保留 AstrBot
3. 连接中心、模块市场、服务注册、工具权限不再回归
4. 新能力优先加在中枢或 AstrBot，不新建第三套框架
```
