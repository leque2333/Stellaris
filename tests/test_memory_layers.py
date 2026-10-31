# -*- coding: utf-8 -*-
"""三层记忆底座：L1 调用索引 + L3 详情库"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.memory import MemoryHub  # noqa: E402
from agent_core.storage.schema import MIGRATIONS  # noqa: E402

LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]
from agent_core.memory.embedding import embed_text_bytes, embed_text_list  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class MemoryLayersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_layers_"))
        self.store = SQLiteStore(self.tmp / "memory.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_schema_has_layers(self):
        self.assertEqual(self.store.schema_version(), LATEST_SCHEMA_VERSION)
        import sqlite3
        with sqlite3.connect(self.tmp / "memory.db") as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("invocations", tables)
        self.assertIn("memory_details", tables)

    def test_l1_invocation_index(self):
        inv1 = self.store.record_invocation(
            "trace_1", "channel", "qq", channel_id="qq", actor_id="master", summary="收到一条群消息"
        )
        inv2 = self.store.record_invocation(
            "trace_1", "model", "llm.persona", summary="调用人格模型"
        )
        self.store.finish_invocation(inv1, status="ok", summary="已处理")
        self.store.finish_invocation(inv2, status="ok")

        rows = self.store.list_invocations(trace_id="trace_1")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["layer"] for r in rows}, {"channel", "model"})
        self.assertTrue(all(r["trace_id"] == "trace_1" for r in rows))

    def test_l3_detail_roundtrip(self):
        inv = self.store.record_invocation("trace_2", "tool", "host_control", tool_name="pc.screenshot")
        detail_id = self.store.save_detail(
            "tool_result",
            {"path": "D:/tmp/shot.jpg", "size": 1234},
            invocation_id=inv,
            event_id="evt_demo",
        )
        row = self.store.get_detail(detail_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["kind"], "tool_result")
        self.assertEqual(row["event_id"], "evt_demo")
        payload = json.loads(row["content"])
        self.assertEqual(payload["path"], "D:/tmp/shot.jpg")

        by_event = self.store.list_details(event_id="evt_demo")
        self.assertEqual(len(by_event), 1)
        by_inv = self.store.list_details(invocation_id=inv)
        self.assertEqual(len(by_inv), 1)

    def test_hub_exposes_three_parts(self):
        hub = MemoryHub(store=self.store)
        self.assertIsNotNone(hub.timeline)
        self.assertIsNotNone(hub.library)
        self.assertIsNotNone(hub.profile)
        inv = hub.timeline.record_invocation("trace_3", "plugin", "bilibili", tool_name="comment.create")
        self.assertIsNotNone(inv)
        detail = hub.library.save_detail("message", "评论原文", invocation_id=inv)
        self.assertEqual(hub.library.get_detail(detail)["content"], "评论原文")
        self.assertFalse(hasattr(hub.library, "add_fact"))


    def test_detail_link_and_session_folding(self):
        first_detail = self.store.save_detail("llm_response", {"message": {"content": "第一轮完整回复"}})
        first = self.store.append_event(
            TimelineEvent(
                event_id="",
                timestamp=time.time(),
                source_module="qq_group:1",
                actor_id="master",
                actor_name="主人",
                narrative="主人说：「第一句」；助手回答：「第一轮」",
                detail_id=first_detail,
            ),
            session_window_seconds=600,
        )
        second_detail = self.store.save_detail("llm_response", {"message": {"content": "第二轮完整回复"}})
        second = self.store.append_event(
            TimelineEvent(
                event_id="",
                timestamp=time.time(),
                source_module="qq_group:1",
                actor_id="master",
                actor_name="主人",
                narrative="主人说：「第二句」；助手回答：「第二轮」",
                detail_id=second_detail,
            ),
            session_window_seconds=600,
        )
        self.assertEqual(first.event_id, second.event_id)
        merged = self.store.get_event(first.event_id)
        self.assertEqual(merged.detail_id, second_detail)
        self.assertEqual((merged.metadata or {}).get("session_count"), 2)
        self.assertIn("连续", merged.narrative)

    def test_embedding_and_vector_recall(self):
        self.store.append_event(TimelineEvent(
            event_id="",
            timestamp=time.time(),
            source_module="qq",
            actor_id="master",
            actor_name="主人",
            narrative="主人说今天加班到十点；助手回答记得早点休息",
            embedding=embed_text_bytes("主人说今天加班到十点；助手回答记得早点休息"),
            importance=2.0,
        ))
        hub = MemoryHub(store=self.store)
        recalls = hub.recall_timeline(
            actor_id="master",
            query="加班",
            query_embedding=embed_text_list("加班"),
            limit=3,
        )
        self.assertTrue(recalls)
        self.assertIn("加班", recalls[0].event.narrative)

    def test_person_id_merges_across_modules(self):
        self.store.append_event(TimelineEvent(
            event_id="",
            timestamp=time.time(),
            source_module="qq_group:1",
            actor_id="master",
            actor_name="主人",
            narrative="主人在群里说周末去看展",
            person_id="1000000001",
            importance=2.0,
        ))
        self.store.append_event(TimelineEvent(
            event_id="",
            timestamp=time.time(),
            source_module="telegram:private",
            actor_id="user_777",
            actor_name="主人",
            narrative="主人在 Telegram 说周末去看展",
            person_id="1000000001",
            importance=2.0,
        ))
        self.store.append_event(TimelineEvent(
            event_id="",
            timestamp=time.time(),
            source_module="qq_group:2",
            actor_id="user_999",
            actor_name="路人",
            narrative="路人说周末去看电影",
            person_id="999",
        ))
        hub = MemoryHub(store=self.store)
        recalls = hub.recall_timeline(
            actor_id="master", person_id="1000000001",
            query="周末 看展", query_embedding=embed_text_list("周末 看展"),
            limit=5, include_all_actors=False,
            exclude_group=False,
        )
        self.assertEqual(len(recalls), 2)
        self.assertTrue(all(r.event.person_id == "1000000001" for r in recalls))

    def test_person_profile_and_affection_unified(self):
        self.store.ensure_archive("master", role="admin", display_name="主人")
        hub = MemoryHub(store=self.store)
        hub.profile.add_fact("1000000001", "喜欢在周末看展")
        self.assertEqual(
            hub.get_user_affection("master"),
            hub.get_user_affection("1000000001"),
        )
        self.assertIn(
            "喜欢在周末看展",
            hub.profile.get_facts_for_prompt("master"),
        )
        self.assertIn(
            "喜欢在周末看展",
            hub.profile.get_facts_for_prompt("1000000001"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

