# -*- coding: utf-8 -*-
"""安全审计日志：写入、去重、读取。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core import audit_log  # noqa: E402


class AuditLogTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="audit_")
        self.path = Path(self.dir) / "audit.log"
        self._old = os.environ.get("HUB_AUDIT_LOG")
        os.environ["HUB_AUDIT_LOG"] = str(self.path)
        audit_log.reset_dedupe()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("HUB_AUDIT_LOG", None)
        else:
            os.environ["HUB_AUDIT_LOG"] = self._old
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)

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

    def test_json_lines_are_valid(self):
        audit_log.record("login_failed", ip="1.1.1.1", username="admin")
        raw = self.path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(raw), 1)
        self.assertEqual(json.loads(raw[0])["event"], "login_failed")

    def test_tail_missing_file(self):
        self.assertEqual(audit_log.tail(5), [])


if __name__ == "__main__":
    unittest.main()
