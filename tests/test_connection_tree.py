# -*- coding: utf-8 -*-
"""连接中心树：一级只有模块与 MCP，事件源/二级插件挂到模块下"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.connection import build_connection_tree  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class ConnectionTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_conn_tree_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.host_root = self.tmp / "astrbot"
        (self.host_root / "data").mkdir(parents=True)

        self.store.upsert_host(
            "astrbot", "AstrBot", platform="qq",
            internal_uid="onebot_napcat", root=str(self.host_root),
        )
        self.store.upsert_component(
            "plugin_a", "astrbot", "插件 A",
            capabilities=["event:message"], path=str(self.host_root / "plugins" / "plugin_a"),
        )
        self.store.upsert_service(
            "mcp_demo", name="Demo MCP", kind="mcp", endpoint="mcp://demo",
            capabilities=["tools"], manifest={"transport": "stdio", "tools": [{"name": "echo"}]},
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _build(self, customs=None, sessions=None):
        return build_connection_tree(
            self.store,
            customs=customs or [],
            mcp_sessions=sessions or [],
        )

    def test_module_groups_components_and_customs(self):
        tree = self._build(
            customs=[
                {"id": "astrbot", "name": "AstrBot 宿主服务", "module_id": "astrbot", "is_self": True},
                {"id": "napcat", "name": "NapCat", "module_id": "astrbot"},
                {"id": "nonebot", "name": "NoneBot"},
            ],
        )
        self.assertEqual(len(tree["modules"]), 1)
        module = tree["modules"][0]
        self.assertEqual(module["module_id"], "astrbot")
        self.assertEqual(module["counts"], {
            "components": 1, "missing_components": 0,
            "custom_components": 1,
        })
        self.assertEqual(module["components"][0]["component_id"], "plugin_a")
        self.assertEqual([c["id"] for c in module["custom_components"]], ["napcat"])

        self.assertEqual([c["id"] for c in tree["unassigned"]["custom_components"]], ["nonebot"])

    def test_imported_custom_component_keeps_parent_module(self):
        from console.modules.module_manager import (
            get_custom_module_configs,
            import_custom_module,
        )

        old_path = os.environ.get("HUB_CUSTOM_TOOLS_PATH")
        os.environ["HUB_CUSTOM_TOOLS_PATH"] = str(self.tmp / "custom_tools.json")
        try:
            ret = import_custom_module({
                "id": "weather_tool",
                "name": "天气组件",
                "category": "自定义组件",
                "module_id": "astrbot",
                "is_custom": True,
            })
            self.assertTrue(ret["success"])
            customs = get_custom_module_configs()["custom_modules"]
            tree = self._build(customs=customs)
            module = next(m for m in tree["modules"] if m["module_id"] == "astrbot")
            self.assertEqual([c["id"] for c in module["custom_components"]], ["weather_tool"])
        finally:
            if old_path is None:
                os.environ.pop("HUB_CUSTOM_TOOLS_PATH", None)
            else:
                os.environ["HUB_CUSTOM_TOOLS_PATH"] = old_path

    def test_stale_host_context_still_shows_info_card(self):
        context_id = self.store.upsert_context(
            "qq", "999", external_type="host", display_name="宿主账号",
            source="auto_provision", host_id="astrbot",
        )
        self.store.set_context_status(context_id, "stale")
        tree = self._build()
        module = next(m for m in tree["modules"] if m["module_id"] == "astrbot")
        self.assertIn("999", [c["external_id"] for c in module["contexts"]])

    def test_missing_components_are_hidden(self):
        self.store.upsert_component(
            "plugin_gone", "astrbot", "已删除插件",
            path=str(self.host_root / "plugins" / "plugin_gone"),
            status="missing",
        )
        tree = self._build()
        module = next(m for m in tree["modules"] if m["module_id"] == "astrbot")
        self.assertEqual([c["component_id"] for c in module["components"]], ["plugin_a"])
        self.assertEqual(module["counts"]["missing_components"], 1)

    def test_mcp_merges_live_session_tools(self):
        tree = self._build(sessions=[{
            "name": "Demo MCP", "transport": "stdio",
            "tools": [{"name": "echo"}, {"name": "ping"}],
        }])
        self.assertEqual(len(tree["mcp"]), 1)
        service = tree["mcp"][0]
        self.assertEqual(service["service_id"], "mcp_demo")
        self.assertTrue(service["live"])
        self.assertEqual([t["name"] for t in service["tools"]], ["echo", "ping"])

    def test_session_without_service_still_listed(self):
        tree = self._build(sessions=[{
            "name": "Loose MCP", "transport": "http", "tools": [{"name": "x"}],
        }])
        self.assertEqual(len(tree["mcp"]), 2)
        loose = next(item for item in tree["mcp"] if item["name"] == "Loose MCP")
        self.assertEqual(loose["service_id"], "mcp_Loose MCP")
        self.assertTrue(loose["live"])

    def test_partial_module_status(self):
        self.store.upsert_host("bare", "Bare Host", root=str(self.tmp / "bare"))
        tree = self._build()
        bare = next(m for m in tree["modules"] if m["module_id"] == "bare")
        self.assertEqual(bare["status"], "partial")

    def test_identity_and_http_split(self):
        self.store.upsert_context(
            "qq", "onebot_napcat", external_type="host",
            display_name="AstrBot", host_id="astrbot",
        )
        self.store.upsert_context(
            "qq", "onebot_napcat:FriendMessage:1000000001",
            external_type="friend", display_name="主人", source="astrbot",
        )
        self.store.upsert_context(
            "bilibili", "bilibili:uid:3000000001",
            external_type="account", display_name="B站账号", source="bilibili_plugin",
        )
        self.store.ensure_archive("master", role="admin", display_name="主人")
        self.store.bind_context(
            "qq", "onebot_napcat:FriendMessage:1000000001", "master",
            confidence=1.0, verified_by="console",
        )
        self.store.upsert_service(
            "http_demo", name="HTTP Demo", kind="service",
            endpoint="http://127.0.0.1:9000", capabilities=["tools"],
        )
        tree = self._build()
        module = tree["modules"][0]
        self.assertEqual(module["identity"]["platform"], "qq")
        self.assertEqual(module["identity"]["external_id"], "onebot_napcat")

        context_ids = [item["external_id"] for item in module["contexts"]]
        self.assertIn("onebot_napcat", context_ids)
        self.assertIn("onebot_napcat:FriendMessage:1000000001", context_ids)
        self.assertNotIn("bilibili:uid:3000000001", context_ids)
        friend = next(
            item for item in module["contexts"]
            if item["external_id"] == "onebot_napcat:FriendMessage:1000000001"
        )
        self.assertEqual(friend["binding"]["archive_id"], "master")

        self.assertEqual([s["service_id"] for s in tree["services"]], ["http_demo"])
        self.assertEqual([s["service_id"] for s in tree["mcp"]], ["mcp_demo"])
        self.assertEqual(tree["summary"]["services"], 1)

    def test_ignored_context_hidden_but_all_types_listed(self):
        friend_id = self.store.upsert_context(
            "qq", "onebot_napcat:FriendMessage:10001",
            external_type="friend", display_name="朋友", source="astrbot",
        )
        self.store.upsert_context(
            "qq", "onebot_napcat:GroupMessage:20002",
            external_type="group", display_name="群", source="astrbot",
        )
        self.store.upsert_context(
            "qq", "onebot_napcat:Bot:30003",
            external_type="bot", display_name="机器人", source="astrbot",
        )
        ignored_id = self.store.upsert_context(
            "qq", "onebot_napcat:FriendMessage:40004",
            external_type="friend", display_name="被忽略", source="astrbot",
        )
        self.store.set_context_status(ignored_id, "ignored")

        tree = self._build()
        ids = [item["external_id"] for item in tree["modules"][0]["contexts"]]
        self.assertIn("onebot_napcat:FriendMessage:10001", ids)
        self.assertIn("onebot_napcat:GroupMessage:20002", ids)
        self.assertIn("onebot_napcat:Bot:30003", ids)
        self.assertNotIn("onebot_napcat:FriendMessage:40004", ids)
        self.assertTrue(friend_id)

    def test_status_and_binding_persist_across_reopen(self):
        self.store.ensure_archive("master", role="admin", display_name="主人")
        self.store.upsert_context(
            "qq", "1000000001", external_type="friend",
            display_name="主人", source="astrbot", host_id="astrbot",
        )
        self.store.bind_context("qq", "1000000001", "master", confidence=1.0)
        ignored_id = self.store.upsert_context(
            "qq", "onebot_napcat:FriendMessage:40004",
            external_type="friend", display_name="忽略我", source="astrbot",
        )
        self.store.set_context_status(ignored_id, "ignored")
        self.store.close()

        reopened = SQLiteStore(self.tmp / "memory.db", migrate=False)
        try:
            self.assertEqual(reopened.resolve_binding("qq", "1000000001"), "master")
            statuses = {
                row["external_id"]: row["status"]
                for row in reopened.list_contexts()
            }
            self.assertEqual(statuses["onebot_napcat:FriendMessage:40004"], "ignored")
            self.assertEqual(statuses["1000000001"], "active")
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
