# -*- coding: utf-8 -*-
"""档案 API：创建 / 列表 / 解析 / 改人格 / 挂平台账号（不依赖真实模型）"""

import json
import os
import shutil
import socket
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

from agent_core.identity import IdentityResolver  # noqa: E402

FIXTURE = Path(r"D:\机器人_完整版\data\memory.db")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ArchiveApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="anan_archive_api_"))
        cls.db = cls.tmp / "memory.db"
        shutil.copy2(FIXTURE, cls.db)

        cls.clients = cls.tmp / "clients.json"
        cls.clients.write_text(json.dumps({
            "require_key": True,
            "clients": [
                {"id": "admin", "name": "管理员", "keys": ["hub_sk_admin"], "default_actor": "master"},
                {"id": "guest", "name": "访客", "keys": ["hub_sk_guest"], "default_actor": "guest"},
            ],
        }), encoding="utf-8")

        cls.port = free_port()
        cls.auth_path = cls.tmp / "auth_config.json"
        env = os.environ.copy()
        env.update({
            "MEMORY_HUB_DB_PATH": str(cls.db),
            "HUB_CLIENTS_PATH": str(cls.clients),
            "HUB_AUTH_CONFIG_PATH": str(cls.auth_path),
            "HUB_ACCESS_POLICY_PATH": str(cls.tmp / "access_policy.json"),
            "HUB_AUTH_PASSWORD": "test-auth-pass",
            "HUB_TOOL_PERMISSIONS_PATH": str(cls.tmp / "tool_permissions.json"),
            "HUB_AGGREGATOR_DISABLED": "1",
            "HUB_DISCOVERY_DISABLED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "console" / "server.py"), str(cls.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        cls._wait_ready()
        cls.cookie = cls._login()

    @classmethod
    def _login(cls):
        data = json.dumps({"username": "admin", "password": "test-auth-pass"}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/api/auth/login",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            cookie = resp.headers.get("Set-Cookie", "").split(";")[0]
        if not cookie:
            raise RuntimeError("login did not return a session cookie")
        return cookie

    @classmethod
    def _wait_ready(cls):
        deadline = time.time() + 20
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError(cls.proc.stdout.read() if cls.proc.stdout else "server exited")
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.15)
        raise RuntimeError("server did not start")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _req(self, method, path, body=None, key="hub_sk_admin"):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if getattr(self, "cookie", ""):
            headers["Cookie"] = self.cookie
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            e.close()
            return e.code, json.loads(raw) if raw else {}

    def test_resolve_admin(self):
        uid = IdentityResolver.get_master_qq()
        status, data = self._req("GET", f"/hub/v1/archives/resolve?platform=qq&user_id={uid}")
        self.assertEqual(status, 200)
        self.assertEqual(data["role"], "admin")
        self.assertEqual(data["archive_id"], os.getenv("HUB_MASTER_ARCHIVE_ID", "arc_master"))
        self.assertNotIn("persona_id", data)
        self.assertNotIn("persona_sections", data)
        self.assertGreaterEqual(data.get("affection", 0), 90)
        self.assertIn("facts", data)

    def test_resolve_unknown_returns_404(self):
        status, _ = self._req("GET", "/hub/v1/archives/resolve?platform=qq&user_id=999999")
        self.assertEqual(status, 404)

    def test_missing_key_rejected(self):
        status, data = self._req("GET", "/hub/v1/archives/resolve?platform=qq&user_id=1", key=None)
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["code"], "missing_key")

    def test_guest_cannot_create(self):
        status, data = self._req("POST", "/hub/v1/archives", {
            "platform": "qq", "user_id": "555555", "display_name": "访客建档案",
        }, key="hub_sk_guest")
        self.assertEqual(status, 403)
        self.assertEqual(data["error"]["code"], "forbidden")

    def test_access_policy_defaults_and_save(self):
        status, data = self._req("GET", "/api/access_policy", key=None)
        self.assertEqual(status, 200)
        self.assertFalse(data["policy"]["guest_enabled"])
        self.assertFalse(data["policy"]["guest_timeline_enabled"])

        status, saved = self._req("POST", "/api/access_policy", {
            "guest_enabled": True,
            "guest_timeline_enabled": True,
            "guest_default_affection": 50,
        }, key=None)
        self.assertEqual(status, 200)
        self.assertTrue(saved["policy"]["guest_enabled"])

    def test_create_guest_archive(self):
        status, created = self._req("POST", "/api/archives", {
            "display_name": "测试访客",
            "role": "guest",
        }, key=None)
        self.assertEqual(status, 201)
        self.assertEqual(created["role"], "guest")

    def test_create_list_get_update(self):
        status, created = self._req("POST", "/hub/v1/archives", {
            "platform": "qq", "user_id": "123456",
            "display_name": "小明",
        })
        self.assertEqual(status, 201)
        aid = created["archive_id"]
        self.assertNotIn("persona_id", created)
        self.assertNotIn("persona_sections", created)
        self.assertEqual(created["identities"][0]["platform"], "qq")

        # 重复创建 -> 409
        status, dup = self._req("POST", "/hub/v1/archives", {
            "platform": "qq", "user_id": "123456", "display_name": "小明2",
        })
        self.assertEqual(status, 409)

        # 列表
        status, listing = self._req("GET", "/hub/v1/archives")
        self.assertEqual(status, 200)
        ids = {a["archive_id"] for a in listing["data"]}
        self.assertIn(aid, ids)
        self.assertIn("arc_master", ids)

        # 详情
        status, detail = self._req("GET", f"/hub/v1/archives/{aid}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["display_name"], "小明")

        # 改人物信息
        status, updated = self._req("PATCH", f"/hub/v1/archives/{aid}", {
            "display_name": "小明改",
        })
        self.assertEqual(status, 200)
        self.assertEqual(updated["display_name"], "小明改")

    def test_console_api_without_key(self):
        status, listing = self._req("GET", "/api/archives", key=None)
        self.assertEqual(status, 200)
        self.assertIn("data", listing)

        # 新建未绑定平台的人物档案
        status, created = self._req("POST", "/api/archives", {
            "display_name": "我的 AI",
        }, key=None)
        self.assertEqual(status, 201)
        aid = created["archive_id"]
        self.assertNotIn("persona_sections", created)
        self.assertEqual(created["identities"], [])
        self.assertIsNone(created["affection"], "新档案还没有交流过，好感度应为未定")

        # 编辑人物信息
        status, updated = self._req("PATCH", f"/api/archives/{aid}", {
            "display_name": "新名字",
        }, key=None)
        self.assertEqual(status, 200)
        self.assertEqual(updated["display_name"], "新名字")

    def test_console_identity_archive(self):
        status, created = self._req("POST", "/api/archives", {
            "platform": "qq", "user_id": "777777", "display_name": "控制台用户",
        }, key=None)
        self.assertEqual(status, 201)
        aid = created["archive_id"]
        self.assertIn("qq", {i["platform"] for i in created["identities"]})

        status, linked = self._req("POST", f"/api/archives/{aid}/identities", {
            "platform": "bilibili", "user_id": "999999",
        }, key=None)
        self.assertEqual(status, 200)
        self.assertIn("bilibili", {i["platform"] for i in linked["identities"]})

    def test_memory_facts_api(self):
        status, data = self._req("POST", "/api/memory/facts", {
            "subject_id": "u1",
            "predicate": "测试暗号",
            "value": "紫罗兰",
        })
        self.assertEqual(status, 200)
        fact_id = data["fact"]["fact_id"]

        status, data = self._req("GET", "/api/memory/facts?status=active")
        self.assertEqual(status, 200)
        self.assertTrue(any(item["fact_id"] == fact_id for item in data["facts"]))

        status, updated = self._req("POST", "/api/memory/facts", {
            "fact_id": fact_id,
            "value": "琥珀",
        })
        self.assertEqual(status, 200)
        new_fact_id = updated["fact"]["fact_id"]
        status, data = self._req("GET", "/api/memory/facts?status=active")
        self.assertEqual([item["value"] for item in data["facts"] if item["subject_id"] == "u1"], ["琥珀"])

        status, _ = self._req("DELETE", f"/api/memory/facts?fact_id={new_fact_id}")
        self.assertEqual(status, 200)

    def test_module_binding_shows_in_person_archive(self):
        status, created = self._req("POST", "/api/archives", {
            "display_name": "模块匹配人物",
        }, key=None)
        self.assertEqual(status, 201)
        aid = created["archive_id"]
        self.assertEqual(created["identities"], [])

        status, _ = self._req("POST", "/api/bindings", {
            "platform": "qq",
            "external_id": "onebot_napcat:FriendMessage:111222",
            "archive_id": aid,
            "confidence": 1.0,
            "verified_by": "console",
        }, key=None)
        self.assertEqual(status, 200)

        status, detail = self._req("GET", f"/api/archives/{aid}", key=None)
        self.assertEqual(status, 200)
        pairs = {(i["platform"], i["platform_user_id"]) for i in detail["identities"]}
        self.assertIn(("qq", "111222"), pairs)

    def test_static_assets_exist(self):
        for asset in (
            "/static/css/archives.css", "/static/js/archives.js",
            "/static/css/overview.css", "/static/js/overview.js",
            "/static/css/timeline.css", "/static/js/timeline.js",
            "/static/css/ledger.css", "/static/js/ledger.js",
        ):
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{asset}", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertGreater(len(resp.read()), 100)

    def test_link_identity(self):
        status, created = self._req("POST", "/hub/v1/archives", {
            "platform": "qq", "user_id": "222222", "display_name": "小红",
        })
        self.assertEqual(status, 201)
        aid = created["archive_id"]

        status, linked = self._req("POST", f"/hub/v1/archives/{aid}/identities", {
            "platform": "bilibili", "user_id": "333333", "display_name": "小红B站",
        })
        self.assertEqual(status, 200)
        platforms = {i["platform"] for i in linked["identities"]}
        self.assertIn("bilibili", platforms)

        status, resolved = self._req(
            "GET", "/hub/v1/archives/resolve?platform=bilibili&user_id=333333"
        )
        self.assertEqual(status, 200)
        self.assertEqual(resolved["archive_id"], aid)

        # 同一平台账号挂到另一个档案 -> 409
        status, other = self._req("POST", "/hub/v1/archives", {
            "platform": "qq", "user_id": "444444", "display_name": "小刚",
        })
        self.assertEqual(status, 201)
        status, _ = self._req("POST", f"/hub/v1/archives/{other['archive_id']}/identities", {
            "platform": "bilibili", "user_id": "333333",
        })
        self.assertEqual(status, 409)


if __name__ == "__main__":
    unittest.main(verbosity=2)
