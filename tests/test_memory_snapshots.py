# -*- coding: utf-8 -*-
"""记忆库快照：整库副本的列出 / 手动备份 / 恢复 / 删除。"""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

import server  # noqa: E402

from agent_core.memory import snapshots  # noqa: E402
from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


def _event(text: str) -> TimelineEvent:
    return TimelineEvent(
        event_id="", timestamp=time.time(), source_module="test",
        actor_id="u1", actor_name="用户", narrative=text,
        person_id="u1", importance=1.0,
    )


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="snapshot_"))
        self.db = self.tmp / "data" / "memory.db"
        self.store = SQLiteStore(self.db)
        for text in ("第一条", "第二条", "第三条"):
            self.store.append_event(_event(text))
        # 首次建库会顺带生成一份 pre_migrate 快照，作为基线
        self.baseline = {item["name"] for item in snapshots.list_snapshots(db_path=self.db)}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _count(self) -> int:
        return len(self.store.list_events(limit=100))

    def test_create_and_list(self):
        created = snapshots.create_snapshot("manual", db_path=self.db)
        self.assertTrue(Path(created["path"]).is_file())
        self.assertEqual(created["kind"], "manual")
        self.assertEqual(created["kind_label"], "手动")
        data = snapshots.status(db_path=self.db)
        self.assertEqual(data["count"], len(self.baseline) + 1)
        self.assertGreater(data["total_bytes"], 0)
        manual = [item for item in data["items"] if item["kind"] == "manual"]
        self.assertEqual([item["name"] for item in manual], [created["name"]])

    def test_restore_round_trip_with_safety_snapshot(self):
        snap = snapshots.create_snapshot("manual", db_path=self.db)
        self.store.append_event(_event("快照之后新增"))
        self.assertEqual(self._count(), 4)
        graph = self.db.parent / "memory_graph.db"
        graph.write_bytes(b"stale graph")

        result = snapshots.restore_snapshot(snap["name"], db_path=self.db)
        self.assertEqual(self._count(), 3)
        self.assertTrue(Path(result["safety"]["path"]).is_file())
        self.assertEqual(result["safety"]["kind"], "restore")
        self.assertFalse(graph.exists())  # 图库不随快照恢复，清空后重建

        # 安全快照保留恢复前的 4 条
        names = {item["name"] for item in snapshots.list_snapshots(db_path=self.db)}
        self.assertIn(result["safety"]["name"], names)

    def test_delete_removes_only_snapshot(self):
        snap = snapshots.create_snapshot("manual", db_path=self.db)
        removed = snapshots.delete_snapshot(snap["name"], db_path=self.db)
        self.assertEqual(removed["name"], snap["name"])
        self.assertFalse(Path(snap["path"]).exists())
        self.assertEqual(snapshots.status(db_path=self.db)["count"], len(self.baseline))
        self.assertEqual(self._count(), 3)  # 当前库不动

    def test_rejects_path_traversal_and_bad_snapshot(self):
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.restore_snapshot("../memory.db", db_path=self.db)
        bad = snapshots.snapshot_dirs(self.db)[0] / "manual_20260101_000000_memory.db"
        bad.write_text("not a sqlite db", encoding="utf-8")
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.restore_snapshot(bad.name, db_path=self.db)
        self.assertEqual(self._count(), 3)

    def test_missing_snapshot(self):
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.restore_snapshot("manual_20200101_000000_memory.db", db_path=self.db)


class SnapshotApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="snapshot_api_"))
        self.db = self.tmp / "data" / "memory.db"
        SQLiteStore(self.db).append_event(_event("api"))
        self.baseline_count = len(snapshots.list_snapshots(db_path=self.db))
        self.sent = []
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.handler._send_json = lambda data, status=200, extra_headers=None: self.sent.append(
            (status, data)
        )
        self.handler._memory_store = lambda: mock.Mock(path=self.db)
        self.audit = mock.patch.object(server.audit_log, "record")
        self.audit.start()

    def tearDown(self):
        self.audit.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_lists_snapshots(self):
        self.handler._get_memory_snapshots()
        status, payload = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["count"], 1)

    def test_create_then_restore_then_delete(self):
        self.handler._post_memory_snapshot_create({})
        status, created = self.sent[-1]
        self.assertEqual(status, 200)
        name = created["created"]["name"]
        self.assertEqual(created["count"], self.baseline_count + 1)

        self.handler._post_memory_snapshot_restore({"name": name})
        status, restored = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(restored["restored"]["name"], name)

        self.handler._post_memory_snapshot_delete({"name": name})
        status, deleted = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(deleted["removed"]["name"], name)

    def test_restore_requires_name(self):
        self.handler._post_memory_snapshot_restore({})
        status, payload = self.sent[-1]
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()
