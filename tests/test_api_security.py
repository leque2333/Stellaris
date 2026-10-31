# -*- coding: utf-8 -*-
"""API 安全：本机访问限制 + 请求签名。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from agent_core import api_security  # noqa: E402


class _TempConfig:
    """把配置指到临时文件，避免碰真实 data/api_security.json。"""

    def __init__(self, payload=None):
        self.dir = tempfile.mkdtemp(prefix="api_sec_")
        self.path = Path(self.dir) / "api_security.json"
        self.path.write_text(json.dumps(payload or {}), encoding="utf-8")
        self._old = os.environ.get("HUB_API_SECURITY_PATH")
        os.environ["HUB_API_SECURITY_PATH"] = str(self.path)

    def write(self, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def close(self):
        if self._old is None:
            os.environ.pop("HUB_API_SECURITY_PATH", None)
        else:
            os.environ["HUB_API_SECURITY_PATH"] = self._old
        import shutil

        shutil.rmtree(self.dir, ignore_errors=True)


class SignatureTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _TempConfig({
            "signature": {"enabled": True, "window_seconds": 300, "secret": "s3cret"},
        })

    def tearDown(self):
        self.cfg.close()

    def _check(self, **kwargs):
        params = {
            "method": "POST",
            "path": "/v1/chat/completions",
            "timestamp": "1000",
            "signature": "",
            "raw_body": b'{"a":1}',
            "now": 1000,
        }
        params.update(kwargs)
        return api_security.signature_error(**params)

    def test_valid_signature_passes(self):
        ts = "1000"
        digest = api_security.sign("POST", "/v1/chat/completions", ts, b'{"a":1}', "s3cret")
        self.assertIsNone(self._check(signature=digest))

    def test_wrong_body_rejected(self):
        digest = api_security.sign("POST", "/v1/chat/completions", "1000", b'{"a":2}', "s3cret")
        self.assertEqual(self._check(signature=digest), "bad_signature")

    def test_stale_timestamp_rejected(self):
        digest = api_security.sign("POST", "/v1/chat/completions", "1000", b'{"a":1}', "s3cret")
        self.assertEqual(self._check(signature=digest, now=1000 + 400), "stale_timestamp")

    def test_missing_headers_rejected(self):
        self.assertEqual(self._check(), "missing_signature")

    def test_bad_timestamp_rejected(self):
        self.assertEqual(self._check(timestamp="abc", signature="x"), "bad_timestamp")

    def test_disabled_signature_passes(self):
        self.cfg.write({"signature": {"enabled": False, "secret": "s3cret"}})
        self.assertIsNone(self._check())

    def test_enabled_without_secret_fails_open(self):
        self.cfg.write({"signature": {"enabled": True, "secret": ""}})
        self.assertIsNone(self._check())


class LocalOnlyTest(unittest.TestCase):
    def setUp(self):
        self.cfg = _TempConfig({"local_only": True})

    def tearDown(self):
        self.cfg.close()

    def test_loopback_allowed(self):
        self.assertTrue(api_security.ip_allowed("127.0.0.1"))

    def test_public_ip_blocked(self):
        self.assertFalse(api_security.ip_allowed("8.8.8.8"))

    def test_forwarded_for_used_only_from_trusted_proxy(self):
        self.assertEqual(api_security.client_ip("127.0.0.1", "8.8.8.8, 10.0.0.1"), "8.8.8.8")
        self.assertEqual(api_security.client_ip("192.168.1.9", "8.8.8.8"), "192.168.1.9")

    def test_custom_cidr(self):
        self.cfg.write({"local_only": True, "allow_cidrs": ["192.168.3.0/24"]})
        self.assertTrue(api_security.ip_allowed("192.168.3.4"))
        self.assertFalse(api_security.ip_allowed("127.0.0.1"))

    def test_local_only_off_allows_anyone(self):
        self.cfg.write({"local_only": False})
        self.assertTrue(api_security.ip_allowed("8.8.8.8"))


class HandlerGuardTest(unittest.TestCase):
    """直接调 ConsoleHandler 的两个守卫，验证接线没漏。"""

    def setUp(self):
        self.cfg = _TempConfig({"local_only": True})
        from server import ConsoleHandler  # noqa: PLC0415

        self.handler = ConsoleHandler.__new__(ConsoleHandler)
        self.responses = []
        self.handler._send_json = lambda data, status=200, extra_headers=None: self.responses.append(
            (status, data)
        )

    def tearDown(self):
        self.cfg.close()

    def _prepare(self, peer="127.0.0.1", forwarded="", path="/v1/models"):
        self.handler.client_address = (peer, 1234)
        self.handler.headers = {"X-Forwarded-For": forwarded}
        self.handler.path = path

    def test_public_forwarded_ip_blocked(self):
        self._prepare(forwarded="8.8.8.8")
        self.assertTrue(self.handler._access_blocked())
        self.assertEqual(self.responses[0][0], 403)
        self.assertEqual(self.responses[0][1]["error"], "local_only")

    def test_local_request_passes(self):
        self._prepare(forwarded="")
        self.assertFalse(self.handler._access_blocked())
        self.assertEqual(self.responses, [])

    def test_signature_required_for_gateway_paths(self):
        self.cfg.write({
            "signature": {"enabled": True, "secret": "s3cret", "window_seconds": 300},
        })
        self.assertFalse(self.handler._signature_blocked("POST", "/api/tokens", b""))
        self._prepare(path="/v1/chat/completions")
        self.assertTrue(self.handler._signature_blocked("POST", "/v1/chat/completions", b"{}"))
        self.assertEqual(self.responses[0][0], 401)


if __name__ == "__main__":
    unittest.main()
