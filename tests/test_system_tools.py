# -*- coding: utf-8 -*-
"""系统工具：工具页展示与启停，不绑定具体宿主。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.tool_shelves import admin, system_tools  # noqa: E402


class SystemToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.shelf = root / "tool_shelves.json"
        self.shelf.write_text(json.dumps({
            "always_available": [],
            "categories": {},
            "approval_tools": [],
        }), encoding="utf-8")
        self.env = patch.dict("os.environ", {
            "HUB_SYSTEM_TOOLS_PATH": str(root / "system_tools.json"),
            "TOOL_SHELVES_CONFIG_PATH": str(self.shelf),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_both_host_controls_are_system_tools(self):
        data = admin.list_tools()
        by_name = {item["name"]: item for item in data["tools"]}
        for name in ("control_host_pc_via_antigravity", "hub_host_control"):
            self.assertTrue(by_name[name]["system"])
            self.assertEqual(by_name[name]["source_group"], "系统工具")

    def test_disable_roundtrip(self):
        admin.update_tool("hub_host_control", enabled=False)
        self.assertFalse(system_tools.enabled("hub_host_control"))
        data = admin.list_tools()
        item = next(i for i in data["tools"] if i["name"] == "hub_host_control")
        self.assertFalse(item["enabled"])
        admin.update_tool("hub_host_control", enabled=True)
        self.assertTrue(system_tools.enabled("hub_host_control"))


if __name__ == "__main__":
    unittest.main()
