# -*- coding: utf-8 -*-
"""时间锚点预取：流式/非流式都能用的记忆检索。"""

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


class AnchorTest(unittest.TestCase):
    def test_hits(self):
        for text in ("你还记得我前天说过什么吗", "昨天那个事", "上次聊的那个"):
            self.assertTrue(server._time_anchor_query(text), text)

    def test_misses(self):
        for text in ("今天天气不错", "帮我查一下代码", ""):
            self.assertFalse(server._time_anchor_query(text), text)


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

    def _handler(self):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._memory_store = lambda: self.store
        return handler

    def test_block_when_anchor_and_hit(self):
        block = self._handler()._prefetch_memory_block(
            {"user_query": "还记得小鱼干那事吗", "person_id": "master"}
        )
        self.assertIn("记忆库·按时间检索", block)
        self.assertIn("小鱼干", block)

    def test_empty_without_anchor(self):
        block = self._handler()._prefetch_memory_block(
            {"user_query": "小鱼干收到了吗", "person_id": "master"}
        )
        self.assertEqual(block, "")

    def test_empty_without_hit(self):
        block = self._handler()._prefetch_memory_block(
            {"user_query": "还记得火箭发射吗", "person_id": "master"}
        )
        self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
