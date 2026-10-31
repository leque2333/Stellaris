# -*- coding: utf-8 -*-
"""
存储层回归测试
运行：python -m unittest tests.test_storage -v
不修改生产库：所有用例都先复制到临时目录再迁移。
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.models import TimelineEvent  # noqa: E402
from agent_core.storage import get_store, reset_stores  # noqa: E402
from agent_core.storage.schema import (  # noqa: E402
    MIGRATIONS,  # noqa: E402
    apply_migrations,
    classify_source,
)
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402

LATEST_SCHEMA_VERSION = MIGRATIONS[-1][0]

FIXTURE = Path(r"D:\机器人_完整版\data\memory.db")


class StorageTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_store_"))
        self.db = self.tmp / "memory.db"
        if FIXTURE.exists():
            shutil.copy2(FIXTURE, self.db)
        reset_stores()

    def tearDown(self):
        reset_stores()
        shutil.rmtree(self.tmp, ignore_errors=True)


class FreshDatabaseTest(StorageTestBase):
    def test_create_from_scratch(self):
        self.db.unlink(missing_ok=True)
        store = SQLiteStore(self.db)
        self.assertEqual(store.schema_version(), LATEST_SCHEMA_VERSION)
        self.assertEqual(store.timeline_count(), 0)
        self.assertEqual(store.activity_count(), 0)

    def test_migration_idempotent(self):
        store = SQLiteStore(self.db)
        first = store.schema_version()
        second = apply_migrations(self.db)
        self.assertEqual(second["applied"], [])
        self.assertEqual(store.schema_version(), first)


class ExistingDatabaseTest(StorageTestBase):
    def test_counts_and_schema_preserved(self):
        with sqlite3.connect(self.db) as conn:
            before = conn.execute("SELECT COUNT(*) FROM agent_timeline").fetchone()[0]
            before_profiles = conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]

        store = SQLiteStore(self.db)
        self.assertEqual(store.timeline_count(), before)
        self.assertEqual(store.schema_version(), LATEST_SCHEMA_VERSION)

        with sqlite3.connect(self.db) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_timeline)")}
            for name in ("plugin_id", "tag_primary", "tag_secondary", "segment_id", "started_at", "ended_at"):
                self.assertIn(name, cols)
            view = conn.execute("SELECT type FROM sqlite_master WHERE name='token_usage_ledger'").fetchone()
            self.assertEqual(view[0], "view")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0], before_profiles)

    def test_tags_backfilled(self):
        SQLiteStore(self.db)
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                "SELECT source_module, tag_primary, tag_secondary FROM agent_timeline WHERE tag_primary IS NOT NULL"
            ).fetchall()
        self.assertTrue(rows, "backfill should produce tags")
        for source, primary, secondary in rows:
            self.assertTrue(primary)
            self.assertTrue(secondary)

    def test_ledger_migrated(self):
        """旧的 token_usage_ledger 表 → 迁移进 token_usage 主表 + 只读视图。

        不依赖生产库：自己造一个旧 schema 的库，测试迁移逻辑本身。
        """
        legacy = self.tmp / "legacy.db"
        legacy.unlink(missing_ok=True)
        conn = sqlite3.connect(legacy)
        conn.execute(
            """
            CREATE TABLE token_usage_ledger (
                id TEXT PRIMARY KEY,
                timestamp REAL,
                model_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                cache_tokens INTEGER,
                source TEXT,
                cost_usd REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO token_usage_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("led_1", 1700000000.0, "充值", 0, 0, 0, "账户充值", 10.0),
        )
        conn.commit()
        conn.close()

        apply_migrations(legacy)

        conn = sqlite3.connect(legacy)
        try:
            rows = conn.execute(
                "SELECT model, source, cost_usd FROM token_usage "
                "WHERE source IN ('人工充值','账户充值','系统注资')"
            ).fetchall()
            self.assertEqual(len(rows), 1, "recharge row should be migrated into token_usage")
            self.assertAlmostEqual(float(rows[0][2]), 10.0, places=2)

            kind = conn.execute(
                "SELECT type FROM sqlite_master WHERE name='token_usage_ledger'"
            ).fetchone()
            self.assertEqual(kind[0], "view", "旧表应被替换成只读视图")

            # 视图能读回迁移后的数据
            view_rows = conn.execute(
                "SELECT source, cost_usd FROM token_usage_ledger WHERE source = '账户充值'"
            ).fetchall()
            self.assertEqual(len(view_rows), 1)
            self.assertAlmostEqual(float(view_rows[0][1]), 10.0, places=2)
        finally:
            conn.close()

    def test_ledger_migration_idempotent(self):
        """重复迁移不会重复插入充值行。"""
        legacy = self.tmp / "legacy2.db"
        legacy.unlink(missing_ok=True)
        conn = sqlite3.connect(legacy)
        conn.execute(
            """
            CREATE TABLE token_usage_ledger (
                id TEXT PRIMARY KEY, timestamp REAL, model_id TEXT,
                input_tokens INTEGER, output_tokens INTEGER, cache_tokens INTEGER,
                source TEXT, cost_usd REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO token_usage_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("led_1", 1700000000.0, "充值", 0, 0, 0, "账户充值", 10.0),
        )
        conn.commit()
        conn.close()

        apply_migrations(legacy)
        apply_migrations(legacy)

        conn = sqlite3.connect(legacy)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM token_usage WHERE source = '账户充值'"
            ).fetchone()[0]
            self.assertEqual(count, 1)
        finally:
            conn.close()


class TimelineTest(StorageTestBase):
    def test_append_with_tags_and_query(self):
        store = SQLiteStore(self.db)
        evt = store.append_event(TimelineEvent(
            event_id="",
            timestamp=time.time(),
            source_module="qq",
            actor_id="master",
            actor_name="主人",
            narrative="在群里聊了会儿",
            plugin_id="qq",
            tag_primary="QQ",
            tag_secondary="摸鱼群",
        ))
        self.assertTrue(evt.event_id)
        hit = store.list_events(limit=5, tag_primary="QQ", tag_secondary="摸鱼群")
        self.assertTrue(any(e.event_id == evt.event_id for e in hit))

    def test_session_folding(self):
        store = SQLiteStore(self.db)
        before = store.timeline_count()
        for i in range(3):
            store.append_event(
                TimelineEvent(
                    event_id="", timestamp=time.time() + i, source_module="bilibili",
                    actor_id="self", actor_name="助手", narrative=f"看了第{i}个视频",
                ),
                session_window_seconds=600,
            )
        self.assertEqual(store.timeline_count(), before + 1)

    def test_recall(self):
        from agent_core.memory import MemoryHub
        hub = MemoryHub(store=SQLiteStore(self.db))
        hub.append_event(source_module="qq", actor_id="master", narrative="主人今天夸了助手的代码结构",
                         tag_primary="QQ", tag_secondary="摸鱼群")
        results = hub.recall_timeline(query="代码结构", actor_id="master", limit=3)
        self.assertTrue(results)

    def test_timeline_and_library_are_separate(self):
        from agent_core.memory import MemoryHub
        hub = MemoryHub(store=SQLiteStore(self.db))
        self.assertIs(hub.timeline, hub.episodic)
        self.assertIsNotNone(hub.library)
        self.assertIsNotNone(hub.profile)
        self.assertFalse(hasattr(hub.library, "add_fact"))
        fact_id = hub.profile.add_fact("1000000001", "测试：事实归画像，不归记忆库", "test")
        self.assertTrue(fact_id)
        self.assertIn("测试：事实归画像，不归记忆库", hub.profile.get_facts_for_prompt("1000000001"))

    def test_import_events_idempotent(self):
        store = SQLiteStore(self.db)
        now = time.time()
        row = {
            "event_id": "evt_import_test", "timestamp": now, "time_str": "", "source_module": "qq",
            "actor_id": "master", "actor_name": "主人", "narrative": "导入测试",
            "emotional_impact": 0, "importance": 1.0, "prev_event_id": None,
            "causal_relation": None, "metadata": "{}",
        }
        self.assertEqual(store.import_events([row]), 1)
        self.assertEqual(store.import_events([row]), 0)


class FactsTest(StorageTestBase):
    def test_fact_supersede(self):
        store = SQLiteStore(self.db)
        uid = "1000000001"
        fact_id = store.add_fact(uid, "喜欢 Windows", category="environment")
        new_id = store.supersede_fact(fact_id, "改用 Mac 了", category="environment")
        active = [f["content"] for f in store.get_active_facts(uid)]
        self.assertIn("改用 Mac 了", active)
        self.assertNotIn("喜欢 Windows", active)
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT supersedes_id, status FROM user_facts WHERE fact_id=?", (new_id,)).fetchone()
        self.assertEqual(row[0], fact_id)

    def test_affection_bounds(self):
        store = SQLiteStore(self.db)
        uid = "affection_test_user"
        store.update_affection(uid, 500, "test")
        self.assertEqual(store.get_affection(uid), 100)
        store.update_affection(uid, -500, "test")
        self.assertEqual(store.get_affection(uid), 0)


class ActivitySegmentTest(StorageTestBase):
    def test_activity_idempotent(self):
        store = SQLiteStore(self.db)
        item = {
            "activity_id": "act_test_1", "plugin_id": "bilibili", "occurred_at": time.time(),
            "kind": "action", "verb": "comment", "object": "视频A", "context_name": "某UP",
        }
        first = store.insert_activities([item])
        second = store.insert_activities([item])
        self.assertEqual(first[0]["status"], "accepted")
        self.assertEqual(second[0]["status"], "duplicate")
        self.assertEqual(store.activity_count(), 1)

    def test_segment_lifecycle(self):
        store = SQLiteStore(self.db)
        seg = store.create_segment("qq", "group", "123", "摸鱼群", time.time())
        self.assertEqual(seg["status"], "open")
        store.touch_segment(seg["segment_id"], time.time(), add_fragments=2, participants=["主人"])
        fresh = store.get_open_segment("qq", "group", "123")
        self.assertEqual(fresh["fragment_count"], 2)
        store.close_segment(seg["segment_id"], time.time())
        self.assertIsNone(store.get_open_segment("qq", "group", "123"))


class ClassifyTest(unittest.TestCase):
    def test_mapping(self):
        self.assertEqual(classify_source("astrbot_group", {"group_name": "摸鱼群"}), ("qq", "QQ", "摸鱼群"))
        self.assertEqual(classify_source("bilibili_patrol", {}), ("bilibili", "B站", "巡检"))
        self.assertEqual(classify_source("pc_bridge", {}), ("host_control", "宿主机", "系统操作"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

