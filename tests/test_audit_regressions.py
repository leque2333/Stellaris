# -*- coding: utf-8 -*-
"""验收审计回归用例（第一轮审计 + 第二轮复核报告）。

这里只放**不需要真实 HTTP 服务**的断言，任何环境都能跑；
需要起服务的 E2E 回归在 tests/test_archive_api.py / test_gateway.py 里。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core import identity  # noqa: E402
from agent_core.clients import ClientRegistry  # noqa: E402
from agent_core.storage import SQLiteStore  # noqa: E402


def _handler(**attrs):
    handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
    handler.headers = {}
    for key, value in attrs.items():
        setattr(handler, key, value)
    return handler


class DefaultDeploymentAuthTest(unittest.TestCase):
    """复核报告 §3：默认部署（没有 clients.json）不能把匿名请求当管理员。"""

    def _tmp_path(self, name="clients.json"):
        self._dir = Path(tempfile.mkdtemp(prefix="hub_clients_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self._dir, ignore_errors=True))
        return self._dir / name

    def test_no_clients_config_denies_hub_archives(self):
        reg = ClientRegistry(self._tmp_path("missing.json"))
        with patch.object(server, "client_registry", reg):
            handler = _handler()
            profile, err = handler._hub_client(require_admin=True)
        self.assertIsNone(profile)
        self.assertEqual(err, "missing_key")

    def test_no_clients_config_denies_v1_master(self):
        """没有配置时，光加 X-User-Id: master 也不能进 /v1。"""
        reg = ClientRegistry(self._tmp_path("missing.json"))
        with patch.object(server, "client_registry", reg):
            handler = _handler()
            handler.headers = {"X-User-Id": "master", "X-Platform": "qq"}
            profile, err = handler._authenticate_gateway_client()
        self.assertIsNone(profile)
        self.assertEqual(err, "missing_key")

    def test_require_key_false_denies_master_spoof(self):
        """显式关掉 require_key 时，匿名也只能是 guest，不能伪造成主人。"""
        path = self._tmp_path()
        path.write_text(json.dumps({
            "require_key": False,
            "clients": [{"id": "app", "keys": ["k1"], "default_actor": "master"}],
        }), encoding="utf-8")
        reg = ClientRegistry(path)
        profile = reg.resolve("").profile
        self.assertEqual(profile.default_actor, "guest")
        handler = _handler()
        actor_id, _, role = handler._resolve_gateway_actor(profile, "qq", "master", "")
        self.assertTrue(actor_id.startswith("guest_"))
        self.assertEqual(role, "guest")

    def test_admin_hub_requires_real_key_even_without_require_key(self):
        """require_key=false 时，管理接口仍然必须带 Key。"""
        path = self._tmp_path()
        path.write_text(json.dumps({
            "require_key": False,
            "clients": [{"id": "app", "keys": ["k1"], "default_actor": "master"}],
        }), encoding="utf-8")
        reg = ClientRegistry(path)
        with patch.object(server, "client_registry", reg):
            handler = _handler()
            profile, err = handler._hub_client(require_admin=True)
        self.assertIsNone(profile)
        self.assertEqual(err, "missing_key")

    def test_example_configs_require_key(self):
        """示例配置必须是 require_key=true，否则默认又回到不安全。

        只断言随代码发布的模板（仓库根目录）；`data/` 是本机私有目录，
        里面的旧副本不该让测试变红。
        """
        template = ROOT / "clients.example.json"
        self.assertTrue(template.is_file(), "缺少 clients.example.json 模板")
        data = json.loads(template.read_text(encoding="utf-8"))
        self.assertIs(data.get("require_key"), True)
        self.assertTrue(data.get("clients"))


class StaticTraversalTest(unittest.TestCase):
    """P0-1：静态目录路径穿越必须被挡死。"""

    def test_dotdot_blocked(self):
        handler = _handler()
        self.assertIsNone(handler._static_path("../../data/clients.json"))
        self.assertIsNone(handler._static_path("..\\..\\data\\auth_config.json"))

    def test_url_encoded_dotdot_blocked(self):
        handler = _handler()
        self.assertIsNone(handler._static_path("..%2f..%2fdata%2fmodels_config.json"))

    def test_absolute_and_unc_blocked(self):
        handler = _handler()
        self.assertIsNone(handler._static_path("C:/Windows/win.ini"))
        self.assertIsNone(handler._static_path("//attacker/share/x.js"))

    def test_disallowed_extension_blocked(self):
        handler = _handler()
        # 目录里真实存在的 .py 也不能被当静态资源发出去
        self.assertIsNone(handler._static_path("../server.py"))

    def test_allowed_static_file(self):
        handler = _handler()
        path = handler._static_path("js/core.js")
        self.assertIsNotNone(path)
        self.assertTrue(path.replace("\\", "/").endswith("console/static/js/core.js"))


class StreamCorsTest(unittest.TestCase):
    """P1-10：SSE 响应头不能固定 `Access-Control-Allow-Origin: *`。"""

    def _prepare(self, origin):
        handler = _handler()
        handler.headers = {"Host": "hub.local:18895"}
        if origin:
            handler.headers["Origin"] = origin
        handler.close_connection = False
        handler.send_response = lambda *a, **k: None
        handler.end_headers = lambda: None
        handler._hub_identity_headers = lambda ctx: {}
        recorded = []

        def _send_header(key, value):
            recorded.append((key.lower(), value))

        handler.send_header = _send_header
        handler._stream_prepare({})
        return dict(recorded)

    def test_cross_origin_gets_no_cors_header(self):
        headers = self._prepare("https://evil.example")
        self.assertNotIn("access-control-allow-origin", headers)
        self.assertEqual(headers.get("content-type"), "text/event-stream; charset=utf-8")

    def test_same_origin_echoes_origin(self):
        headers = self._prepare("https://hub.local:18895")
        self.assertEqual(headers.get("access-control-allow-origin"), "https://hub.local:18895")
        self.assertNotEqual(headers.get("access-control-allow-origin"), "*")


class AdapterRootTest(unittest.TestCase):
    """P0-3：适配器只允许扫描导入目录。"""

    def test_outside_root_rejected(self):
        self.assertIsNone(server._adapter_repo_path(str(ROOT)))
        self.assertIsNone(server._adapter_repo_path("C:\\Windows"))

    def test_inside_default_root_accepted(self):
        target = Path(ROOT) / "data" / "imports"
        target.mkdir(parents=True, exist_ok=True)
        self.assertIsNotNone(server._adapter_repo_path(str(target)))

    def test_env_can_extend_roots(self):
        extra = Path(tempfile.mkdtemp(prefix="adapter_roots_"))
        with patch.dict(os.environ, {server.ADAPTER_ROOT_ENV: str(extra)}, clear=False):
            self.assertIsNotNone(server._adapter_repo_path(str(extra)))


class BodyLimitTest(unittest.TestCase):
    """P1-9：超长请求体直接 413，不读进内存。"""

    def test_oversized_body_rejected(self):
        handler = _handler()
        handler.headers = {"Content-Length": str(server.MAX_BODY_BYTES + 1)}
        sent = {}

        def _fake_json(data, status=200, extra_headers=None):
            sent["data"] = data
            sent["status"] = status

        handler._send_json = _fake_json
        self.assertIsNone(handler._read_raw_body())
        self.assertEqual(sent["status"], 413)

    def test_invalid_length_rejected(self):
        handler = _handler()
        handler.headers = {"Content-Length": "abc"}
        sent = {}
        handler._send_json = lambda data, status=200, extra_headers=None: sent.update(status=status)
        self.assertIsNone(handler._read_raw_body())
        self.assertEqual(sent["status"], 400)


class IdentityRegressionTest(unittest.TestCase):
    """P1-3 / P1-5：无 UID 不落主人，通用词别名不再认主。"""

    def test_missing_uid_is_guest(self):
        handler = _handler()
        profile = type("P", (), {"id": "astrbot", "name": "AstrBot", "default_actor": "master"})()
        actor_id, _, role = handler._resolve_gateway_actor(profile, "qq", "", "")
        self.assertTrue(actor_id.startswith("guest_"))
        self.assertEqual(role, "guest")

    def test_generic_alias_is_not_master(self):
        """默认别名里不能再有 admin/leque 这类通用词（测试夹具另行显式配置）。"""
        import importlib

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASTER_ALIASES", None)
            reloaded = importlib.reload(identity)
            try:
                for uid in ("admin", "leque"):
                    actor_id, _, _ = reloaded.IdentityResolver.resolve_actor("qq", uid)
                    self.assertNotEqual(actor_id, "master", uid)
                    self.assertFalse(reloaded.IdentityResolver.is_master(uid))
                # 显式的主人身份仍然认
                self.assertEqual(
                    reloaded.IdentityResolver.resolve_actor("qq", "master")[0], "master"
                )
                self.assertTrue(reloaded.IdentityResolver.is_master("master"))
            finally:
                importlib.reload(identity)  # 还原测试进程里的别名配置

    def test_explicit_master_still_works(self):
        self.assertEqual(identity.IdentityResolver.resolve_actor("qq", "master")[0], "master")

    def test_master_qq_is_master(self):
        master_qq = identity.IdentityResolver.get_master_qq()
        if not master_qq:
            self.skipTest("未配置 MASTER_QQ")
        self.assertEqual(identity.IdentityResolver.resolve_actor("qq", master_qq)[0], "master")

    def test_sniff_before_strip(self):
        handler = _handler()
        ids = handler._gateway_ids(
            {}, [{"role": "user", "content": "乐阙(2000000001): 你好"}], "qq:123",
        )
        self.assertEqual(ids["raw_user_id"], "2000000001")
        self.assertEqual(ids["user_query"], "你好")


class FactLifecycleTest(unittest.TestCase):
    """P1-10 / P1-11：事实会 supersede，低置信度进 pending。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fact_life_"))
        self.store = SQLiteStore(self.tmp / "memory.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _active(self):
        return [f["content"] for f in self.store.get_active_facts("arc_master", limit=20)]

    def test_same_axis_supersedes(self):
        self.store.add_fact("arc_master", "喜欢猫", category="喜好")
        self.store.add_fact("arc_master", "喜欢狗", category="喜好")
        active = self._active()
        self.assertIn("喜欢狗", active)
        self.assertNotIn("喜欢猫", active)

    def test_different_axis_coexists(self):
        self.store.add_fact("arc_master", "喜欢猫", category="喜好")
        self.store.add_fact("arc_master", "讨厌狗", category="喜好")
        active = self._active()
        self.assertIn("喜欢猫", active)
        self.assertIn("讨厌狗", active)

    def test_generic_category_never_supersedes(self):
        self.store.add_fact("arc_master", "喜欢二次元", category="preference")
        self.store.add_fact("arc_master", "使用 Windows", category="preference")
        active = self._active()
        self.assertIn("喜欢二次元", active)
        self.assertIn("使用 Windows", active)

    def test_low_confidence_goes_pending(self):
        fact_id = self.store.add_fact("arc_master", "生日是 5 月 1 日", category="生日", confidence=0.2)
        self.assertTrue(fact_id)
        self.assertNotIn("生日是 5 月 1 日", self._active())

    def test_duplicate_content_not_repeated(self):
        first = self.store.add_fact("arc_master", "喜欢猫", category="喜好")
        second = self.store.add_fact("arc_master", "喜欢猫", category="喜好")
        self.assertEqual(first, second)
        self.assertEqual(self._active().count("喜欢猫"), 1)


class ApiKeyRedactionTest(unittest.TestCase):
    """P1-7：GET 只回掩码，保存时掩码不会把真 Key 写坏。"""

    def setUp(self):
        from console.modules import model_service

        self.svc = model_service

    def test_redact_removes_api_key(self):
        cfg = {
            "master_credentials": [{"name": "a", "base_url": "u", "api_key": "sk-abcdefghi1234"}],
            "function_credentials": [{"name": "b", "base_url": "v", "api_key": "sk-zzzzzzzzz999"}],
            "api_pool": [{"api_key": "sk-poolpoolpool1"}],
        }
        safe = self.svc.redact_models_config(cfg)
        for pool in ("master_credentials", "function_credentials", "api_pool"):
            for item in safe[pool]:
                self.assertNotIn("api_key", item)
                self.assertTrue(item["has_key"])
                self.assertIn("****", item["api_key_masked"])

    def test_masked_value_restores_old_key(self):
        old = {"master_credentials": [{"name": "a", "base_url": "u", "api_key": "sk-real-key-12345"}]}
        new = {"master_credentials": [{"name": "a", "base_url": "u",
                                       "api_key": "sk-****2345", "has_key": True}]}
        self.svc._restore_masked_keys(new, old)
        self.assertEqual(new["master_credentials"][0]["api_key"], "sk-real-key-12345")

    def test_explicit_new_key_kept(self):
        old = {"master_credentials": [{"name": "a", "base_url": "u", "api_key": "sk-old"}]}
        new = {"master_credentials": [{"name": "a", "base_url": "u", "api_key": "sk-brand-new"}]}
        self.svc._restore_masked_keys(new, old)
        self.assertEqual(new["master_credentials"][0]["api_key"], "sk-brand-new")


class HostControlTest(unittest.TestCase):
    """P1-2：宿主机命令执行默认关闭。"""

    def test_powershell_off_by_default(self):
        from agent_core import host_control

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(host_control.POWERSHELL_ENV, None)
            self.assertFalse(host_control.powershell_enabled())
            self.assertNotIn("powershell", host_control.available_actions())


class StaticContentTypeTest(unittest.TestCase):
    def test_only_whitelisted_types_sent(self):
        handler = _handler()
        self.assertIsNone(handler._static_path("js/../../data/clients.json"))
        self.assertIsNotNone(handler._static_path("js/core.js"))


class ImportFormatTest(unittest.TestCase):
    """B27：ChatGPT 导出（顶层数组）能被识别。"""

    def test_chatgpt_array_detected(self):
        from agent_core.memory import portability

        payload = json.dumps([{"title": "t", "mapping": {"a": {"message": None}}}])
        self.assertEqual(portability.detect_format(payload), "chatgpt")

    def test_plain_text_still_text(self):
        from agent_core.memory import portability

        self.assertEqual(portability.detect_format("随便聊聊"), "text")


if __name__ == "__main__":
    unittest.main()
