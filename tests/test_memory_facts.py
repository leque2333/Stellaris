# -*- coding: utf-8 -*-
"""事实层：确定事实、冲突失效、跨平台共享。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.memory.facts import FactStore, extract_facts, _parse_json_array  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class FactStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_facts_"))
        self.store = SQLiteStore(self.tmp / "memory.db")
        self.facts = FactStore(self.store)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_chinese_facts(self):
        items = extract_facts("记住一个测试暗号：琥珀-5521")
        self.assertEqual(items[0]["predicate"], "测试暗号")
        self.assertEqual(items[0]["value"], "琥珀-5521")
        self.assertTrue(extract_facts("我的测试暗号是紫罗兰-8842"))
        self.assertTrue(extract_facts("我叫主人"))

    def test_new_value_supersedes_old(self):
        first = self.facts.add("1000000001", "测试暗号", "紫罗兰-8842")
        second = self.facts.add("1000000001", "测试暗号", "琥珀-5521")
        self.assertTrue(first["fact_id"])
        self.assertTrue(second["fact_id"])
        active = self.facts.search("1000000001")
        self.assertEqual([item["value"] for item in active], ["琥珀-5521"])
        old = self.facts.get(first["fact_id"])
        self.assertEqual(old["status"], "superseded")
        self.assertIsNotNone(old["invalid_at"])

    def test_facts_are_scoped_by_person(self):
        self.facts.add("1000000001", "测试暗号", "琥珀-5521")
        self.assertEqual(self.facts.search("999"), [])
        self.assertEqual(len(self.facts.prompt_lines("1000000001")), 1)

    def test_facts_at_time(self):
        import time
        base = time.time()
        old = self.facts.add(
            "u1", "测试暗号", "紫罗兰", valid_at=base - 100,
        )
        new = self.facts.add(
            "u1", "测试暗号", "琥珀", valid_at=base,
        )
        at_old = self.facts.facts_at("u1", base - 50)
        self.assertEqual([item["value"] for item in at_old], ["紫罗兰"])
        at_now = self.facts.facts_at("u1", base + 50)
        self.assertEqual([item["value"] for item in at_now], ["琥珀"])
        self.assertTrue(old and new)

    def test_fts_exact_substring(self):
        from agent_core.models import TimelineEvent

        self.store.append_event(TimelineEvent(
            event_id="",
            timestamp=1_700_000_000.0,
            source_module="test",
            actor_id="u1",
            actor_name="主人",
            narrative="主人说：青柠-9933 是测试暗号",
            person_id="u1",
        ))
        rows = self.facts.search_episodes("青柠-9933", person_id="u1")
        self.assertTrue(rows)
        self.assertIn("青柠-9933", rows[0]["narrative"])

    def test_pending_conflict_requires_confirmation(self):
        old = self.facts.add("u1", "测试暗号", "紫罗兰")
        new = self.facts.add(
            "u1", "测试暗号", "琥珀",
            confidence=0.9, require_confirmation=True,
        )
        self.assertEqual(new["status"], "pending")
        self.assertEqual([item["value"] for item in self.facts.search("u1")], ["紫罗兰"])
        confirmed = self.facts.confirm(new["fact_id"])
        self.assertEqual(confirmed["status"], "active")
        self.assertEqual([item["value"] for item in self.facts.search("u1")], ["琥珀"])
        self.assertEqual(self.facts.get(old["fact_id"])["status"], "superseded")

    def test_low_confidence_fact_is_pending(self):
        fact = self.facts.add("u1", "生日", "10月29日", confidence=0.4)
        self.assertEqual(fact["status"], "pending")
        self.assertEqual(self.facts.search("u1"), [])

    def test_graph_relations_follow_active_facts(self):
        old = self.facts.add("u1", "测试暗号", "紫罗兰")
        new = self.facts.add("u1", "测试暗号", "琥珀")
        graph = self.facts.graph("u1")
        values = [edge["object_value"] for edge in graph["edges"]]
        self.assertIn("琥珀", values)
        self.assertNotIn("紫罗兰", values)
        self.facts.rollback(old["fact_id"])
        graph = self.facts.graph("u1")
        values = [edge["object_value"] for edge in graph["edges"]]
        self.assertIn("紫罗兰", values)
        self.assertNotIn("琥珀", values)
        self.assertTrue(new)

    def test_rollback_restores_old_value(self):
        first = self.facts.add("u1", "测试暗号", "紫罗兰")
        second = self.facts.add("u1", "测试暗号", "琥珀")
        restored = self.facts.rollback(first["fact_id"])
        self.assertEqual(restored["status"], "active")
        active = self.facts.search("u1")
        self.assertEqual([item["value"] for item in active], ["紫罗兰"])
        self.assertEqual(self.facts.get(second["fact_id"])["status"], "superseded")

    def test_persona_includes_relations(self):
        from agent_core.persona import PersonaManager
        text = PersonaManager().build_system_context(
            actor_id="master",
            actor_name="主人",
            role_type="admin",
            affection=90,
            base_prompt="人设",
            relations=["主人 --测试暗号--> 琥珀"],
        )
        self.assertIn("关系图", text)
        self.assertIn("主人 --测试暗号--> 琥珀", text)

    def test_ai_json_parser(self):
        items = _parse_json_array('```json\n[{"predicate":"生日","value":"10月29日"}]\n```')
        self.assertEqual(items, [{"predicate": "生日", "value": "10月29日", "confidence": 1.0}])
        self.assertEqual(_parse_json_array("not json"), [])
        self.assertEqual(_parse_json_array('[{"predicate":"","value":"x"}]'), [])

    def test_extract_and_store_from_turn(self):
        self.facts.extract_and_store(
            "1000000001",
            "记住一个测试暗号：琥珀-5521",
            "记住了",
            "evt_1",
        )
        lines = self.facts.prompt_lines("1000000001", "暗号")
        self.assertEqual(lines, ["[当前事实] 测试暗号：琥珀-5521"])


if __name__ == "__main__":
    unittest.main()
