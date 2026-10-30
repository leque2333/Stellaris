# -*- coding: utf-8 -*-
"""二级插件扫描：标准 metadata 与无 metadata 目录"""

import sys
import tempfile
import unittest
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.adapters.astrbot import ComponentScanner  # noqa: E402
from agent_core.adapters.astrbot import HostResolver  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class ComponentScannerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_components_"))
        plugins = self.tmp / "plugins"
        plugin_a = plugins / "plugin_a"
        plugin_a.mkdir(parents=True)
        (plugin_a / "metadata.yaml").write_text(
            "name: astrbot_plugin_a\ndisplay_name: 测试插件\nversion: 1.2.3\n",
            encoding="utf-8",
        )
        (plugin_a / "_conf_schema.json").write_text("{}", encoding="utf-8")
        (plugin_a / "main.py").write_text(
            '@filter.command("hello")\ndef hello():\n    pass\n',
            encoding="utf-8",
        )
        plugin_b = plugins / "plugin_b"
        plugin_b.mkdir()
        (plugin_b / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (self.tmp / "cmd_config.json").write_text(
            '{"platform":[{"id":"onebot_napcat","type":"aiocqhttp"}]}',
            encoding="utf-8",
        )
        self.root = self.tmp
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.scanner = ComponentScanner(self.store)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_metadata_and_unknown(self):
        found = self.scanner.scan(str(self.root), host_id="astrbot")
        self.assertEqual(len(found), 2)
        rows = {r["component_id"]: r for r in self.store.list_components()}
        self.assertIn("astrbot_plugin_a", rows)
        self.assertEqual(rows["astrbot_plugin_a"]["status"], "active")
        self.assertTrue(rows["astrbot_plugin_a"]["capabilities"])
        self.assertEqual(rows["plugin_b"]["status"], "needs_review")

    def test_delete_component(self):
        self.scanner.scan(str(self.root))
        self.assertTrue(self.store.delete_component("plugin_b"))
        self.assertEqual(len(self.store.list_components()), 1)

    def test_incremental_only_changed(self):
        self.scanner.scan(str(self.root))
        self.assertEqual(self.scanner.scan(str(self.root)), [])
        meta = self.root / "plugins" / "plugin_a" / "metadata.yaml"
        meta.write_text(
            "name: astrbot_plugin_a\ndisplay_name: 改名后的插件\nversion: 2.0.0\n",
            encoding="utf-8",
        )
        changed = self.scanner.scan(str(self.root))
        self.assertEqual([c["component_id"] for c in changed], ["astrbot_plugin_a"])

    def test_host_resolver_identity_and_components(self):
        result = asyncio.run(HostResolver(self.store).resolve(
            str(self.root), {"host_id": "astrbot", "name": "AstrBot"}, use_ai=False
        ))
        self.assertEqual(result["host"]["host_id"], "astrbot")
        self.assertEqual(result["host"]["internal_uid"], "onebot_napcat")
        self.assertEqual(result["count"], 2)
        self.assertTrue(self.store.list_hosts())


if __name__ == "__main__":
    unittest.main()
