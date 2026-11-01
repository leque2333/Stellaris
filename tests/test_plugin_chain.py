# -*- coding: utf-8 -*-
"""插件调用链：A 调 B 时主标签显示 A → B，不写死具体插件。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class PluginChainTest(unittest.TestCase):
    def setUp(self):
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)

    def test_explicit_chain(self):
        ctx = {"plugin_chain": "napcat,bilibili", "platform": "qq", "sender_uid": "1"}
        self.assertEqual(self.handler._plugin_chain(ctx), "napcat → bilibili")
        self.assertEqual(self.handler._timeline_plugin_name(ctx), "napcat → bilibili")

    def test_caller_and_host(self):
        ctx = {"caller_plugin": "napcat", "host_plugin": "bilibili"}
        self.assertEqual(self.handler._plugin_chain(ctx), "napcat → bilibili")

    def test_single_plugin_falls_back(self):
        ctx = {"plugin_chain": "", "caller_plugin": "", "host_plugin": "napcat"}
        self.assertEqual(self.handler._timeline_plugin_name(ctx), "napcat")

    def test_initiator_tag_is_archive_id(self):
        ctx = {"archive_id": "arc_master", "actor_id": "master", "sender_uid": "2000000001"}
        self.assertEqual(self.handler._timeline_initiator_tag(ctx), "arc_master")
        self.assertEqual(self.handler._timeline_tag_secondary(ctx), "arc_master")

    def test_initiator_falls_back_to_platform_uid_without_archive(self):
        ctx = {"platform": "qq", "sender_uid": "10001", "actor_id": "user_10001"}
        self.assertEqual(self.handler._timeline_initiator_tag(ctx), "qq:10001")

    def test_initiator_falls_back_to_actor_without_platform(self):
        ctx = {"actor_id": "user_123"}
        self.assertEqual(self.handler._timeline_initiator_tag(ctx), "user_123")

    def test_session_uuid_is_not_a_plugin_name(self):
        ctx = {
            "platform": "web",
            "session_id": "eee4b2e1-be5f-4914-bcbe-a0487e293b1d",
            "sender_uid": "2000000001",
        }
        self.assertEqual(self.handler._timeline_plugin_name(ctx), "web")


if __name__ == "__main__":
    unittest.main()
