# -*- coding: utf-8 -*-
"""控制台鉴权：默认配置、会话、改密"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.auth import AuthManager  # noqa: E402


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_auth_"))
        self.auth = AuthManager(str(self.tmp))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_login(self):
        self.assertTrue(self.auth.verify_credentials("admin", self.auth.DEFAULT_PASSWORD))
        self.assertFalse(self.auth.verify_credentials("admin", "wrong"))

    def test_session_lifecycle(self):
        token = self.auth.create_session("admin")
        self.assertIsNotNone(self.auth.validate_session(token))
        self.auth.revoke_session(token)
        self.assertIsNone(self.auth.validate_session(token))

    def test_change_password(self):
        ok, _ = self.auth.change_password(self.auth.DEFAULT_PASSWORD, "new-pass-123")
        self.assertTrue(ok)
        self.assertFalse(self.auth.verify_credentials("admin", self.auth.DEFAULT_PASSWORD))
        self.assertTrue(self.auth.verify_credentials("admin", "new-pass-123"))


if __name__ == "__main__":
    unittest.main()
