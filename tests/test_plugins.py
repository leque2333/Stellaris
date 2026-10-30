# -*- coding: utf-8 -*-
"""动态插件注册表：按协议识别，不按插件名写死"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.plugins import PluginRegistry, probe_path  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class PluginRegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_plugins_"))
        self.data = self.tmp / "components"
        self.data.mkdir()
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.registry = PluginRegistry(self.store)

        db = self.data / "messages.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE messages (id INTEGER, text TEXT)")
        conn.execute("INSERT INTO messages VALUES (1, 'hello')")
        conn.commit()
        conn.close()
        (self.data / "events.json").write_text(
            json.dumps({"events": [{"id": 1, "text": "hi"}]}), encoding="utf-8"
        )
        (self.data / "run.log").write_text("2026-01-01 boot\n", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_probe_detects_generic_protocols(self):
        self.assertEqual(probe_path(self.data / "messages.db")["protocol"], "sqlite")
        self.assertEqual(probe_path(self.data / "events.json")["protocol"], "json")
        self.assertEqual(probe_path(self.data / "run.log")["protocol"], "log")

    def test_scan_registers_plugins_without_names(self):
        result = self.registry.scan([self.data])
        protocols = {p["protocol"] for p in result["registered"]}
        self.assertEqual(protocols, {"sqlite", "json", "log"})
        listed = self.registry.list()
        self.assertEqual(len(listed), 3)
        self.assertTrue(all(p["plugin_id"].startswith("plug_") for p in listed))
        self.assertTrue(all("name" in p for p in listed))

    def test_manual_register_and_delete(self):
        plugin = self.registry.register_manual(
            "自定义组件", str(self.data / "events.json"), "json", {"path": "events.json"}
        )
        self.assertEqual(plugin["protocol"], "json")
        self.assertTrue(self.registry.get(plugin["plugin_id"]))
        self.assertTrue(self.registry.delete(plugin["plugin_id"]))
        self.assertIsNone(self.registry.get(plugin["plugin_id"]))


if __name__ == "__main__":
    unittest.main()
