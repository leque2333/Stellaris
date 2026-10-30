# -*- coding: utf-8 -*-
"""档案画像：好感度浮动范围 + 自定义设定 + 事实来源。"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.storage.schema import apply_migrations  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class ArchiveProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="archive_profile_"))
        self.db = self.tmp / "memory.db"
        apply_migrations(self.db)
        self.store = SQLiteStore(self.db)
        self.store.ensure_archive("arc_master", role="admin", display_name="主人")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 好感度浮动范围 ----
    def test_default_range_is_full(self):
        archive = self.store.get_archive("arc_master")
        self.assertEqual(archive["affection_min"], 0)
        self.assertEqual(archive["affection_max"], 100)

    def test_affection_clamped_to_window(self):
        self.store.update_archive("arc_master", affection_min=60, affection_max=100)
        self.store.update_affection("arc_master", 0, "init", default=94)
        self.assertEqual(self.store.update_affection("arc_master", -100, "down"), 60)
        self.assertEqual(self.store.update_affection("arc_master", +100, "up"), 100)

    def test_affection_inside_window(self):
        self.store.update_archive("arc_master", affection_min=50, affection_max=80)
        self.store.update_affection("arc_master", 0, "init", default=60)
        self.assertEqual(self.store.update_affection("arc_master", 5, "up"), 65)
        self.assertEqual(self.store.update_affection("arc_master", -10, "down"), 55)

    def test_range_validation(self):
        with self.assertRaises(ValueError):
            self.store.update_archive("arc_master", affection_min=80, affection_max=20)

    def test_range_clamped_to_0_100(self):
        self.store.update_archive("arc_master", affection_min=-20, affection_max=200)
        archive = self.store.get_archive("arc_master")
        self.assertEqual(archive["affection_min"], 0)
        self.assertEqual(archive["affection_max"], 100)

    def test_history_records_range(self):
        self.store.update_archive("arc_master", affection_min=60, affection_max=100)
        self.store.update_affection("arc_master", 1, "test", default=94)
        profile = self.store.get_profile("arc_master")
        history = profile["affection_history"]
        self.assertTrue(history)
        self.assertEqual(history[-1]["range"], [60, 100])

    def test_tightening_window_clamps_current(self):
        self.store.update_affection("arc_master", 0, "init", default=94)
        self.store.update_archive("arc_master", affection_min=0, affection_max=70)
        # 窗口收窄后，当前值应被夹回
        self.store.update_affection("arc_master", 0, "收紧范围")
        self.assertEqual(self.store.get_affection("arc_master"), 70)

    # ---- 自定义设定 ----
    def test_add_and_list_settings(self):
        self.store.upsert_archive_setting("arc_master", "称呼", "主人", sort_order=1)
        self.store.upsert_archive_setting("arc_master", "雷点", "别叫主人", sort_order=2)
        settings = self.store.list_archive_settings("arc_master")
        self.assertEqual([s["label"] for s in settings], ["称呼", "雷点"])
        self.assertEqual(settings[0]["content"], "主人")

    def test_update_setting(self):
        created = self.store.upsert_archive_setting("arc_master", "称呼", "主人")
        self.store.upsert_archive_setting(
            "arc_master", "称呼", "主人2333", setting_id=created["setting_id"],
        )
        settings = self.store.list_archive_settings("arc_master")
        self.assertEqual(len(settings), 1)
        self.assertEqual(settings[0]["content"], "主人2333")

    def test_delete_setting(self):
        created = self.store.upsert_archive_setting("arc_master", "称呼", "主人")
        self.assertTrue(self.store.delete_archive_setting("arc_master", created["setting_id"]))
        self.assertEqual(self.store.list_archive_settings("arc_master"), [])

    def test_setting_requires_label(self):
        with self.assertRaises(ValueError):
            self.store.upsert_archive_setting("arc_master", "  ", "x")

    def test_settings_isolated_per_archive(self):
        self.store.ensure_archive("arc_guest", role="guest")
        self.store.upsert_archive_setting("arc_master", "称呼", "主人")
        self.store.upsert_archive_setting("arc_guest", "称呼", "路人")
        master = self.store.list_archive_settings("arc_master")
        guest = self.store.list_archive_settings("arc_guest")
        self.assertEqual(master[0]["content"], "主人")
        self.assertEqual(guest[0]["content"], "路人")

    def test_sort_order(self):
        self.store.upsert_archive_setting("arc_master", "B", "2", sort_order=2)
        self.store.upsert_archive_setting("arc_master", "A", "1", sort_order=1)
        labels = [s["label"] for s in self.store.list_archive_settings("arc_master")]
        self.assertEqual(labels, ["A", "B"])

    # ---- 事实来源 ----
    def test_fact_source_default_manual(self):
        self.store.add_fact("arc_master", "默认来源事实")
        facts = self.store.get_active_facts("arc_master")
        self.assertEqual(facts[0]["source"], "manual")

    def test_fact_source_ai_extract(self):
        self.store.add_fact("arc_master", "AI 总结的事实", source="ai_extract")
        facts = self.store.get_active_facts("arc_master")
        self.assertEqual(facts[0]["source"], "ai_extract")

    def test_supersede_keeps_source(self):
        fid = self.store.add_fact("arc_master", "旧事实", source="ai_extract")
        self.store.supersede_fact(fid, "新事实", source="ai_extract")
        facts = self.store.get_active_facts("arc_master")
        self.assertEqual(facts[0]["content"], "新事实")
        self.assertEqual(facts[0]["source"], "ai_extract")

    # ---- 迁移 ----
    def test_migration_columns_exist(self):
        with self.store._connect() as conn:
            arch_cols = {r[1] for r in conn.execute("PRAGMA table_info(archives)")}
            fact_cols = {r[1] for r in conn.execute("PRAGMA table_info(user_facts)")}
        self.assertIn("affection_min", arch_cols)
        self.assertIn("affection_max", arch_cols)
        self.assertIn("source", fact_cols)

    def test_migration_idempotent(self):
        apply_migrations(self.db)
        apply_migrations(self.db)
        with self.store._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE name = 'archive_settings'"
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
