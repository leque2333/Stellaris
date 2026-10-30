# -*- coding: utf-8 -*-
"""档案层：管理员档案必须存在，用户档案必须手动创建，画像绑定档案"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.identity import IdentityResolver  # noqa: E402
from agent_core.memory import MemoryHub  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_archive_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.hub = MemoryHub(store=self.store)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_admin_archive_is_mandatory(self):
        admin = self.hub.profile.archives.admin()
        self.assertEqual(admin["archive_id"], "arc_master")
        self.assertEqual(admin["role"], "admin")
        self.assertNotIn("persona_id", admin)
        self.assertNotIn("persona_sections", admin)
        identities = self.hub.profile.list_identities(admin["archive_id"])
        platforms = {i["platform"] for i in identities}
        self.assertIn("qq", platforms)
        # B站插件已从测试端移除，不再自动硬编码 B站身份
        self.assertNotIn("bilibili", platforms)

    def test_create_person_archive(self):
        arch = self.hub.profile.archives.create_person("我的 AI")
        self.assertEqual(arch["role"], "guest")
        self.assertNotIn("persona_sections", arch)
        self.assertEqual(self.hub.profile.list_identities(arch["archive_id"]), [])

        updated = self.hub.profile.archives.update(
            arch["archive_id"],
            display_name="新名字",
        )
        self.assertEqual(updated["display_name"], "新名字")
        self.assertNotIn("persona_sections", updated)

    def test_user_archive_requires_manual_creation(self):
        # 未建档：解析返回 None，画像不写入
        self.assertIsNone(self.hub.profile.archives.resolve("qq", "123456"))
        self.assertIsNone(self.hub.profile.get_archive("user_123456"))
        self.assertIsNone(self.hub.profile.get_affection("user_123456"), "未建档用户好感度应为未定")
        self.assertEqual(self.hub.profile.add_fact("user_123456", "喜欢猫"), "")

        # 手动创建后才可用
        arch = self.hub.profile.archives.create_user("qq", "123456", display_name="小明")
        self.assertEqual(arch["role"], "guest")
        again = self.hub.profile.archives.resolve("qq", "123456")
        self.assertEqual(again["archive_id"], arch["archive_id"])

    def test_identity_maps_across_platforms(self):
        arch = self.hub.profile.archives.create_user("qq", "234567", display_name="小红")
        self.hub.profile.archives.link(
            arch["archive_id"], "bilibili", "888888", display_name="小红B站"
        )
        by_bili = self.hub.profile.archives.resolve("bilibili", "888888")
        self.assertEqual(by_bili["archive_id"], arch["archive_id"])

    def test_profile_binds_to_archive(self):
        self.hub.profile.archives.create_user("qq", "345678", display_name="小刚")
        fact_id = self.hub.profile.add_fact("user_345678", "喜欢猫", "test")
        self.assertTrue(fact_id)
        facts = self.hub.profile.get_facts_for_prompt("user_345678")
        self.assertIn("喜欢猫", facts)
        # 主人事实不受影响
        master_facts = self.hub.profile.get_facts_for_prompt("master")
        self.assertNotIn("喜欢猫", master_facts)

    def test_actor_resolution(self):
        for actor in ("master", IdentityResolver.get_master_qq()):
            arch = self.hub.profile.archives.resolve_actor(actor)
            self.assertEqual(arch["role"], "admin")
        self.assertIsNone(self.hub.profile.archives.resolve_actor("system"))
        # 未建档用户解析为 None
        self.assertIsNone(self.hub.profile.archives.resolve_actor("user_456789"))
        # 建档后可解析
        self.hub.profile.archives.create_user("qq", "456789")
        arch = self.hub.profile.archives.resolve_actor("user_456789")
        self.assertEqual(arch["role"], "guest")

    def test_archive_is_person_not_ai_persona(self):
        arch = self.hub.profile.archives.create_user(
            "qq", "567890", display_name="自定义用户"
        )
        self.assertNotIn("persona_id", arch)
        self.assertNotIn("persona_sections", arch)
        self.assertEqual(arch["role"], "guest")


if __name__ == "__main__":
    unittest.main(verbosity=2)
