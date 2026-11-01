# -*- coding: utf-8 -*-
"""工具页「扫描新插件」：清插件缓存 + 重载货架配置 + 返回最新列表。"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

import server  # noqa: E402

from agent_core.tool_shelves import plugin_scan  # noqa: E402


class ToolsRescanApiTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.handler._send_json = lambda data, status=200, extra_headers=None: self.sent.append(
            (status, data)
        )
        self.handler._tools_admin_list = lambda: {
            "ok": True, "count": 3, "tools": [{"name": "set_emotion"}],
            "categories": [],
        }
        self.audit = mock.patch.object(server.audit_log, "record")
        self.audit.start()

    def tearDown(self):
        self.audit.stop()

    def test_rescan_clears_cache_and_reloads(self):
        gate = mock.Mock()
        with mock.patch.object(server, "tool_gate", gate), mock.patch.object(
            plugin_scan, "clear_cache"
        ) as clear:
            self.handler._post_tools_rescan({})
        clear.assert_called_once()
        gate.cfg.reload.assert_called_once_with(force=True)
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(data["rescan"], {"cache_cleared": True, "shelf_reloaded": True})

    def test_rescan_without_gate_still_returns_tools(self):
        with mock.patch.object(server, "tool_gate", None), mock.patch.object(
            plugin_scan, "clear_cache"
        ):
            self.handler._post_tools_rescan({})
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertFalse(data["rescan"]["shelf_reloaded"])


if __name__ == "__main__":
    unittest.main()
