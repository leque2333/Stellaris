# -*- coding: utf-8 -*-
"""记忆召回：同群共享，不同群/私聊隔离"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402
from agent_core.timeline_hub import TimelineHub  # noqa: E402


class RecallScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_policy_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.hub = TimelineHub(store=self.store)
        self.hub.append_event(source_module="test", actor_id="alice", narrative="alice 的秘密是红苹果")
        self.hub.append_event(source_module="test", actor_id="bob", narrative="bob 的秘密是蓝风筝")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_isolated_by_default(self):
        rows = self.hub.recall_timeline(actor_id="alice", query="秘密", limit=5)
        self.assertTrue(rows)
        self.assertTrue(all(r.event.actor_id == "alice" for r in rows))

    def test_include_all_actors(self):
        rows = self.hub.recall_timeline(
            actor_id="alice", query="蓝风筝", limit=5, include_all_actors=True
        )
        self.assertTrue(any(r.event.actor_id == "bob" for r in rows))

    def test_group_channel_recall(self):
        self.hub.append_event(
            source_module="qq_group:g1", actor_id="carol", narrative="群里说暗号是绿竹子"
        )
        self.hub.append_event(
            source_module="qq_group:g2", actor_id="dave", narrative="另一个群说暗号是黄柠檬"
        )
        self.hub.append_event(
            source_module="private_chat", actor_id="master", narrative="私聊说暗号是黑咖啡"
        )
        rows = self.hub.recall_timeline(
            actor_id="alice", query="暗号", limit=5,
            channel_id="qq_group:g1", include_channel=True,
        )
        self.assertTrue(any(r.event.actor_id == "carol" for r in rows))
        self.assertFalse(any(r.event.actor_id == "dave" for r in rows))
        self.assertFalse(any(r.event.actor_id == "master" for r in rows))

    def test_private_excludes_groups(self):
        self.hub.append_event(
            source_module="qq_group:g1", actor_id="master", narrative="群里说暗号是红玫瑰"
        )
        rows = self.hub.recall_timeline(
            actor_id="master", query="暗号", limit=5, exclude_group=True
        )
        self.assertFalse(any(r.event.narrative.startswith("群里") for r in rows))

    def test_master_private_sees_all(self):
        self.hub.append_event(
            source_module="qq_group:g1", actor_id="alice", narrative="群一说暗号是红玫瑰"
        )
        self.hub.append_event(
            source_module="qq_group:g2", actor_id="bob", narrative="群二说暗号是白百合"
        )
        self.hub.append_event(
            source_module="private_chat", actor_id="carol", narrative="私聊说暗号是黑咖啡"
        )
        rows = self.hub.recall_timeline(
            actor_id="master", query="暗号", limit=8,
            include_all_actors=True, exclude_group=False,
        )
        actors = {r.event.actor_id for r in rows}
        self.assertTrue({"alice", "bob", "carol"}.issubset(actors))


if __name__ == "__main__":
    unittest.main()
