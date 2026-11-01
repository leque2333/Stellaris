# -*- coding: utf-8 -*-
"""记忆库导出 / 导入：原生 bundle、JSONL、备份。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.memory import portability  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class MemoryPortabilityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = SQLiteStore(self.root / "memory.db")
        self.store.append_event(TimelineEvent(
            event_id="evt_portable_1",
            timestamp=1_800_000_000,
            source_module="qq",
            actor_id="master",
            actor_name="乐阙",
            narrative="乐阙说：「导出测试」",
            tag_primary="napcat",
            tag_secondary="arc_master",
            tag_permission="admin",
            person_id="arc_master",
        ))

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_bundle_has_native_tables(self):
        bundle = portability.export_bundle(self.store)
        self.assertEqual(bundle["format"], portability.FORMAT)
        self.assertIn("agent_timeline", bundle["tables"])
        self.assertEqual(bundle["tables"]["agent_timeline"][0]["event_id"], "evt_portable_1")

    def test_import_native_bundle(self):
        bundle = portability.export_bundle(self.store)
        other = SQLiteStore(self.root / "other.db")
        result = portability.import_bundle(bundle, store=other)
        self.assertTrue(result["ok"])
        self.assertTrue(Path(result["backup"]).exists())
        events = other.list_events(limit=10)
        self.assertEqual([e.event_id for e in events], ["evt_portable_1"])
        self.assertEqual(events[0].tag_permission, "admin")

    def test_import_jsonl(self):
        result = portability.import_content(
            '{"role":"user","content":"你好"}\n{"role":"assistant","text":"在呢"}\n',
            fmt="jsonl",
            store=self.store,
        )
        self.assertEqual(result["counts"]["agent_timeline"], 2)
        self.assertGreaterEqual(len(self.store.list_events(limit=10)), 2)

    def test_detect_formats(self):
        self.assertEqual(portability.detect_format('{"format":"stellaris.memory.v1","tables":{}}'), "native")
        self.assertEqual(portability.detect_format('{"role":"user","content":"hi"}'), "jsonl")
        self.assertEqual(portability.detect_format("普通文本\n\n第二段"), "text")

    def test_memory_stats_has_disk_usage(self):
        from agent_core.memory import settings

        stats = settings.memory_stats(self.store)
        self.assertTrue(stats["ok"])
        self.assertGreater(stats["db_disk_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
