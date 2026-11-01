# -*- coding: utf-8 -*-
"""群内工具调用单独成事件：插件名 + 调用者 UID。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class ToolTimelineTest(unittest.TestCase):
    def setUp(self):
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.handler._timeline_payload = lambda ctx, detail: {
            "plugin_name": "bili_plugin",
            "plugin_id": "bili_plugin",
            "tag_primary": "bili_plugin",
        }
        self.handler._timeline_initiator_tag = lambda ctx: "arc_master"
        self.handler._timeline_permission = lambda ctx: "admin"
        self.calls = []
        self._old = server.backend.ingest_timeline_event
        server.backend.ingest_timeline_event = lambda **kwargs: self.calls.append(kwargs)

    def tearDown(self):
        server.backend.ingest_timeline_event = self._old

    def _ctx(self, group_id):
        return {
            "actor_id": "2000000001",
            "actor_name": "乐阙",
            "group_id": group_id,
        }

    def test_group_tool_event_uses_tool_plugin_and_archive(self):
        self.handler._ingest_tool_event(self._ctx("784826874"), "bili_comment")
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["tag_primary"], "bili_plugin")
        self.assertEqual(payload["tool_name"], "bili_comment")
        self.assertEqual(payload["tag_secondary"], "arc_master")
        self.assertEqual(payload["tag_permission"], "admin")
        self.assertIn("bili_comment", self.calls[0]["narrative"])
        self.assertEqual(self.calls[0]["source"], "bili_plugin")

    def test_wrapper_resolves_inner_tool_plugin(self):
        tool_call = {
            "function": {
                "name": "call_raw_tool",
                "arguments": '{"tool": "bilibili_hub", "arguments": "{}"}',
            },
        }
        self.handler._ingest_tool_event(self._ctx("784826874"), "call_raw_tool", tool_call)
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["tag_primary"], "bili_plugin")
        self.assertEqual(payload["tool_name"], "bilibili_hub")
        self.assertEqual(payload["tag_secondary"], "arc_master")
        self.assertEqual(payload["tag_permission"], "admin")
        self.assertIn("bilibili_hub", self.calls[0]["narrative"])

    def test_private_tool_event_uses_archive_and_permission(self):
        self.handler._ingest_tool_event(self._ctx(""), "bili_comment")
        payload = self.calls[0]["payload"]
        self.assertEqual(payload["tag_primary"], "bili_plugin")
        self.assertEqual(payload["tool_name"], "bili_comment")
        self.assertEqual(payload["tag_secondary"], "arc_master")
        self.assertEqual(payload["tag_permission"], "admin")


if __name__ == "__main__":
    unittest.main()
