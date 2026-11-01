# -*- coding: utf-8 -*-
"""时间轴页面：纯模块聚合摘要不该混进给用户看的记忆流。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402
from console.modules import system_service  # noqa: E402


class TimelineDisplayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="timeline_display_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self._old_get_store = system_service.get_store
        system_service.get_store = lambda: self.store

    def tearDown(self):
        system_service.get_store = self._old_get_store
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _event(self, event_id, timestamp, narrative, **kwargs):
        payload = dict(
            event_id=event_id, timestamp=timestamp, source_module="qq",
            actor_id="master", actor_name="乐阙", narrative=narrative,
            person_id="arc_master",
        )
        payload.update(kwargs)
        self.store.append_event(TimelineEvent(**payload))

    def test_summary_without_highlights_is_hidden(self):
        self._event("summary", 1_800_000_000, "master；模块：llm.persona、qq（共 4 个片段）")
        self.assertEqual(system_service.get_timeline_events(limit=10), [])

    def test_dialogue_is_kept(self):
        self._event("chat", 1_800_000_001, "乐阙说：「在吗」；安桉回答：「在呢」")
        rows = system_service.get_timeline_events(limit=10)
        self.assertEqual([item["id"] for item in rows], ["chat"])

    def test_tags_split_plugin_platform_and_uid(self):
        self._event(
            "split", 1_800_000_003, "乐阙说：「在吗」",
            tag_primary="napcat", tag_secondary="qq:10001",
            tag_permission="guest", metadata={"platform": "qq", "sender_uid": "10001"},
        )
        row = system_service.get_timeline_events(limit=10)[0]
        self.assertEqual(row["tag_primary"], "napcat")
        self.assertEqual(row["platform"], "qq")
        self.assertEqual(row["uid"], "10001")
        self.assertEqual(row["display_name"], "10001")

    def test_summary_with_highlights_is_kept(self):
        self._event("summary_hit", 1_800_000_002,
                    "qq；模块：llm.persona（共 2 个片段）。要点：看了视频")
        rows = system_service.get_timeline_events(limit=10)
        self.assertEqual([item["id"] for item in rows], ["summary_hit"])


if __name__ == "__main__":
    unittest.main()
