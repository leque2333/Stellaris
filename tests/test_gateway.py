# -*- coding: utf-8 -*-
"""
网关端到端回归：用本地 mock 上游验证 /v1 的 tools、参数透传、真流式、记账与回灌。
不调用真实大模型，不修改生产库。
"""

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.mock_openai_upstream import start_mock  # noqa: E402

FIXTURE = Path(r"D:\机器人_完整版\data\memory.db")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class GatewayE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="anan_gateway_"))
        cls.db = cls.tmp / "memory.db"
        shutil.copy2(FIXTURE, cls.db)
        cls.mock, cls.mock_port = start_mock()

        cls.compute_cfg = cls.tmp / "compute_providers.json"
        cls.compute_cfg.write_text(json.dumps({
            "active_slots": {"persona_slot": "mock", "logic_slot": "mock"},
            "routing_policy": {"auto_dispatch_logic": False},
            "providers": {
                "mock": {
                    "name": "Mock Provider",
                    "auth_type": "official_api",
                    "base_url": f"http://127.0.0.1:{cls.mock_port}/v1",
                    "api_key": "test-key",
                    "model": "mock-model",
                    "temperature": 0.7,
                }
            },
        }, ensure_ascii=False), encoding="utf-8")

        cls.port = free_port()
        cls.clients_cfg = cls.tmp / "clients.json"
        cls.clients_cfg.write_text(json.dumps({
            "require_key": False,
            "clients": [{
                "id": "guest-client",
                "name": "访客端",
                "keys": ["hub_sk_guest"],
                "channel": "guest_channel",
                "default_actor": "guest",
                "allow_recall_memory": False,
                "allow_ingest_timeline": True,
                "rate_limit_per_minute": 60,
            }],
        }, ensure_ascii=False), encoding="utf-8")

        cls.policy_cfg = cls.tmp / "access_policy.json"
        cls.policy_cfg.write_text(json.dumps({
            "guest_enabled": True,
            "guest_timeline_enabled": True,
            "guest_default_affection": 50,
        }, ensure_ascii=False), encoding="utf-8")

        env = os.environ.copy()
        env.update({
            "COMPUTE_CONFIG_PATH": str(cls.compute_cfg),
            "HUB_CLIENTS_PATH": str(cls.clients_cfg),
            "HUB_ACCESS_POLICY_PATH": str(cls.policy_cfg),
            "MEMORY_HUB_DB_PATH": str(cls.db),
            "HUB_AGGREGATOR_DISABLED": "1",
            "HUB_DISCOVERY_DISABLED": "1",
            # 网关透传回归不测工具货架，显式关闭以免分类器参与
            "TOOL_SHELVES_DISABLED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "console" / "server.py"), str(cls.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        cls._wait_ready()

    @classmethod
    def _wait_ready(cls):
        deadline = time.time() + 20
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                out = cls.proc.stdout.read() if cls.proc.stdout else ""
                raise RuntimeError(f"server exited: {out}")
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.15)
        raise RuntimeError("server did not start in time")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.mock.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def _post(self, payload, headers=None):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
        hdrs.update(headers or {})
        req = urllib.request.Request(self._url("/v1/chat/completions"), data=data, headers=hdrs, method="POST")
        return urllib.request.urlopen(req, timeout=30)

    def test_hub_identity_round_trip(self):
        with self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "你是谁"}],
        }, {
            "X-User-Id": "1000000001",
            "X-User-Name": "LeQue",
            "X-Platform": "qq",
            "X-Channel": "qq_group:2000000001",
            "X-Channel-Type": "group",
        }) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            headers = dict(resp.headers)

        self.assertEqual(payload["hub"]["sender_uid"], "1000000001")
        self.assertEqual(payload["hub"]["platform"], "qq")
        self.assertEqual(headers["X-Hub-Sender-Uid"], "1000000001")
        self.assertEqual(headers["X-Hub-Platform"], "qq")

    def test_non_stream_tools_and_params(self):
        tools = [{
            "type": "function",
            "function": {
                "name": "mock_tool",
                "description": "demo",
                "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
            },
        }]
        with self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "帮我看看"}],
            "tools": tools,
            "temperature": 0.2,
            "max_tokens": 32,
            "stop": ["END"],
        }, {"X-Channel": "test_channel"}) as resp:
            self.assertEqual(resp.status, 200)
            payload = json.loads(resp.read().decode("utf-8"))

        message = payload["choices"][0]["message"]
        self.assertEqual(payload["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "mock_tool")
        self.assertIn("echo_tools=True", message["content"])
        self.assertIn("max=32", message["content"])
        self.assertIn("END", message["content"])
        self.assertEqual(payload["usage"]["prompt_tokens"], 21)

        # L1 调用索引 + L3 详情应已落库
        time.sleep(0.2)
        conn = sqlite3.connect(str(self.db))
        try:
            layers = {r[0] for r in conn.execute(
                "SELECT layer FROM invocations WHERE channel_id='test_channel'"
            )}
            detail_count = conn.execute("SELECT COUNT(*) FROM memory_details").fetchone()[0]
        finally:
            conn.close()
        self.assertTrue({"channel", "model", "tool"}.issubset(layers), layers)
        self.assertGreaterEqual(detail_count, 3)

    def test_stream_real_sse(self):
        with self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "流式打个招呼"}],
            "stream": True,
        }, {"X-Channel": "test_stream"}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))
            raw = b""
            deadline = time.time() + 20
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                raw += line
                if b"[DONE]" in line:
                    break

        text = raw.decode("utf-8", "ignore")
        self.assertIn("data:", text)
        self.assertIn("流", text)
        self.assertIn("试", text)
        self.assertIn("[DONE]", text)

        # 流结束后服务端才落账/回灌，稍等再查库
        time.sleep(0.6)
        conn = sqlite3.connect(str(self.db))
        try:
            before_stream_events = conn.execute(
                "SELECT COUNT(*) FROM agent_timeline WHERE source_module='test_stream'"
            ).fetchone()[0]
            stream_usage = conn.execute(
                "SELECT COUNT(*) FROM token_usage WHERE source='test_stream'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(stream_usage, 1)
        self.assertGreaterEqual(before_stream_events, 1)

    def test_client_key_guest_isolation(self):
        with self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "访客你好"}],
        }, {"Authorization": "Bearer hub_sk_guest"}) as resp:
            self.assertEqual(resp.status, 200)
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertIn("echo_tools=False", payload["choices"][0]["message"]["content"])

        time.sleep(0.5)
        conn = sqlite3.connect(str(self.db))
        try:
            row = conn.execute(
                "SELECT actor_id, tag_primary FROM agent_timeline "
                "WHERE source_module='guest_channel' ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertTrue(str(row[0]).startswith("guest_"))

    def test_invalid_key_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self._post({
                "model": "client-model",
                "messages": [{"role": "user", "content": "hi"}],
            }, {"Authorization": "Bearer not-a-valid-key"})
        self.assertEqual(ctx.exception.code, 401)
        payload = json.loads(ctx.exception.read().decode("utf-8"))
        ctx.exception.close()
        self.assertEqual(payload["error"]["code"], "invalid_key")

    def test_models_endpoint(self):
        with urllib.request.urlopen(self._url("/v1/models"), timeout=10) as resp:
            self.assertEqual(resp.status, 200)
            payload = json.loads(resp.read().decode("utf-8"))
        self.assertIn("data", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
