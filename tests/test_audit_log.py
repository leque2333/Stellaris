# -*- coding: utf-8 -*-
"""安全审计日志：写入、去重、读取、保留策略。"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core import audit_log  # noqa: E402


class AuditLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="audit_")
        self._env = {
            key: os.environ.get(key)
            for key in ("HUB_AUDIT_DIR", "HUB_AUDIT_MAX_FILE_MB",
                        "HUB_AUDIT_RETENTION_DAYS", "HUB_AUDIT_MAX_TOTAL_MB")
        }
        os.environ["HUB_AUDIT_DIR"] = self.dir
        os.environ["HUB_AUDIT_MAX_FILE_MB"] = "1"
        os.environ["HUB_AUDIT_RETENTION_DAYS"] = "30"
        os.environ["HUB_AUDIT_MAX_TOTAL_MB"] = "64"
        audit_log.reset_dedupe()
        audit_log._LAST_PRUNE = 0.0
        self.path = audit_log.log_path()

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

    def _write_file(self, name, lines=1):
        path = Path(self.dir) / name
        path.write_text("\n".join(json.dumps({"event": "x"}) for _ in range(lines)) + "\n",
                        encoding="utf-8")
        return path

    def test_record_and_tail(self):
        audit_log.record("login_ok", ip="127.0.0.1", username="admin")
        audit_log.record("security_changed", ip="127.0.0.1", local_only=True)
        entries = audit_log.tail(10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["event"], "security_changed")
        self.assertEqual(entries[1]["event"], "login_ok")
        self.assertTrue(self.path.exists())

    def test_dedupe_same_event(self):
        audit_log.record("signature_rejected", ip="8.8.8.8", error="bad_signature")
        audit_log.record("signature_rejected", ip="8.8.8.8", error="bad_signature")
        self.assertEqual(len(audit_log.tail(10)), 1)

    def test_dedupe_separates_keys(self):
        audit_log.record("signature_rejected", ip="8.8.8.8", error="bad_signature")
        audit_log.record("signature_rejected", ip="8.8.4.4", error="bad_signature")
        audit_log.record("signature_rejected", ip="8.8.8.8", error="stale_timestamp")
        self.assertEqual(len(audit_log.tail(10)), 3)

    def test_tail_missing_dir(self):
        self.assertEqual(audit_log.tail(5), [])

    def test_file_name_has_date(self):
        self.assertRegex(self.path.name, r"^audit-\d{4}-\d{2}-\d{2}\.log$")

    def test_rotate_when_file_full(self):
        os.environ["HUB_AUDIT_MAX_FILE_MB"] = "0.002"  # 约 2 KB
        for index in range(40):
            audit_log.record("login_failed", dedupe=False, ip="1.1.1.1", n=index)
        names = sorted(item.name for item in Path(self.dir).iterdir())
        self.assertGreaterEqual(len(names), 2)
        self.assertTrue(any(name.endswith(".1.log") for name in names))
        self.assertEqual(len(audit_log.tail(200)), 40)

    def test_prune_by_age(self):
        old = self._write_file("audit-2020-01-01.log")
        os.utime(old, (time.time() - 40 * 86400, time.time() - 40 * 86400))
        fresh = self._write_file("audit-2030-01-01.log")
        removed = audit_log.prune()
        self.assertIn(old.name, removed)
        self.assertTrue(fresh.exists())

    def test_prune_by_total_size(self):
        os.environ["HUB_AUDIT_MAX_TOTAL_MB"] = "0.001"
        first = self._write_file("audit-2026-01-01.log", lines=60)
        second = self._write_file("audit-2026-01-02.log", lines=60)
        removed = audit_log.prune()
        self.assertIn(first.name, removed)
        self.assertTrue(second.exists())

    def test_stats(self):
        self._write_file("audit-2026-02-01.log")
        info = audit_log.stats()
        self.assertEqual(info["files"], 1)
        self.assertGreater(info["bytes"], 0)
        self.assertEqual(info["retention_days"], 30.0)
        self.assertEqual(info["oldest"], "audit-2026-02-01.log")

    def test_compress_old_file(self):
        os.environ["HUB_AUDIT_COMPRESS_AFTER_DAYS"] = "1"
        old = self._write_file("audit-2026-01-01.log", lines=2)
        os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        fresh = self._write_file("audit-2026-01-02.log", lines=2)
        done = audit_log.compress_old()
        self.assertIn(old.name + ".gz", done)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())
        self.assertEqual(len(audit_log.tail(10)), 4)
        # 压缩后保留原时间，否则保留策略会从压缩时刻重算
        self.assertLess(abs((Path(self.dir) / (old.name + ".gz")).stat().st_mtime
                            - (time.time() - 3 * 86400)), 120)

    def test_tail_reads_gz(self):
        os.environ["HUB_AUDIT_COMPRESS_AFTER_DAYS"] = "1"
        old = self._write_file("audit-2026-01-01.log", lines=3)
        os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        audit_log.compress_old()
        events = audit_log.tail(10)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["event"], "x")

    def test_stats_counts_compressed(self):
        os.environ["HUB_AUDIT_COMPRESS_AFTER_DAYS"] = "1"
        old = self._write_file("audit-2026-01-01.log")
        os.utime(old, (time.time() - 3 * 86400, time.time() - 3 * 86400))
        audit_log.compress_old()
        self.assertEqual(audit_log.stats()["compressed"], 1)

    def test_migrate_legacy(self):
        legacy = Path(self.dir) / "audit.log"
        legacy.write_text('{"event": "old"}\n', encoding="utf-8")
        moved = audit_log.migrate_legacy()
        self.assertTrue(moved and moved.startswith("audit-"))
        self.assertFalse(legacy.exists())


if __name__ == "__main__":
    unittest.main()
