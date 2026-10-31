# -*- coding: utf-8 -*-
"""时间轴缓冲区：落库、先进先出、按人隔离、重启还在。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

from agent_core.storage import SQLiteStore  # noqa: E402


class BufferTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="buffer_"))
        self.db = self.dir / "memory.db"
        self.store = SQLiteStore(self.db)

    def _events(self, count, length=50):
        return [
            {"event_id": f"e{index}", "narrative": "字" * length,
             "timestamp": 1793000000 + index, "source_module": "qq"}
            for index in range(count)
        ]

    def test_push_and_peek(self):
        self.store.push_buffer("master", self._events(3))
        rows = self.store.peek_buffer("master")
        self.assertEqual([row["event_id"] for row in rows], ["e0", "e1", "e2"])
        self.assertEqual(len(self.store.peek_buffer("master")), 3)  # peek 不删

    def test_pop_is_fifo_and_removes(self):
        self.store.push_buffer("master", self._events(3))
        first = self.store.pop_buffer("master", limit=2)
        self.assertEqual([row["event_id"] for row in first], ["e0", "e1"])
        rest = self.store.peek_buffer("master")
        self.assertEqual([row["event_id"] for row in rest], ["e2"])

    def test_person_isolation(self):
        self.store.push_buffer("master", self._events(2))
        self.store.push_buffer("other", self._events(1))
        self.assertEqual(len(self.store.peek_buffer("master")), 2)
        self.assertEqual(len(self.store.peek_buffer("other")), 1)
        self.store.clear_buffer("master")
        self.assertEqual(self.store.peek_buffer("master"), [])
        self.assertEqual(len(self.store.peek_buffer("other")), 1)

    def test_stats(self):
        self.store.push_buffer("master", self._events(2, length=10))
        stats = self.store.buffer_stats("master")
        self.assertEqual(stats["events"], 2)
        self.assertEqual(stats["chars"], 20)

    def test_survives_restart(self):
        self.store.push_buffer("master", self._events(2))
        self.store.close()
        reopened = SQLiteStore(self.db)
        self.assertEqual(len(reopened.peek_buffer("master")), 2)

    def test_schema_version_bumped(self):
        self.assertGreaterEqual(self.store.schema_version(), 27)

    def test_pop_empty(self):
        self.assertEqual(self.store.pop_buffer("nobody"), [])


if __name__ == "__main__":
    unittest.main()
