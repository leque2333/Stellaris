# -*- coding: utf-8 -*-
"""通用插件工具扫描：AstrBot / NoneBot / Dify / 通用 Python 插件都要能扫到。"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

import server  # noqa: E402

from agent_core.tool_shelves import admin, plugin_scan  # noqa: E402


def _mk_plugin(base: Path, dir_name: str, metadata: str, main_py: str) -> Path:
    path = base / dir_name
    path.mkdir(parents=True)
    (path / "metadata.yaml").write_text(metadata, encoding="utf-8")
    (path / "main.py").write_text(main_py, encoding="utf-8")
    return path


class PluginScanTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="plugin_scan_"))
        _mk_plugin(
            self.dir, "astrbot_plugin_alpha",
            "name: astrbot_plugin_alpha\ndisplay_name: 测试插件A\ndesc: 演示用\n",
            '@filter.llm_tool(name="do_thing")\nasync def do_thing(event):\n    pass\n'
            '@filter.command("hello")\ndef hello():\n    pass\n',
        )
        _mk_plugin(
            self.dir, "plugin_beta",
            "name: plugin_beta\n",
            "@llm_tool\ndef bare_tool(event):\n    pass\n",
        )

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_scans_llm_tools_only(self):
        entries = {item["name"]: item for item in plugin_scan.scan_plugins([self.dir])}
        self.assertIn("do_thing", entries)
        self.assertIn("bare_tool", entries)
        self.assertNotIn("hello", entries)  # 命令不是工具
        self.assertEqual(entries["do_thing"]["plugin"], "测试插件A")
        self.assertEqual(entries["do_thing"]["description"], "演示用")
        self.assertEqual(entries["do_thing"]["framework"], "astrbot")
        self.assertEqual(entries["bare_tool"]["plugin"], "plugin_beta")

    def test_cache_can_be_cleared(self):
        plugin_scan.clear_cache()
        first = plugin_scan.tool_entries([self.dir])
        second = plugin_scan.tool_entries([self.dir])
        self.assertEqual(first, second)
        plugin_scan.clear_cache()


class GenericFrameworkScanTest(unittest.TestCase):
    """同一套扫描器对 NoneBot / Dify / 通用 Python 插件同样生效。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="plugin_generic_"))
        nonebot = self.dir / "nonebot_plugin_demo"
        nonebot.mkdir()
        (nonebot / "pyproject.toml").write_text(
            '[project]\nname = "nonebot_plugin_demo"\ndescription = "一个 NoneBot 插件"\n',
            encoding="utf-8",
        )
        (nonebot / "__init__.py").write_text(
            "from nonebot import on_command\n"
            '@tool(name="nb_search")\ndef nb_search():\n    pass\n',
            encoding="utf-8",
        )

        dify = self.dir / "dify_demo"
        (dify / "tools").mkdir(parents=True)
        (dify / "manifest.yaml").write_text(
            "name: dify_demo\nlabel: Dify 演示\ndescription: 声明式工具\n",
            encoding="utf-8",
        )
        (dify / "tools" / "search.yaml").write_text(
            "identity:\n  name: dify_search\ndescription: 搜索\n",
            encoding="utf-8",
        )

        generic = self.dir / "generic_pack"
        generic.mkdir()
        (generic / "plugin.json").write_text(
            json.dumps({"name": "通用插件", "description": "generic"}), encoding="utf-8"
        )
        (generic / "tools.py").write_text(
            '@function_tool(name="gen_tool")\ndef gen_tool():\n    pass\n',
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_scan_across_frameworks(self):
        entries = {item["name"]: item for item in plugin_scan.scan_plugins([self.dir])}
        self.assertIn("nb_search", entries)
        self.assertEqual(entries["nb_search"]["framework"], "nonebot")
        self.assertIn("dify_search", entries)
        self.assertIn("gen_tool", entries)
        self.assertEqual(entries["gen_tool"]["plugin"], "通用插件")

    def test_non_plugin_dir_is_ignored(self):
        (self.dir / "not_a_plugin").mkdir()
        names = {item["name"] for item in plugin_scan.scan_plugins([self.dir])}
        self.assertNotIn("not_a_plugin", names)


class NestedPluginRootTest(unittest.TestCase):
    """厂商目录（root/<vendor>/<plugin>）也要能扫到。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="plugin_nested_"))
        vendor = self.dir / "vendor_a"
        vendor.mkdir()
        _mk_plugin(
            vendor, "nested_plugin",
            "name: nested_plugin\ndisplay_name: 嵌套插件\n",
            '@tool(name="nested_tool")\ndef nested_tool():\n    pass\n',
        )

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_nested_vendor_directory(self):
        entries = {item["name"]: item for item in plugin_scan.scan_plugins([self.dir])}
        self.assertIn("nested_tool", entries)
        self.assertEqual(entries["nested_tool"]["plugin"], "嵌套插件")


class PluginScanConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="plugin_cfg_"))
        self.plugin_root = self.dir / "plugins"
        _mk_plugin(
            self.plugin_root, "demo",
            "name: demo\ndisplay_name: 演示插件\n",
            '@filter.llm_tool(name="demo_tool")\ndef demo_tool():\n    pass\n',
        )
        self.config = self.dir / "plugin_scan.json"
        self.env = mock.patch.dict(os.environ, {plugin_scan.CONFIG_ENV: str(self.config)})
        self.env.start()
        plugin_scan.clear_cache()

    def tearDown(self):
        self.env.stop()
        plugin_scan.clear_cache()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_save_and_load_roots(self):
        missing = str(self.dir / "not_here")
        saved = plugin_scan.save_config([str(self.plugin_root), missing], auto=False)
        self.assertEqual(saved["config"]["roots"], [str(self.plugin_root.resolve())])
        self.assertIn(missing, saved["invalid"])
        loaded = plugin_scan.load_config()
        self.assertFalse(loaded["auto"])
        self.assertEqual(loaded["roots"], [str(self.plugin_root.resolve())])

    def test_status_reports_plugins_and_tools(self):
        plugin_scan.save_config([str(self.plugin_root)], auto=False)
        data = plugin_scan.status()
        self.assertTrue(data["ok"])
        self.assertEqual(data["plugin_count"], 1)
        self.assertEqual(data["tool_count"], 1)
        self.assertEqual(data["plugins"][0]["tools"], ["demo_tool"])
        self.assertEqual(data["plugins"][0]["framework"], "astrbot")

    def test_auto_off_uses_only_explicit_roots(self):
        plugin_scan.save_config([str(self.plugin_root)], auto=False)
        self.assertEqual(plugin_scan.resolve_roots(), [self.plugin_root.resolve()])


class ToolPageWiringTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="tool_page_"))
        self.cfg = self.dir / "tool_shelves.json"
        self.cfg.write_text(json.dumps({
            "version": 1,
            "always_available": ["known_tool"],
            "categories": {},
            "approval_tools": [],
        }), encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {"TOOL_SHELVES_CONFIG_PATH": str(self.cfg)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_plugin_tool_appears_in_tool_page(self):
        fake = [{
            "name": "plugin_tool", "plugin": "测试插件A", "framework": "astrbot",
            "description": "来自插件", "path": "",
        }]
        with mock.patch.object(plugin_scan, "tool_entries", return_value=fake):
            data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "plugin_tool")
        self.assertEqual(item["source"], "plugin")
        self.assertEqual(item["source_group"], "测试插件A")
        self.assertEqual(item["framework"], "astrbot")
        self.assertEqual(item["mode"], "unknown")

    def test_config_entry_wins_over_plugin_scan(self):
        fake = [{
            "name": "known_tool", "plugin": "测试插件A", "framework": "astrbot",
            "description": "", "path": "",
        }]
        with mock.patch.object(plugin_scan, "tool_entries", return_value=fake):
            data = admin.list_tools()
        item = next(t for t in data["tools"] if t["name"] == "known_tool")
        self.assertEqual(item["source"], "config")
        self.assertEqual(item["mode"], "direct")
        # 自动识别也会刷新分组：分组跟插件走，mode/approval 仍归配置
        self.assertEqual(item["source_group"], "测试插件A")
        self.assertEqual(item["framework"], "astrbot")


class PluginScanApiTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.handler._send_json = lambda data, status=200, extra_headers=None: self.sent.append(
            (status, data)
        )

    def test_get_returns_status(self):
        with mock.patch.object(plugin_scan, "status", return_value={"ok": True, "tool_count": 3}):
            self.handler._get_plugin_scan()
        self.assertEqual(self.sent[-1], (200, {"ok": True, "tool_count": 3}))

    def test_post_splits_lines_and_saves(self):
        with mock.patch.object(
            plugin_scan, "save_config",
            return_value={"config": {"roots": ["D:/a"], "auto": False}, "invalid": []},
        ) as save, mock.patch.object(
            plugin_scan, "status", return_value={"ok": True, "plugins": []},
        ), mock.patch.object(server.audit_log, "record"):
            self.handler._post_plugin_scan({"roots": "D:/a\nD:/b", "auto": False})
        save.assert_called_once_with(roots=["D:/a", "D:/b"], auto=False)
        self.assertTrue(self.sent[-1][1]["ok"])

    def test_post_rejects_bad_roots(self):
        self.handler._post_plugin_scan({"roots": {"x": 1}})
        status, payload = self.sent[-1]
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
