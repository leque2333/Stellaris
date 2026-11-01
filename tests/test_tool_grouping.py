# -*- coding: utf-8 -*-
"""工具页分组：工具必须落在插件或系统工具下，未登记只打红点，不单独成组。"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.tool_shelves import admin, registry  # noqa: E402
from agent_core.tool_shelves.config import ShelfConfig  # noqa: E402
from agent_core.tool_shelves.gate import ToolGate  # noqa: E402
from agent_core.tool_shelves.session import SessionStore  # noqa: E402

TOOL = {
    "type": "function",
    "function": {
        "name": "set_emotion",
        "description": "设置桌宠表情",
        "parameters": {"type": "object", "properties": {"emotion": {"type": "string"}}},
    },
}


class _Metrics:
    def record(self, **kwargs):
        pass


class ToolGroupingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tool_group_"))
        self.config = self.tmp / "tool_shelves.json"
        self.config.write_text(json.dumps({
            "gateway": {"enabled": False, "max_tools": 12},
            "always_available": [],
            "categories": {},
        }), encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {
            "TOOL_SHELVES_CONFIG_PATH": str(self.config),
            "TOOL_REGISTRY_PATH": str(self.tmp / "tool_registry.json"),
        })
        self.env.start()
        registry.clear()
        self.gate = ToolGate(
            cfg=ShelfConfig(self.config),
            classifier=lambda *a, **k: None,
            sessions=SessionStore(),
            metrics=_Metrics(),
        )

    def tearDown(self):
        self.env.stop()
        registry.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gate_records_plugin_identity(self):
        self.gate.decide(
            [{"role": "user", "content": "设置表情"}], [TOOL],
            channel_id="bloub_voice:web-demo", actor_id="web-demo",
            client_id="bloub_voice", plugin_id="bloub_voice",
        )
        info = registry.all_tools()["set_emotion"]
        self.assertEqual(info["plugin"], "bloub_voice")

    def test_observed_tool_groups_under_plugin_with_dot(self):
        registry.record([TOOL], plugin="bloub_voice")
        data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "set_emotion")
        self.assertEqual(item["source"], "observed")
        self.assertEqual(item["source_group"], "bloub_voice")
        self.assertTrue(item["unregistered"])

    def test_observed_tool_uses_display_name(self):
        from agent_core.tool_shelves import plugin_scan
        registry.record([TOOL], plugin="astrbot_plugin_bloub_voice")
        with mock.patch.object(plugin_scan, "label_for", return_value="Bloub 桌宠"):
            data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "set_emotion")
        self.assertEqual(item["source_group"], "Bloub 桌宠")

    def test_config_tool_without_plugin_gets_unknown_group(self):
        self.config.write_text(json.dumps({
            "gateway": {"enabled": False},
            "always_available": ["demo_tool"],
            "categories": {},
        }), encoding="utf-8")
        data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "demo_tool")
        self.assertEqual(item["source_group"], "未知插件")
        self.assertFalse(item["unregistered"])

    def test_plugin_scan_entry_keeps_display_group(self):
        fake = [{
            "name": "set_emotion", "plugin": "语音插件", "framework": "generic",
            "description": "", "path": "/tmp/voice",
        }]
        from agent_core.tool_shelves import plugin_scan
        with mock.patch.object(plugin_scan, "tool_entries", return_value=fake):
            data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "set_emotion")
        self.assertEqual(item["source_group"], "语音插件")
        self.assertTrue(item["unregistered"])


class ToolEventTagTest(unittest.TestCase):
    """工具事件：主标签是插件，工具名单独放 tool_name。"""

    def test_tool_event_keeps_plugin_primary(self):
        console_dir = str(ROOT / "console")
        if console_dir not in sys.path:
            sys.path.insert(0, console_dir)
        import server

        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._timeline_payload = lambda ctx, detail: {
            "plugin_name": "Bloub 桌宠",
            "plugin_id": "astrbot_plugin_bloub_voice",
            "plugin_chain": "astrbot → astrbot_plugin_bloub_voice",
            "tag_primary": "Bloub 桌宠",
            "tag_secondary": "arc_master",
            "tag_permission": "admin",
        }
        handler._timeline_initiator_tag = lambda ctx: "arc_master"
        handler._timeline_permission = lambda ctx: "admin"
        ctx = {
            "is_guest": False,
            "perms": {"allow_ingest_timeline": True},
            "actor_name": "乐阙",
            "group_name": "",
        }
        with mock.patch.object(server.backend, "ingest_timeline_event") as ingest:
            handler._ingest_tool_event(ctx, "set_emotion")
        kwargs = ingest.call_args.kwargs
        self.assertEqual(kwargs["payload"]["tag_primary"], "Bloub 桌宠")
        self.assertEqual(kwargs["payload"]["plugin_id"], "astrbot_plugin_bloub_voice")
        self.assertEqual(kwargs["payload"]["tool_name"], "set_emotion")
        self.assertEqual(kwargs["source"], "Bloub 桌宠")


if __name__ == "__main__":
    unittest.main()
