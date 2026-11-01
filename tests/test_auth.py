# -*- coding: utf-8 -*-
"""控制台鉴权：首启随机口令、会话、改密、老哈希升级、弱口令轮换"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.auth import AuthManager  # noqa: E402


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_auth_"))
        # 测试包默认把鉴权配置指到临时文件；这里要每个用例用自己的目录
        self._env = patch.dict(os.environ)
        self._env.start()
        os.environ.pop("HUB_AUTH_CONFIG_PATH", None)
        self.auth = AuthManager(str(self.tmp))

    def tearDown(self):
        self._env.stop()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _config_path(self, manager=None):
        return Path((manager or self.auth).config_path)

    def test_first_run_generates_random_password(self):
        """首启不再有内置口令：随机生成、强制改密、能登录。"""
        self.assertTrue(self.auth.initial_password)
        self.assertGreaterEqual(len(self.auth.initial_password), 16)
        self.assertNotIn(self.auth.initial_password.lower(), ("admin888", "123456"))
        self.assertTrue(self.auth.verify_credentials("admin", self.auth.initial_password))
        self.assertFalse(self.auth.verify_credentials("admin", "wrong"))
        self.assertFalse(self.auth.verify_credentials("admin", "admin888"))
        self.assertTrue(self.auth.must_change_password())

    def test_session_lifecycle(self):
        token = self.auth.create_session("admin")
        self.assertIsNotNone(self.auth.validate_session(token))
        self.auth.revoke_session(token)
        self.assertIsNone(self.auth.validate_session(token))

    def test_change_password(self):
        old = self.auth.initial_password
        ok, _ = self.auth.change_password(old, "new-pass-123456")
        self.assertTrue(ok)
        self.assertFalse(self.auth.verify_credentials("admin", old))
        self.assertTrue(self.auth.verify_credentials("admin", "new-pass-123456"))
        self.assertFalse(self.auth.must_change_password())

    def test_change_password_rejects_short(self):
        ok, message = self.auth.change_password(self.auth.initial_password, "short")
        self.assertFalse(ok)
        self.assertIn("12", message)

    def test_change_password_revokes_sessions(self):
        token = self.auth.create_session("admin")
        self.auth.change_password(self.auth.initial_password, "new-pass-123456")
        self.assertIsNone(self.auth.validate_session(token))

    def test_legacy_sha256_hash_upgrades_on_login(self):
        """老配置的 sha256(salt:password) 登录后自动升级成 PBKDF2。"""
        cfg = json.loads(self._config_path().read_text(encoding="utf-8"))
        salt = "0123456789abcdef0123456789abcdef"
        legacy = AuthManager._legacy_hash("old-pass-123456", salt)
        cfg.update({"salt": salt, "password_hash": legacy})
        self._config_path().write_text(json.dumps(cfg), encoding="utf-8")

        self.assertTrue(self.auth.verify_credentials("admin", "old-pass-123456"))
        upgraded = json.loads(self._config_path().read_text(encoding="utf-8"))
        self.assertTrue(upgraded["password_hash"].startswith("pbkdf2_sha256$"))
        self.assertTrue(self.auth.verify_credentials("admin", "old-pass-123456"))

    def test_weak_default_password_is_rotated(self):
        """存量配置仍是历史弱口令时，启动检查即轮换并清空会话。"""
        path = self._config_path()
        cfg = json.loads(path.read_text(encoding="utf-8"))
        salt = cfg["salt"]
        cfg["password_hash"] = AuthManager._legacy_hash("admin888", salt)
        cfg["sessions"] = {"deadbeef": {"username": "admin", "created_at": 0, "expires_at": 9999999999}}
        path.write_text(json.dumps(cfg), encoding="utf-8")

        rotated = AuthManager(str(self.tmp))
        rotated.enforce_password_policy()
        self.assertFalse(rotated.verify_credentials("admin", "admin888"))
        self.assertTrue(rotated.verify_credentials("admin", rotated.initial_password))
        self.assertIsNone(rotated.validate_session("deadbeef"))

    def test_construct_does_not_touch_existing_config(self):
        """仅仅构造 AuthManager（比如跑测试 import server）不能改线上配置。"""
        path = self._config_path()
        cfg = json.loads(path.read_text(encoding="utf-8"))
        cfg["password_hash"] = AuthManager._legacy_hash("admin888", cfg["salt"])
        path.write_text(json.dumps(cfg), encoding="utf-8")
        before = path.read_bytes()

        AuthManager(str(self.tmp))
        self.assertEqual(path.read_bytes(), before)

    def test_initial_password_file_is_written(self):
        self.assertTrue(os.path.exists(self.auth.password_file))
        self.assertIn(self.auth.initial_password, Path(self.auth.password_file).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
