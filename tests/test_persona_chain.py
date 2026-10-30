# -*- coding: utf-8 -*-
"""人格 ↔ 档案 ↔ 好感度 的上下文植入链路。"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_core.persona import PersonaManager
from agent_core.profile import ProfileManager
from agent_core.storage.sqlite_store import SQLiteStore


class PersonaChainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stellaris_chain_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.profile = ProfileManager(self.store)
        self.archive = self.profile.archives.create_user(
            "qq", "123456", display_name="阿明"
        )
        self.archive_id = self.archive["archive_id"]

        self.persona_path = self.tmp / "persona.md"
        self.persona_path.write_text(
            "# 人设：星野\n\n## 人格设定\n你是星野，说话简洁。\n",
            encoding="utf-8",
        )
        self.rules_path = self.tmp / "persona_rules.json"
        self.rules_path.write_text(json.dumps({
            "affection_tiers": [
                {
                    "id": "tier_intimate",
                    "min_score": 80,
                    "max_score": 100,
                    "stage_name": "极度信任",
                    "attitude_hint": "测试高阶态度",
                }
            ],
            "speech_regex_rules": [],
        }, ensure_ascii=False), encoding="utf-8")
        self.manager = PersonaManager(
            persona_path=self.persona_path,
            rules_path=self.rules_path,
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_affection_and_fact_reach_persona_context(self):
        self.profile.set_affection(self.archive_id, 85, "测试")
        self.profile.add_fact(self.archive_id, "喜欢喝乌龙茶")
        context = self.manager.build_system_context(
            actor_id=self.archive_id,
            actor_name="阿明",
            role_type="guest",
            affection=self.profile.get_affection(self.archive_id),
            master_facts=self.profile.get_facts_for_prompt(self.archive_id),
        )
        self.assertIn("星野", context)
        self.assertIn("85/100", context)
        self.assertIn("测试高阶态度", context)
        self.assertIn("喜欢喝乌龙茶", context)
        self.assertIn("阿明", context)

    def test_feedback_updates_next_persona_context(self):
        self.profile.set_affection(self.archive_id, 85, "测试")
        first = self.manager.build_system_context(
            actor_id=self.archive_id,
            actor_name="阿明",
            role_type="guest",
            affection=self.profile.get_affection(self.archive_id),
        )
        self.assertIn("85/100", first)

        new_value = self.profile.update_affection(self.archive_id, 5, "这轮聊得很好")
        self.profile.add_fact(self.archive_id, "今天心情不好")
        second = self.manager.build_system_context(
            actor_id=self.archive_id,
            actor_name="阿明",
            role_type="guest",
            affection=self.profile.get_affection(self.archive_id),
            master_facts=self.profile.get_facts_for_prompt(self.archive_id),
        )
        self.assertEqual(new_value, 90)
        self.assertIn("90/100", second)
        self.assertIn("今天心情不好", second)

    def test_archive_resolution_returns_display_name(self):
        resolved = self.profile.archives.resolve("qq", "123456")
        self.assertEqual(resolved["archive_id"], self.archive_id)
        self.assertEqual(resolved["display_name"], "阿明")
        actor = self.profile.archives.resolve_actor(self.archive_id)
        self.assertEqual(actor["archive_id"], self.archive_id)

    def test_affection_none_means_first_contact(self):
        context = self.manager.build_system_context(
            actor_id=self.archive_id,
            actor_name="阿明",
            role_type="guest",
            affection=None,
        )
        self.assertIn("第一次交流", context)
        self.assertIn("不要预设亲近", context)

    def test_affection_tier_boundary(self):
        self.profile.set_affection(self.archive_id, 80, "测试")
        context = self.manager.build_system_context(
            actor_id=self.archive_id,
            actor_name="阿明",
            role_type="guest",
            affection=80,
        )
        self.assertIn("极度信任", context)
        self.assertIn("测试高阶态度", context)


if __name__ == "__main__":
    unittest.main()
