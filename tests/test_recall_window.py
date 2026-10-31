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

from agent_core.memory.aggregator import SegmentAggregator, _readable_context  # noqa: E402
from agent_core.memory.episodic_stream import (  # noqa: E402
    _drop_empty_summaries,
    _recall_window,
    _trim_to_budget,
)
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


def _fake_event(event_id, narrative):
    return TimelineEvent(
        event_id=event_id, timestamp=0, source_module="qq", actor_id="master",
        actor_name="master", narrative=narrative,
    )


class ConversationHeaderTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT / "console"))

    def test_group_message(self):
        from server import _split_conversation_id

        self.assertEqual(
            _split_conversation_id("aiocqhttp:GroupMessage:784826874"),
            ("group", "784826874", "aiocqhttp:GroupMessage:784826874"),
        )

    def test_friend_message(self):
        from server import _split_conversation_id

        kind, group, session = _split_conversation_id("aiocqhttp:FriendMessage:2986371638")
        self.assertEqual((kind, group, session), ("private", "", "2986371638"))

    def test_uuid_falls_back_to_conversation(self):
        from server import _split_conversation_id

        kind, group, session = _split_conversation_id("eee4b2e1-be5f-4914-bcbe-a0487e293b1d")
        self.assertEqual(kind, "conversation")
        self.assertEqual(session, "eee4b2e1-be5f-4914-bcbe-a0487e293b1d")

    def test_empty(self):
        from server import _split_conversation_id

        self.assertEqual(_split_conversation_id(""), ("", "", ""))


class RecallWindowTest(unittest.TestCase):
    def test_defaults(self):
        for key in ("HUB_RECALL_CHARS", "HUB_RECALL_DAYS", "HUB_RECALL_CANDIDATES",
                    "HUB_RECALL_EXCLUDE", "HUB_RECALL_HALF_LIFE_HOURS"):
            os.environ.pop(key, None)
        chars, days, limit, excluded = _recall_window()
        self.assertEqual(chars, 300_000)
        self.assertEqual(days, 0.0)  # 默认不按时间窗，按字数预算
        self.assertEqual(limit, 3000)
        self.assertIn("verify", excluded)

    def test_env_override(self):
        os.environ["HUB_RECALL_CHARS"] = "1000"
        os.environ["HUB_RECALL_DAYS"] = "7"
        os.environ["HUB_RECALL_CANDIDATES"] = "50"
        os.environ["HUB_RECALL_EXCLUDE"] = "a,b"
        try:
            chars, days, limit, excluded = _recall_window()
            self.assertEqual(chars, 1000)
            self.assertEqual(days, 7.0)
            self.assertEqual(limit, 50)
            self.assertEqual(excluded, ("a", "b"))
        finally:
            for key in ("HUB_RECALL_CHARS", "HUB_RECALL_DAYS", "HUB_RECALL_CANDIDATES",
                        "HUB_RECALL_EXCLUDE"):
                os.environ.pop(key, None)

    def test_drop_empty_summaries(self):
        empty = _fake_event("a", "qq 群聊 784826874；模块：llm.persona（共 2 个片段）")
        with_highlights = _fake_event("b", "qq 群聊；模块：x（共 3 个片段）。要点：看了视频")
        dialog = _fake_event("c", "某说：「hi」；安桉回答：「嗯」")
        kept = _drop_empty_summaries([empty, with_highlights, dialog])
        self.assertEqual([e.event_id for e in kept], ["b", "c"])

    def test_trim_to_budget(self):
        events = [
            _fake_event("a", "x" * 100),
            _fake_event("b", "y" * 100),
            _fake_event("c", "z" * 100),
        ]
        self.assertEqual([e.event_id for e in _trim_to_budget(events, 250)], ["a", "b"])
        self.assertEqual(len(_trim_to_budget(events, 0)), 3)
        self.assertEqual(len(_trim_to_budget(events, 50)), 1)  # 至少留一条


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

    def test_group_label_is_readable(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master",
            "metadata": {"group_id": "784826874", "channel_type": "group"},
        })
        self.assertEqual(key[3], "qq 群聊 784826874")

    def test_conversation_label_hides_uuid(self):
        uuid = "eee4b2e1-be5f-4914-bcbe-a0487e293b1d"
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master",
            "metadata": {"session_id": uuid, "channel_type": "conversation"},
        })
        self.assertEqual(key[3], "qq 对话")
        self.assertNotIn(uuid, key[3])

    def test_readable_context_replaces_uuid(self):
        uuid = "eee4b2e1-be5f-4914-bcbe-a0487e293b1d"
        label = _readable_context({
            "plugin_id": "qq", "context_name": uuid, "context_kind": "conversation",
        })
        self.assertEqual(label, "qq 对话")

    def test_readable_context_keeps_plain_name(self):
        self.assertEqual(
            _readable_context({"plugin_id": "qq", "context_name": "qq 群聊 784826874"}),
            "qq 群聊 784826874",
        )

    def test_composed_narrative_hides_uuid(self):
        uuid = "eee4b2e1-be5f-4914-bcbe-a0487e293b1d"
        aggregator = SegmentAggregator.__new__(SegmentAggregator)
        seg = {
            "segment_id": "seg_test", "plugin_id": "qq", "context_kind": "conversation",
            "context_id": uuid, "context_name": uuid,
            "started_at": 1793000000.0, "last_at": 1793000060.0,
        }
        event = aggregator._compose_event(
            seg,
            [{"invocation_id": "inv_test", "actor_id": "master", "module_id": "llm.persona"}],
            [],
            1793000060.0,
        )
        self.assertNotIn(uuid, event.narrative)
        self.assertIn("qq 对话", event.narrative)

    def test_composed_narrative_keeps_group_label(self):
        aggregator = SegmentAggregator.__new__(SegmentAggregator)
        seg = {
            "segment_id": "seg_test", "plugin_id": "qq", "context_kind": "group",
            "context_id": "784826874", "context_name": "qq 群聊 784826874",
            "started_at": 1793000000.0, "last_at": 1793000060.0,
        }
        event = aggregator._compose_event(
            seg, [{"invocation_id": "inv_test", "actor_id": "master", "module_id": "llm.persona"}], [], 1793000060.0,
        )
        self.assertIn("qq 群聊 784826874", event.narrative)

    def test_bad_metadata_does_not_crash(self):
        key = SegmentAggregator._key_from_invocation({
            "channel_id": "qq", "actor_id": "master", "metadata": "not-a-dict",
        })
        self.assertEqual(key[2], "master")


if __name__ == "__main__":
    unittest.main()
