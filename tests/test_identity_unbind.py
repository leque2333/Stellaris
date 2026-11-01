# -*- coding: utf-8 -*-
"""身份解绑：白名单与请求期绑定表要一起清，不能只清一半。"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

import server  # noqa: E402

from agent_core.storage import SQLiteStore  # noqa: E402


class UnlinkIdentityStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="unbind_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        self.store.ensure_archive("arc_guest", role="guest", display_name="访客")
        self.store.link_identity("arc_guest", "qq", "10001", confidence=0.95)
        self.store.bind_context("qq", "10001", "arc_guest", confidence=0.95)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_unlink_identity_removes_whitelist_row(self):
        self.assertIsNotNone(self.store.find_archive_by_identity("qq", "10001"))
        self.assertTrue(self.store.unlink_identity("qq", "10001"))
        self.assertIsNone(self.store.find_archive_by_identity("qq", "10001"))
        self.assertFalse(self.store.unlink_identity("qq", "10001"))

    def test_unbind_context_removes_binding_row(self):
        self.assertEqual(self.store.resolve_binding("qq", "10001"), "arc_guest")
        self.assertTrue(self.store.unbind_context("qq", "10001"))
        self.assertIsNone(self.store.resolve_binding("qq", "10001"))


class IdentityUnbindHandlerTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="unbind_api_"))
        self.store = SQLiteStore(self.dir / "memory.db")
        self.store.ensure_archive("arc_guest", role="guest", display_name="访客")
        self.store.link_identity("arc_guest", "qq", "10001", confidence=0.95)
        self.store.bind_context("qq", "10001", "arc_guest", confidence=0.95)
        self.sent = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _handler(self, authenticated=True):
        handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        handler._is_authenticated = lambda: authenticated
        handler._send_json = lambda data, status=200, extra_headers=None: self.sent.append(
            (status, data)
        )
        return handler

    def test_unbind_requires_login(self):
        self._handler(authenticated=False)._post_identity_unbind({"items": [{"platform": "qq", "uid": "10001"}]})
        self.assertEqual(self.sent[-1][0], 401)
        self.assertIsNotNone(self.store.find_archive_by_identity("qq", "10001"))

    def test_unbind_clears_both_tables(self):
        with mock.patch("agent_core.storage.get_store", return_value=self.store):
            self._handler()._post_identity_unbind({"items": [{"platform": "qq", "uid": "10001"}]})
        status, payload = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unbound"], 1)
        self.assertIsNone(self.store.find_archive_by_identity("qq", "10001"))
        self.assertIsNone(self.store.resolve_binding("qq", "10001"))

    def test_unbind_validates_items(self):
        self._handler()._post_identity_unbind({"items": [{"platform": "qq"}]})
        status, payload = self.sent[-1]
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["failed"], 1)


if __name__ == "__main__":
    unittest.main()
