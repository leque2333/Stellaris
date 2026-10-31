# -*- coding: utf-8 -*-
"""记忆检索的默认行为：不靠关键词猜，交给模型；兜底预取可手动开。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class BudgetTest(unittest.TestCase):
    def test_no_reserved(self):
        self.assertEqual(server._timeline_budget_with_reserved(0, total=3000), 3000)

    def test_reserved_deducted(self):
        self.assertEqual(server._timeline_budget_with_reserved(1000, total=3000), 2000)

    def test_floor(self):
        self.assertEqual(server._timeline_budget_with_reserved(90_000, total=3000), 700)

    def test_negative_reserved_ignored(self):
        self.assertEqual(server._timeline_budget_with_reserved(-5, total=3000), 3000)


class PrefetchTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="prefetch_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        for index, narrative in enumerate([
            "乐阙说：「小鱼干收到了吗」；安桉回答：「收到了」",
            "乐阙说：「今天吃什么」；安桉回答：「随便」",
        ]):
            self.store.append_event(TimelineEvent(
                event_id=f"e{index}", timestamp=1793000000 + index, source_module="qq",
                actor_id="master", actor_name="master", narrative=narrative,
                person_id="master",
            ))
        self._old = os.environ.pop("HUB_MEMORY_PREFETCH", None)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HUB_MEMORY_PREFETCH", None)
        else:
            os.environ["HUB_MEMORY_PREFETCH"] = self._old

    def _handler(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._memory_store = lambda: self.store
        return handler

    def test_default_is_off(self):
        block = self._handler()._prefetch_memory_block(
            {"user_query": "还记得小鱼干那事吗", "person_id": "master"}
        )
        self.assertEqual(block, "")

    def test_fallback_when_enabled(self):
        os.environ["HUB_MEMORY_PREFETCH"] = "1"
        block = self._handler()._prefetch_memory_block(
            {"user_query": "还记得小鱼干那事吗", "person_id": "master"}
        )
        self.assertIn("记忆库·按时间检索", block)
        self.assertIn("小鱼干", block)

    def test_terms_are_plain_words(self):
        terms = server._prefetch_terms("还记得小鱼干那事吗")
        self.assertTrue(terms)
        self.assertNotIn("吗", terms)
        self.assertNotIn("记得", terms)


if __name__ == "__main__":
    unittest.main()
