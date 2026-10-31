# -*- coding: utf-8 -*-
"""检索写回：回忆条目压缩一次后写到时间轴最前面，且不重复灌。"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core.memory.deep_recall import Hit  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class NoDoubleCompressTest(unittest.TestCase):
    def test_flagged_event_not_recompressed(self):
        import sys
        sys.path.insert(0, str(ROOT))
        from agent_core.memory import timeline_curve
        from agent_core.models import TimelineEvent

        event = TimelineEvent(
            event_id="e1", timestamp=1, source_module="memory_recall",
            actor_id="master", actor_name="乐阙", narrative="回忆：" + "字" * 400,
            metadata={"compressed": True},
        )
        entries = timeline_curve.build_curve([event], budget_chars=10_000)
        self.assertEqual(entries[0].kept_chars, entries[0].original_chars)


class WritebackTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="writeback_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        self.session = {"person_id": "master", "actor_id": "master", "actor_name": "乐阙"}

    def _hits(self, count=3, length=60):
        return [
            Hit(f"e{index}", "2026-10-29 10:00:00", 1793000000 + index,
                "旧事" + "字" * length, "qq")
            for index in range(count)
        ]

    def test_writes_one_event(self):
        text = server._recall_writeback(self.store, self.session, self._hits(), "full")
        self.assertTrue(text.startswith("回忆："))
        rows = self.store.list_candidates(person_ids=["master"], limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source_module, "memory_recall")
        self.assertLessEqual(len(rows[0].narrative), len(text) + 2)

    def test_compressed_once(self):
        hits = self._hits(3, 200)
        text = server._recall_writeback(self.store, self.session, hits, "full")
        raw = "回忆：" + "；".join(hit.narrative for hit in hits[:3])
        self.assertLess(len(text), len(raw))

    def test_dedupe_within_window(self):
        server._recall_writeback(self.store, self.session, self._hits(), "full")
        again = server._recall_writeback(self.store, self.session, self._hits(), "full")
        self.assertEqual(again, "")
        self.assertEqual(len(self.store.list_candidates(person_ids=["master"], limit=5)), 1)

    def test_timestamp_is_now(self):
        server._recall_writeback(self.store, self.session, self._hits(), "full")
        row = self.store.list_candidates(person_ids=["master"], limit=1)[0]
        self.assertLess(abs(row.timestamp - time.time()), 5)

    def test_empty_hits_noop(self):
        self.assertEqual(server._recall_writeback(self.store, self.session, [], "full"), "")


if __name__ == "__main__":
    unittest.main()
