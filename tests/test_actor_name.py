# -*- coding: utf-8 -*-
"""网关身份：说话人必须是人物档案昵称，不能是 API 客户端名。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core.identity import IdentityResolver  # noqa: E402


class _Timeline:
    def get_user_affection(self, person_id):
        return 100

    def get_user_profile(self, person_id):
        return {"facts": ["喜欢科技新闻"]}


class ActorNameTest(unittest.TestCase):
    def setUp(self):
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.hub = _Timeline()

    def _result(self):
        return {
            "actor_id": "master",
            "actor_name": "AstrBot QQ 通道（唯一主管理员客户端）",
            "archive_id": "arc_master",
            "bound_archive": None,
            "is_guest": False,
            "perms": {},
        }

    def test_archive_display_name_wins(self):
        result = self._result()
        self.handler._build_member_identity(
            result, self.hub, "master", "2986371638", None, {"display_name": "乐阙"}
        )
        self.assertEqual(result["actor_name"], "乐阙")

    def test_master_falls_back_to_identity_name(self):
        result = self._result()
        self.handler._build_member_identity(
            result, self.hub, "master", "2986371638", None, None
        )
        self.assertEqual(result["actor_name"], IdentityResolver.get_master_name())

    def test_client_name_not_used_for_member(self):
        result = self._result()
        result["actor_id"] = "user_10001"
        self.handler._build_member_identity(
            result, self.hub, "user_10001", "2986371638", None, None
        )
        self.assertNotIn("AstrBot", result["actor_name"])


if __name__ == "__main__":
    unittest.main()
