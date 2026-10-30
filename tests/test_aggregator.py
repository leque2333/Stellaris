# -*- coding: utf-8 -*-
"""L1→L2 聚合器：一段活动只生成一条时间轴摘要"""

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.memory.aggregator import SegmentAggregator  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class AggregatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_agg_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.agg = SegmentAggregator(self.store, idle_seconds=60, max_duration_seconds=3600)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _invocation(self, trace, layer, module, t, **kwargs):
        inv = self.store.record_invocation(trace, layer, module, started_at=t, **kwargs)
        self.store.finish_invocation(inv, ended_at=t + 1, summary="ok")
        return inv

    def test_one_session_becomes_one_event(self):
        t0 = time.time() - 1000
        for i, (layer, module) in enumerate((
            ("channel", "qq"), ("model", "llm.persona"), ("tool", "memory.recall"),
        )):
            self._invocation(f"tr_{i}", layer, module, t0 + i, channel_id="qq", actor_id="master")

        result = self.agg.tick(now=t0 + 5)
        self.assertEqual(result["invocations"], 3)
        self.assertEqual(self.store.timeline_count(), 0)

        result = self.agg.tick(now=t0 + 120)
        self.assertEqual(result["closed"], 1)
        events = self.store.list_events(limit=10, source_module="qq")
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertIn("qq", event.narrative)
        self.assertEqual(event.tag_primary, "QQ")
        self.assertEqual(event.segment_id, events[0].segment_id)

        invs = self.store.list_invocations(segment_id=event.segment_id)
        self.assertEqual(len(invs), 3)
        self.assertTrue(all(i["event_id"] == event.event_id for i in invs))

        # 重复 tick 不重复结算
        self.agg.tick(now=t0 + 130)
        self.assertEqual(self.store.timeline_count(), 1)

    def test_two_sessions_become_two_events(self):
        t0 = time.time() - 2000
        self._invocation("tr_a", "channel", "qq", t0, channel_id="qq", actor_id="master")
        self._invocation("tr_b", "channel", "qq", t0 + 300, channel_id="qq", actor_id="master")

        self.agg.tick(now=t0 + 5)
        self.agg.tick(now=t0 + 120)  # 关闭第一段
        self.agg.tick(now=t0 + 320)
        self.agg.tick(now=t0 + 500)  # 关闭第二段

        events = self.store.list_events(limit=10, source_module="qq")
        self.assertEqual(len(events), 2)

    def test_plugin_activity_aggregation(self):
        t0 = time.time() - 1000
        self.store.insert_activities([
            {
                "activity_id": "act_1", "plugin_id": "bilibili",
                "context_kind": "up", "context_id": "123", "context_name": "某UP",
                "occurred_at": t0, "kind": "action", "verb": "comment",
                "object": "视频A", "outcome": "已发送评论", "actor_name": "助手",
            },
            {
                "activity_id": "act_2", "plugin_id": "bilibili",
                "context_kind": "up", "context_id": "123", "context_name": "某UP",
                "occurred_at": t0 + 30, "kind": "action", "verb": "like",
                "object": "视频A", "outcome": "已点赞", "actor_name": "助手",
            },
        ])
        result = self.agg.tick(now=t0 + 40)
        self.assertEqual(result["activities"], 2)
        self.agg.tick(now=t0 + 200)

        events = self.store.list_events(limit=10, source_module="bilibili")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].tag_primary, "B站")
        self.assertEqual(events[0].tag_secondary, "某UP")
        self.assertIn("视频A", events[0].narrative)


if __name__ == "__main__":
    unittest.main(verbosity=2)
