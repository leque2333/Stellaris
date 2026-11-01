# -*- coding: utf-8 -*-
"""时间轴有界化：超出保留字数的旧事件搬进缓冲区，之后可以接回。"""

import shutil
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


class ArchivedSearchTest(unittest.TestCase):
    """换页搬走的旧事件：默认不参与检索，深时检索（include_archived）必须能翻到。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="arch_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        for index in range(6):
            self.store.append_event(TimelineEvent(
                event_id=f"a{index}", timestamp=1793000000 + index, source_module="qq",
                actor_id="master", actor_name="乐阙",
                narrative=f"青柠暗号{index}" + "字" * 95,
                person_id="master",
            ))

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_archived_event_is_hidden_by_default(self):
        self.store.release_tail("master", keep_chars=300)
        rows = self.store.search_narratives(["青柠暗号0"], person_ids=["master"], limit=10)
        self.assertEqual(rows, [])

    def test_archived_event_is_recallable_when_requested(self):
        self.store.release_tail("master", keep_chars=300)
        rows = self.store.search_narratives(
            ["青柠暗号0"], person_ids=["master"], limit=10, include_archived=True,
        )
        self.assertEqual([row.event_id for row in rows], ["a0"])

    def test_archived_event_still_visible_in_console_list(self):
        self.store.release_tail("master", keep_chars=300)
        # 控制台时间轴走 list_events，不按 visibility 过滤：界面仍能看到全部
        self.assertEqual(len(self.store.list_events(limit=100)), 6)


class MemoryLookupWiringTest(unittest.TestCase):
    """生产检索路径必须显式带 include_archived，否则 F1 会悄悄回归。"""

    def test_run_memory_lookup_includes_archived(self):
        import server

        calls = {}

        class FakeStore:
            def search_narratives(self, terms, **kwargs):
                calls.update(kwargs)
                return []

        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._memory_store = lambda: FakeStore()
        handler._timeline_restore = lambda ctx: 0
        handler._related_facts = lambda ctx, terms: []
        handler._compress_recall_text = lambda ctx, text, terms=None: text

        out = handler._run_memory_lookup({"person_id": "master"}, {"keywords": "青柠"})
        self.assertIn("没有找到", out)
        self.assertTrue(calls.get("include_archived"))


if __name__ == "__main__":
    unittest.main()
