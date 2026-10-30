# -*- coding: utf-8 -*-
"""HTTP 驱动：鉴权头、GET/POST、分页"""

import asyncio
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.bridge import BridgeSpec, HttpEventDriver  # noqa: E402


class HttpDriverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.calls = {}

        class Handler(BaseHTTPRequestHandler):
            def _send(self, payload, status=200):
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                HttpDriverTest.requests.append({
                    "method": "GET", "path": self.path,
                    "auth": self.headers.get("Authorization"),
                })
                parsed = urlparse(self.path)
                if parsed.path.startswith("/retry"):
                    count = HttpDriverTest.calls.get("retry", 0)
                    HttpDriverTest.calls["retry"] = count + 1
                    if count == 0:
                        self._send({"error": "retry"}, status=500)
                        return
                    self._send({"events": [{"id": 20, "content": "retried", "time": 120}]})
                    return
                if parsed.path.startswith("/cursor"):
                    cursor = (parse_qs(parsed.query).get("cursor") or [""])[0]
                    if cursor == "next":
                        self._send({"events": [{"id": 22, "content": "second", "time": 122}], "next": None})
                    else:
                        self._send({"events": [{"id": 21, "content": "first", "time": 121}], "next": "next"})
                    return
                if "page=2" in self.path:
                    self._send({"events": [{"id": 3, "content": "c", "time": 103}]})
                else:
                    self._send({"events": [
                        {"id": 1, "content": "a", "time": 101},
                        {"id": 2, "content": "b", "time": 102},
                    ]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                HttpDriverTest.requests.append({
                    "method": "POST", "path": self.path, "body": body,
                    "auth": self.headers.get("Authorization"),
                })
                self._send({"events": [{"id": 10, "content": "post", "time": 110}]})

            def log_message(self, *args):
                return

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_get_with_auth_and_pagination(self):
        spec = BridgeSpec(
            "http_get", "http", "protocol", f"http://127.0.0.1:{self.port}/events",
            {
                "headers": {"Authorization": "Bearer test"},
                "items_path": "events", "id": "id", "content": "content", "time": "time",
                "pagination": {"param": "page", "start": 1, "page_size": 2, "max_pages": 3},
            },
        )
        events = asyncio.run(HttpEventDriver(spec, emit=None).poll_once())
        self.assertEqual([e.content for e in events], ["a", "b", "c"])
        request = [r for r in self.requests if "page=1" in r["path"]][0]
        self.assertEqual(request["auth"], "Bearer test")
        self.assertIn("page=1", request["path"])

    def test_post_body(self):
        spec = BridgeSpec(
            "http_post", "http", "protocol", f"http://127.0.0.1:{self.port}/events",
            {
                "method": "POST", "body": {"filter": "all"},
                "headers": {"X-Token": "t"},
                "items_path": "events", "id": "id", "content": "content", "time": "time",
            },
        )
        events = asyncio.run(HttpEventDriver(spec, emit=None).poll_once())
        self.assertEqual(events[0].content, "post")
        post = [r for r in self.requests if r["method"] == "POST"][0]
        self.assertIn('"filter"', post["body"])

    def test_retry_on_500(self):
        spec = BridgeSpec(
            "http_retry", "http", "protocol", f"http://127.0.0.1:{self.port}/retry",
            {
                "items_path": "events", "id": "id", "content": "content", "time": "time",
                "retry": {"max_attempts": 3, "backoff": 0.01},
            },
        )
        events = asyncio.run(HttpEventDriver(spec, emit=None).poll_once())
        self.assertEqual(events[0].content, "retried")

    def test_cursor_pagination(self):
        spec = BridgeSpec(
            "http_cursor", "http", "protocol", f"http://127.0.0.1:{self.port}/cursor",
            {
                "items_path": "events", "id": "id", "content": "content", "time": "time",
                "pagination": {
                    "mode": "cursor", "next_path": "next",
                    "cursor_param": "cursor", "max_pages": 5,
                },
            },
        )
        events = asyncio.run(HttpEventDriver(spec, emit=None).poll_once())
        self.assertEqual([e.content for e in events], ["first", "second"])


if __name__ == "__main__":
    unittest.main()
