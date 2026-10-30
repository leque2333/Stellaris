# -*- coding: utf-8 -*-
"""身份映射落到发送者 UID：只认宿主传来的底层用户 UID"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.identity_binding import infer_platform, resolve_archive_for_sender  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class IdentityBindingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_identity_bind_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.store.ensure_archive("master", role="admin", display_name="主人")
        self.store.upsert_context(
            "qq", "1000000001",
            external_type="friend", display_name="主人", source="astrbot",
        )
        self.store.bind_context(
            "qq", "1000000001", "master",
            confidence=1.0, verified_by="console",
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sender_uid_resolves_binding(self):
        self.assertEqual(
            resolve_archive_for_sender(self.store, "qq", "1000000001"),
            "master",
        )

    def test_explicit_external_id_resolves_when_no_sender(self):
        self.store.ensure_archive("friend", role="user", display_name="朋友")
        self.store.bind_context("telegram", "tg:10001", "friend")
        self.assertEqual(
            resolve_archive_for_sender(self.store, "telegram", "", "tg:10001"),
            "friend",
        )

    def test_unknown_sender_does_not_resolve(self):
        self.assertIsNone(resolve_archive_for_sender(self.store, "qq", "1000000003"))
        self.assertIsNone(
            resolve_archive_for_sender(
                self.store, "qq", "1000000003", "qq:1000000003"
            )
        )

    def test_platform_less_sender_matches_across_platforms(self):
        self.assertEqual(
            resolve_archive_for_sender(self.store, "", "1000000001"),
            "master",
        )

    def test_infer_platform(self):
        self.assertEqual(infer_platform("qq_group:123", ""), "qq")
        self.assertEqual(infer_platform("", "qq:123"), "qq")
        self.assertEqual(infer_platform("telegram:123", ""), "telegram")
        self.assertEqual(infer_platform("", ""), "")


if __name__ == "__main__":
    unittest.main()
