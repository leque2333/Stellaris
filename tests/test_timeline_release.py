# -*- coding: utf-8 -*-
"""时间轴有界化：超出保留字数的旧事件搬进缓冲区，之后可以接回。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class ReleaseTailTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rel_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        for index in range(6):
            self.store.append_event(TimelineEvent(
                event_id=f"e{index}", timestamp=1793000000 + index, source_module="qq",
                actor_id="master", actor_name="乐阙", narrative="字" * 100,
                person_id="master",
            ))

    def _count(self):
        return len(self.store.list_candidates(person_ids=["master"], limit=100))

    def test_release_keeps_newest(self):
        moved = self.store.release_tail("master", keep_chars=300)
        self.assertEqual(moved, 3)
        rows = self.store.list_candidates(person_ids=["master"], limit=10)
        self.assertEqual([r.event_id for r in rows], ["e5", "e4", "e3"])
        self.assertEqual(len(self.store.peek_buffer("master")), 3)

    def test_release_nothing_when_within_budget(self):
        self.assertEqual(self.store.release_tail("master", keep_chars=10_000), 0)
        self.assertEqual(self._count(), 6)

    def test_restore_puts_back(self):
        self.store.release_tail("master", keep_chars=300)
        restored = self.store.restore_buffer("master")
        self.assertEqual(restored, 3)
        self.assertEqual(self._count(), 6)
        self.assertEqual(self.store.peek_buffer("master"), [])

    def test_restore_is_idempotent(self):
        self.store.release_tail("master", keep_chars=300)
        self.store.restore_buffer("master")
        self.assertEqual(self.store.restore_buffer("master"), 0)
        self.assertEqual(self._count(), 6)

    def test_release_preserves_fields(self):
        self.store.release_tail("master", keep_chars=300)
        payload = self.store.peek_buffer("master")[0]
        self.assertIn("narrative", payload)
        self.assertIn("timestamp", payload)
        self.assertEqual(payload["person_id"], "master")


class HandlerWiringTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="wire_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        for index in range(8):
            self.store.append_event(TimelineEvent(
                event_id=f"w{index}", timestamp=1793000000 + index, source_module="qq",
                actor_id="master", actor_name="乐阙", narrative="字" * 2000,
                person_id="master",
            ))

    def _handler(self):
        import server
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._memory_store = lambda: self.store
        return handler

    def test_release_keeps_timeline_bounded(self):
        handler = self._handler()
        handler._timeline_release({"person_id": "master"})
        rows = self.store.list_candidates(person_ids=["master"], limit=100)
        self.assertLessEqual(sum(len(r.narrative) for r in rows), 4_000)
        self.assertGreater(len(self.store.peek_buffer("master")), 0)

    def test_restore_only_when_room(self):
        handler = self._handler()
        handler._timeline_release({"person_id": "master"})
        before = len(self.store.list_candidates(person_ids=["master"], limit=100))
        handler._timeline_restore({"person_id": "master"})
        after = len(self.store.list_candidates(person_ids=["master"], limit=100))
        self.assertGreaterEqual(after, before)


if __name__ == "__main__":
    unittest.main()
