# -*- coding: utf-8 -*-
"""执行 AI / 启发式生成 BridgeSpec"""

import asyncio
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.bridge import BridgeSpecInferer  # noqa: E402


class _NoKeyLLM:
    api_key = ""

    async def generate_with_tools(self, **kwargs):
        return "", []


class _FakeLLM:
    api_key = "fake"

    async def generate_with_tools(self, **kwargs):
        return json.dumps({
            "source_id": "auto_ai", "kind": "sqlite", "scope": "plugin",
            "locator": "", "options": {
                "table": "messages", "cursor": "id", "id": "id",
                "time": "created_at", "channel": "group_id",
                "actor": "sender_id", "content": "content",
            },
            "confidence": 0.9,
        }), []


class InferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_infer_"))
        self.db = self.tmp / "messages.db"
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE messages (id INTEGER, created_at TEXT, group_id TEXT, sender_id TEXT, content TEXT)"
        )
        conn.executemany("INSERT INTO messages VALUES (?, ?, ?, ?, ?)", [
            (1, "2026-01-01T10:00:00", "g1", "u1", "你好"),
            (2, "2026-01-01T10:01:00", "g1", "u2", "在吗"),
        ])
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_heuristic_sqlite(self):
        result = asyncio.run(BridgeSpecInferer(llm=_NoKeyLLM()).infer(str(self.db)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["spec"]["kind"], "sqlite")
        self.assertEqual(result["spec"]["options"]["table"], "messages")
        self.assertEqual(len(result["preview"]), 2)

    def test_ai_spec(self):
        result = asyncio.run(BridgeSpecInferer(llm=_FakeLLM()).infer(str(self.db)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "ai")
        self.assertEqual(result["spec"]["source_id"], "auto_ai")

    def test_json_heuristic(self):
        path = self.tmp / "events.json"
        path.write_text(json.dumps({
            "events": [{"id": 1, "text": "hi", "user": "u1", "group": "g1"}]
        }), encoding="utf-8")
        result = asyncio.run(BridgeSpecInferer(llm=_NoKeyLLM()).infer(str(path)))
        self.assertTrue(result["ok"])
        self.assertEqual(result["spec"]["kind"], "json")
        self.assertEqual(result["spec"]["options"]["items_path"], "events")

    def test_url_heuristic(self):
        payload = {"events": [{"id": 1, "time": 100, "channel": "g1", "actor": "u1", "content": "hi"}]}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/events"
            result = asyncio.run(BridgeSpecInferer(llm=_NoKeyLLM()).infer(url))
            self.assertTrue(result["ok"])
            self.assertEqual(result["spec"]["kind"], "http")
            self.assertEqual(result["spec"]["options"]["items_path"], "events")
            self.assertEqual(len(result["preview"]), 1)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
