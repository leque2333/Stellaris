# -*- coding: utf-8 -*-
"""自动备份与巡检：配置、定时快照、quick_check、API。"""

import os
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


class BackupConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mm_backup_"))
        self.db = self.tmp / "data" / "memory.db"
        SQLiteStore(self.db).append_event(_event("backup"))
        self.offsite = self.tmp / "offsite"
        self.env = mock.patch.dict(os.environ, {
            snapshots.BACKUP_CONFIG_ENV: str(self.tmp / "memory_backup.json"),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_and_save(self):
        config = snapshots.load_backup_config()
        self.assertFalse(config["enabled"])
        self.assertEqual(config["interval_hours"], 24)
        self.assertTrue(config["check_enabled"])

        saved = snapshots.save_backup_config(
            enabled=True, interval_hours=12, backup_dir=str(self.offsite), check_enabled=False,
        )
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["interval_hours"], 12)
        self.assertTrue(self.offsite.is_dir())
        loaded = snapshots.load_backup_config()
        self.assertEqual(loaded["backup_dir"], str(self.offsite.resolve()))
        self.assertFalse(loaded["check_enabled"])

    def test_rejects_bad_interval(self):
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.save_backup_config(interval_hours=0)
        with self.assertRaises(snapshots.SnapshotError):
            snapshots.save_backup_config(interval_hours=721)

    def test_scheduled_backup_writes_to_offsite(self):
        snapshots.save_backup_config(
            enabled=True, interval_hours=24, backup_dir=str(self.offsite),
        )
        info = snapshots.run_scheduled_backup(self.db)
        self.assertEqual(info["kind"], "scheduled")
        self.assertEqual(Path(info["path"]).parent, self.offsite.resolve())
        config = snapshots.load_backup_config()
        self.assertEqual(config["last_backup_name"], info["name"])
        self.assertFalse(snapshots.backup_due(config))
        names = {item["name"] for item in snapshots.list_snapshots(self.db)}
        self.assertIn(info["name"], names)

    def test_due_rules(self):
        config = {"enabled": True, "interval_hours": 24, "last_backup_at": 0,
                  "last_backup_error_at": 0, "check_enabled": True, "last_check_at": 0}
        self.assertTrue(snapshots.backup_due(config))
        self.assertTrue(snapshots.check_due(config))
        config["last_backup_at"] = time.time()
        config["last_check_at"] = time.time()
        self.assertFalse(snapshots.backup_due(config))
        self.assertFalse(snapshots.check_due(config))

    def test_integrity_check_and_corrupt_file(self):
        result = snapshots.check_integrity(self.db)
        self.assertTrue(result["ok"])
        bad = self.tmp / "bad.db"
        bad.write_text("not sqlite", encoding="utf-8")
        self.assertFalse(snapshots.check_integrity(bad)["ok"])

    def test_backup_tick_runs_due_backup_and_check(self):
        snapshots.save_backup_config(
            enabled=True, interval_hours=24, backup_dir=str(self.offsite), check_enabled=True,
        )
        result = snapshots.backup_tick(self.db)
        self.assertIsNotNone(result["backup"])
        self.assertIsNotNone(result["check"])
        self.assertTrue(result["check"]["ok"])
        self.assertFalse(snapshots.backup_due())
        self.assertFalse(snapshots.check_due())


class BackupApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="mm_backup_api_"))
        self.db = self.tmp / "data" / "memory.db"
        SQLiteStore(self.db).append_event(_event("api"))
        self.offsite = self.tmp / "offsite"
        self.env = mock.patch.dict(os.environ, {
            snapshots.BACKUP_CONFIG_ENV: str(self.tmp / "memory_backup.json"),
        })
        self.env.start()
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
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_get_backup_status(self):
        self.handler._get_memory_backup()
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertFalse(data["config"]["enabled"])

    def test_save_config_and_run_check(self):
        self.handler._post_memory_backup_config({
            "enabled": True, "interval_hours": 6,
            "backup_dir": str(self.offsite), "check_enabled": True,
        })
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(data["config"]["enabled"])
        self.assertEqual(data["config"]["interval_hours"], 6)

        self.handler._post_memory_backup_check({})
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(data["check"]["ok"])

    def test_run_backup(self):
        self.handler._post_memory_backup_config({
            "enabled": True, "backup_dir": str(self.offsite),
        })
        self.handler._post_memory_backup_run({})
        status, data = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertEqual(data["created"]["kind"], "scheduled")
        self.assertGreaterEqual(data["snapshot_count"], 1)

    def test_bad_interval_returns_400(self):
        self.handler._post_memory_backup_config({"interval_hours": 0})
        status, data = self.sent[-1]
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
