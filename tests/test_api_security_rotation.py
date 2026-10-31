# -*- coding: utf-8 -*-
"""密钥轮换 + 登录失败限速。"""

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

from agent_core import api_security  # noqa: E402


class _Config:
    def __init__(self, payload):
        self.dir = tempfile.mkdtemp(prefix="api_sec_rot_")
        self.path = Path(self.dir) / "api_security.json"
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self._old = os.environ.get("HUB_API_SECURITY_PATH")
        os.environ["HUB_API_SECURITY_PATH"] = str(self.path)

    def close(self):
        if self._old is None:
            os.environ.pop("HUB_API_SECURITY_PATH", None)
        else:
            os.environ["HUB_API_SECURITY_PATH"] = self._old
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


class RotationTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _Config({"signature": {"enabled": True, "secret": "old-secret"}})

    def tearDown(self):
        self.cfg.close()

    def _sig(self, secret, ts, body=b"{}"):
        return api_security.sign("POST", "/v1/x", ts, body, secret)

    def test_rotate_keeps_old_secret_valid(self):
        now = int(time.time())
        ts = str(now)
        api_security.rotate_secret(grace_seconds=600)
        self.assertIsNone(
            api_security.signature_error("POST", "/v1/x", ts, self._sig("old-secret", ts), b"{}",
                                         now=now)
        )

    def test_new_secret_works_immediately(self):
        now = int(time.time())
        ts = str(now)
        saved = api_security.rotate_secret(grace_seconds=600)
        new_secret = saved["signature"]["secret"]
        self.assertIsNone(
            api_security.signature_error("POST", "/v1/x", ts, self._sig(new_secret, ts), b"{}",
                                         now=now)
        )

    def test_old_secret_expires_after_grace(self):
        now = int(time.time())
        ts = str(now)
        api_security.rotate_secret(grace_seconds=60)
        self.assertEqual(
            api_security.signature_error(
                "POST", "/v1/x", ts, self._sig("old-secret", ts), b"{}", now=now + 120
            ),
            "bad_signature",
        )

    def test_status_reports_grace_remaining(self):
        api_security.rotate_secret(grace_seconds=600)
        remaining = api_security.status()["signature"]["previous_valid_for"]
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 600)


class ThrottleTest(unittest.TestCase):
    def test_blocks_after_max_failures(self):
        throttle = api_security.LoginThrottle(max_failures=3, window_seconds=60, block_seconds=30)
        now = 1000.0
        for step in range(3):
            throttle.record_failure("1.2.3.4", now=now + step)
        self.assertEqual(throttle.blocked_for("1.2.3.4", now=now + 3), 29)

    def test_success_clears(self):
        throttle = api_security.LoginThrottle(max_failures=2)
        throttle.record_failure("1.2.3.4", now=1000)
        throttle.record_failure("1.2.3.4", now=1001)
        self.assertGreater(throttle.blocked_for("1.2.3.4", now=1002), 0)
        throttle.record_success("1.2.3.4")
        self.assertEqual(throttle.blocked_for("1.2.3.4", now=1003), 0)

    def test_block_expires(self):
        throttle = api_security.LoginThrottle(max_failures=1, block_seconds=10)
        throttle.record_failure("5.6.7.8", now=1000)
        self.assertGreater(throttle.blocked_for("5.6.7.8", now=1001), 0)
        self.assertEqual(throttle.blocked_for("5.6.7.8", now=1011), 0)

    def test_window_forgets_old_failures(self):
        throttle = api_security.LoginThrottle(max_failures=3, window_seconds=10)
        throttle.record_failure("9.9.9.9", now=1000)
        throttle.record_failure("9.9.9.9", now=1100)
        throttle.record_failure("9.9.9.9", now=1101)
        self.assertEqual(throttle.blocked_for("9.9.9.9", now=1102), 0)


if __name__ == "__main__":
    unittest.main()
