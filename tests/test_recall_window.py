# -*- coding: utf-8 -*-
"""召回窗口（时间窗/来源过滤）与聚合分段键（群聊/私聊）。"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core.memory.aggregator import SegmentAggregator  # noqa: E402
from agent_core.memory.episodic_stream import _recall_window  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


class RecallWindowTest(unittest.TestCase):
    def test_defaults(self):
        for key in ("HUB_RECALL_DAYS", "HUB_RECALL_CANDIDATES", "HUB_RECALL_EXCLUDE"):
            os.environ.pop(key, None)
        days, limit, excluded = _recall_window()
        self.assertEqual(days, 180.0)
        self.assertEqual(limit, 3000)
        self.assertIn("verify", excluded)

    def test_env_override(self):
        os.environ["HUB_RECALL_DAYS"] = "7"
        os.environ["HUB_RECALL_CANDIDATES"] = "50"
        os.environ["HUB_RECALL_EXCLUDE"] = "a,b"
        try:
            days, limit, excluded = _recall_window()
            self.assertEqual(days, 7.0)
            self.assertEqual(limit, 50)
            self.assertEqual(excluded, ("a", "b"))
        finally:
            for key in ("HUB_RECALL_DAYS", "HUB_RECALL_CANDIDATES", "HUB_RECALL_EXCLUDE"):
                os.environ.pop(key, None)


class CandidateFilterTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="recall_"))
        self.store = SQLiteStore(self.dir / "memory.db")

    def _event(self, event_id, source, ts, actor="master"):
        event = TimelineEvent(
            event_id=event_id, time_str="2026-10-31 10:00:00", source_module=source,
            actor_id=actor, actor_name=actor, narrative=f"{source} 的事件", timestamp=ts,
        )
        return event

    def test_since_ts_filters_old_events(self):
        now = 1793000000.0
        self.store.append_event(self._event("old", "qq", now - 10 * 86400))
        self.store.append_event(self._event("new", "qq", now - 60))
        rows = self.store.list_candidates(actor_ids=["master"], since_ts=now - 86400, limit=100)
        self.assertEqual([row.event_id for row in rows], ["new"])

    def test_exclude_sources(self):
        self.store.append_event(self._event("a", "qq", 1793000000.0))
        self.store.append_event(self._event("b", "verify", 1793000001.0))
        rows = self.store.list_candidates(
            actor_ids=["master"], exclude_sources=["verify"], limit=100
        )
        self.assertEqual([row.event_id for row in rows], ["a"])

    def test_time_window_keeps_more_than_row_cap(self):
        base = 1793000000.0
        for index in range(30):
            self.store.append_event(self._event(f"e{index:02d}", "qq", base + index))
        rows = self.store.list_candidates(actor_ids=["master"], limit=5)
        self.assertEqual(len(rows), 5)


class SegmentKeyTest(unittest.TestCase):
    def test_group_uses_group_id(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master",
            "metadata": {"group_id": "784826874", "channel_type": "group"},
        })
        self.assertEqual(key[1], "group")
        self.assertEqual(key[2], "784826874")

    def test_private_uses_session(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master",
            "metadata": {"session_id": "friend:2986371638", "channel_type": "private"},
        })
        self.assertEqual(key[1], "private")
        self.assertEqual(key[2], "friend:2986371638")

    def test_falls_back_to_actor(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master", "metadata": {},
        })
        self.assertEqual(key[2], "master")

    def test_bad_metadata_does_not_crash(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master", "metadata": "not-a-dict",
        })
        self.assertEqual(key[2], "master")


if __name__ == "__main__":
    unittest.main()
