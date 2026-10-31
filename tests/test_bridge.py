# -*- coding: utf-8 -*-
"""通用桥接层：BridgeSpec / SQLite 驱动 / 去重入库"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.bridge import (  # noqa: E402
    BridgeIngestor,
    BridgeSpec,
    HubEvent,
    JsonEventDriver,
    LogEventDriver,
    SqliteEventDriver,
    default_engine,
)
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_bridge_"))
        self.db = self.tmp / "messages.db"
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE messages "
            "(id INTEGER, created_at TEXT, group_id TEXT, sender_id TEXT, sender_name TEXT, content TEXT)"
        )
        wrapped = (
            '<message_batch source="qq">'
            + json.dumps({"message": [{"type": "plain", "text": "你好"}]}, ensure_ascii=False)
            + "</message_batch>"
        )
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)", [
            (1, "2026-01-01T10:00:00", "g1", "u1", "主人", wrapped),
            (2, "2026-01-01T10:01:00", "g1", "u2", "朋友", "普通消息"),
        ])
        conn.commit()
        conn.close()
        self.store = SQLiteStore(self.tmp / "memory.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spec(self):
        return BridgeSpec(
            source_id="test_source", kind="sqlite", scope="plugin",
            locator=str(self.db),
            options={
                "table": "messages", "cursor": "id", "id": "id",
                "time": "created_at", "channel": "group_id",
                "actor": "sender_id", "actor_name": "sender_name",
                "content": "content", "content_format": "astrbot_json",
                "strip_wrapper": "message_batch", "platform": "qq",
            },
        )

    def test_sqlite_driver_extracts_events(self):
        driver = SqliteEventDriver(self.spec(), emit=None, store=self.store)
        events = driver.poll_once()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].content, "你好")
        self.assertEqual(events[0].channel_id, "g1")
        self.assertEqual(events[1].content, "普通消息")
        self.assertEqual(self.store.get_bridge_cursor("test_source"), "2")

    def test_ingestor_dedup(self):
        ingestor = BridgeIngestor(self.store)
        event = HubEvent(
            source="test_source", event_key="test_source:1",
            platform="qq", channel_id="g1", channel_name="群",
            actor_id="u1", actor_name="主人", content="你好",
        )
        self.assertTrue(asyncio.run(ingestor.handle(event)))
        self.assertFalse(asyncio.run(ingestor.handle(event)))
        self.assertEqual(len(self.store.list_events(limit=10)), 1)

    def test_bridge_spec_persistence(self):
        spec = {
            "source_id": "saved_source", "kind": "sqlite", "scope": "plugin",
            "locator": str(self.db), "options": {"table": "messages"},
        }
        self.store.upsert_bridge_spec(spec, auto_start=True, status="running")
        saved = self.store.list_bridge_specs(auto_start=True)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["options"]["table"], "messages")
        self.store.set_bridge_spec_status("saved_source", "stopped", auto_start=False)
        self.assertEqual(self.store.list_bridge_specs(auto_start=True), [])
        self.assertTrue(self.store.delete_bridge_spec("saved_source"))

    def test_engine_registers_generic_drivers(self):
        engine = default_engine(self.store)
        for kind in ("sqlite", "json", "log", "http"):
            self.assertIn(kind, engine.drivers)

    def test_json_driver_preview(self):
        path = self.tmp / "events.json"
        path.write_text(json.dumps({
            "events": [
                {"id": 1, "time": 100, "group": "g1", "user": "u1", "name": "主人", "text": "你好"},
                {"id": 2, "time": 101, "group": "g1", "user": "u2", "name": "朋友", "text": "在吗"},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        spec = BridgeSpec("json_source", "json", "plugin", str(path), {
            "items_path": "events", "id": "id", "time": "time",
            "channel": "group", "actor": "user", "actor_name": "name",
            "content": "text", "platform": "qq",
        })
        events = JsonEventDriver(spec, emit=None, store=self.store).preview()
        self.assertEqual([e.content for e in events], ["你好", "在吗"])
        self.assertEqual(events[0].event_key, "json_source:1")

    def test_jsonl_driver_uses_offset(self):
        path = self.tmp / "events.jsonl"
        path.write_text(
            json.dumps({"id": 1, "text": "第一条"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        spec = BridgeSpec("jsonl_source", "json", "plugin", str(path), {
            "id": "id", "content": "text", "platform": "qq",
        })
        driver = JsonEventDriver(spec, emit=None, store=self.store)
        self.assertEqual(len(driver.preview()), 1)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": 2, "text": "第二条"}, ensure_ascii=False) + "\n")
        events = driver._poll_jsonl()
        self.assertEqual([e.content for e in events], ["第一条", "第二条"])

    def test_log_driver_pattern(self):
        path = self.tmp / "app.log"
        path.write_text("2026-01-01|u1|g1|hello\nbad line\n", encoding="utf-8")
        spec = BridgeSpec("log_source", "log", "plugin", str(path), {
            "pattern": r"^(?P<time>[^|]+)\|(?P<actor>[^|]+)\|(?P<channel>[^|]+)\|(?P<content>.+)$",
            "platform": "qq",
        })
        driver = LogEventDriver(spec, emit=None, store=self.store)
        events = driver.preview()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].content, "hello")
        self.assertEqual(events[0].channel_id, "g1")


if __name__ == "__main__":
    unittest.main()
