# -*- coding: utf-8 -*-
"""工具按需加载（Tool Shelves）：快判、分类、裁剪、兜底、字段删除的回归。

全部不联网：
- 快判/裁剪/兜底用注入的假分类器；
- 分类器 JSON 解析用 mock 掉 compute_manager 的单次请求；
- 端到端用本地 mock 上游（persona 与 classifier 分开两个 mock）。
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="anan_shelves_"))
os.environ["TOOL_SHELVES_METRICS_DIR"] = str(_TMP_ROOT / "metrics")
os.environ["TOOL_SHELVES_LOG_DIR"] = str(_TMP_ROOT / "logs")

from agent_core.tool_shelves import fastpath  # noqa: E402
from agent_core.tool_shelves.classifier import ClassifierResult, parse_output  # noqa: E402
from agent_core.tool_shelves.config import ShelfConfig, default_settings  # noqa: E402
from agent_core.tool_shelves.gate import ToolGate  # noqa: E402
from agent_core.tool_shelves.session import SessionStore  # noqa: E402

FIXTURE_DB = Path(r"D:\机器人_完整版\data\memory.db")


# ----------------------------------------------------------------------
# 共用夹具
# ----------------------------------------------------------------------
TOOLS = [
    {"type": "function", "function": {"name": "mock_tool", "description": "搜索", "parameters": {}}},
    {"type": "function", "function": {"name": "media_demo", "description": "放歌", "parameters": {}}},
    {"type": "function", "function": {"name": "server_demo", "description": "服务器", "parameters": {}}},
    {"type": "function", "function": {"name": "send_message_to_user", "description": "发消息", "parameters": {}}},
    {"type": "function", "function": {"name": "future_task", "description": "定时", "parameters": {}}},
]

BASE_SETTINGS = {
    "version": 1,
    "gateway": {
        "enabled": True,
        "mode": "enforce_soft",
        "exempt_clients": [],
        "max_tools": 12,
        "sticky_tool_turns": 2,
        "on_failure": "previous_then_all",
        "classifier": {"slot": "logic_slot", "timeout_ms": 2500, "confidence_floor": 0.45},
        "auto_expand": {"enabled": True, "retry_on_empty": True},
    },
    "fast_path": {
        "min_chars": 2,
        "max_chars": 10,
        "trivial_regex": default_settings()["fast_path"]["trivial_regex"],
        "force_tools_regex": default_settings()["fast_path"]["force_tools_regex"],
    },
    "always_available": ["send_message_to_user", "future_task"],
    "unknown_tool_policy": "always",
    "categories": {
        "media": {"label": "影音类", "brief": "放歌", "tools": ["media_demo"]},
        "server": {"label": "服务器类", "brief": "系统", "tools": ["server_demo"]},
    },
}


class _MetricsStub:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)

    def snapshot(self):
        return {"today": {}, "totals": {}, "recent": [], "days": []}


def _cfg(settings=None) -> ShelfConfig:
    """测试配置一律写无 BOM 的 UTF-8，避免 json 解析失败掩盖真实断言。"""
    tmp = Path(tempfile.mkdtemp(prefix="shelves_cfg_"))
    path = tmp / "tool_shelves.json"
    path.write_text(json.dumps(settings or BASE_SETTINGS, ensure_ascii=False), encoding="utf-8")
    return ShelfConfig(path)


def _gate(settings=None, classifier=None):
    cfg = _cfg(settings)
    metrics = _MetricsStub()
    gate = ToolGate(cfg=cfg, classifier=classifier or (lambda *a, **k: ClassifierResult(ok=True,
                     need_tools=False, categories=[], confidence=0.9)), sessions=SessionStore(), metrics=metrics)
    return gate, metrics


def _msgs(text="帮我找首歌", role="user"):
    return [{"role": "user", "content": "前面的话"}, {"role": role, "content": text}]


class FastPathTest(unittest.TestCase):
    def setUp(self):
        self.settings = BASE_SETTINGS

    def test_pure_vocalization_is_trivial(self):
        for text in ("嗯", "嗯嗯嗯", "好", "哈哈", "好的", "晚安", "…", "？", "[CQ:face,id=1]"):
            self.assertTrue(fastpath.is_trivial(text, self.settings), text)

    def test_normal_sentence_not_trivial(self):
        for text in ("今天天气不错", "嗯这游戏真难", "晚安啦明天见"):
            self.assertFalse(fastpath.is_trivial(text, self.settings), text)

    def test_force_keyword_routes_to_classifier(self):
        self.assertTrue(fastpath.has_force_keyword("帮我查一下", self.settings))
        self.assertEqual(fastpath.route(_msgs("帮我查一下"), self.settings), fastpath.PATH_CLASSIFY)
        self.assertEqual(fastpath.route(_msgs("嗯"), self.settings), fastpath.PATH_TRIVIAL)

    def test_tool_result_detected_by_last_role(self):
        messages = [
            {"role": "user", "content": "搜一下"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "结果"},
        ]
        self.assertEqual(fastpath.route(messages, self.settings), fastpath.PATH_TOOL_RESULT)


class GateDecideTest(unittest.TestCase):
    def test_media_picked_and_others_stripped(self):
        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["media"], confidence=0.9)

        gate, metrics = _gate(classifier=fake_classifier)
        decision = gate.decide(_msgs("放首歌"), TOOLS, channel_id="qq", actor_id="master")
        self.assertTrue(decision.applied)
        self.assertEqual(decision.categories, ["media"])
        # 只保留选中类别 + 常驻 + 未归类工具，且维持上游原始顺序
        self.assertEqual(
            decision.tools_out,
            ["mock_tool", "media_demo", "send_message_to_user", "future_task"],
        )
        # 未知工具（配置里没登记）默认放行
        self.assertIn("mock_tool", decision.unknown_tools)
        self.assertNotIn("server_demo", decision.tools_out)

    def test_need_tools_false_strips_and_marks_drop(self):
        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=False, categories=[], confidence=0.95)

        gate, _ = _gate(classifier=fake_classifier)
        decision = gate.decide(_msgs("今天有点累"), TOOLS, channel_id="qq", actor_id="master")
        self.assertTrue(decision.applied)
        self.assertEqual(decision.tools_out, ["send_message_to_user", "future_task"])
        self.assertFalse(decision.drop_tool_fields)

    def test_no_always_tools_drops_fields(self):
        settings = json.loads(json.dumps(BASE_SETTINGS))
        settings["always_available"] = []

        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=False, categories=[], confidence=0.95)

        gate, _ = _gate(settings, classifier=fake_classifier)
        decision = gate.decide(_msgs("今天有点累"), TOOLS, channel_id="qq", actor_id="master")
        self.assertTrue(decision.applied)
        self.assertEqual(decision.tools_out, [])
        self.assertTrue(decision.drop_tool_fields)

        extra = gate.apply(decision, {"tools": TOOLS, "tool_choice": "auto", "parallel_tool_calls": True})
        self.assertNotIn("tools", extra)
        self.assertNotIn("tool_choice", extra)
        self.assertNotIn("parallel_tool_calls", extra)

    def test_tool_result_sticks_to_previous_categories(self):
        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["media"], confidence=0.9)

        gate, _ = _gate(classifier=fake_classifier)
        gate.decide(_msgs("放首歌"), TOOLS, channel_id="qq", actor_id="master")

        messages = [
            {"role": "user", "content": "放首歌"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "已播放"},
        ]
        decision = gate.decide(messages, TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, fastpath.PATH_TOOL_RESULT)
        self.assertEqual(decision.categories, ["media"])
        self.assertIn("media_demo", decision.tools_out)

    def test_trivial_carries_previous_categories(self):
        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["server"], confidence=0.8)

        gate, _ = _gate(classifier=fake_classifier)
        gate.decide(_msgs("看下服务器进程"), TOOLS, channel_id="qq", actor_id="master")

        decision = gate.decide(_msgs("嗯嗯"), TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, fastpath.PATH_TRIVIAL)
        self.assertEqual(decision.categories, ["server"])
        self.assertIn("server_demo", decision.tools_out)

    def test_trivial_without_history_keeps_always_only(self):
        gate, _ = _gate()
        decision = gate.decide(_msgs("嗯"), TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, fastpath.PATH_TRIVIAL)
        self.assertEqual(decision.tools_out, ["send_message_to_user", "future_task"])

    def test_low_confidence_falls_back_to_previous(self):
        results = [
            ClassifierResult(ok=True, need_tools=True, categories=["server"], confidence=0.9),
            ClassifierResult(ok=True, need_tools=True, categories=["media"], confidence=0.1),
        ]

        def fake_classifier(*a, **k):
            return results.pop(0) if results else ClassifierResult(ok=True, need_tools=True,
                                                                   categories=["media"], confidence=0.1)

        gate, _ = _gate(classifier=fake_classifier)
        gate.decide(_msgs("看下服务器进程"), TOOLS, channel_id="qq", actor_id="master")
        decision = gate.decide(_msgs("再看看另一个东西"), TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.categories, ["server"])
        self.assertIn("沿用上轮类别", decision.reason)

    def test_classifier_error_falls_back_to_all(self):
        def boom(*a, **k):
            raise RuntimeError("deepseek down")

        gate, _ = _gate(classifier=boom)
        decision = gate.decide(_msgs("放首歌"), TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, "fallback_all")
        self.assertTrue(decision.applied)
        # 全量放行必须包含未归类工具
        self.assertEqual(set(decision.tools_out), {t["function"]["name"] for t in TOOLS})

    def test_choice_fields_stripped_after_trim(self):
        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["media"], confidence=0.9)

        gate, _ = _gate(classifier=fake_classifier)
        decision = gate.decide(_msgs("放首歌"), TOOLS, channel_id="qq", actor_id="master")
        extra = gate.apply(decision, {"tools": TOOLS, "tool_choice": "auto", "parallel_tool_calls": True})
        self.assertIn("tools", extra)
        stripped = gate.strip_choice_fields(extra)
        self.assertNotIn("tool_choice", stripped)
        self.assertNotIn("parallel_tool_calls", stripped)

    def test_exempt_client_passes_through(self):
        settings = json.loads(json.dumps(BASE_SETTINGS))
        settings["gateway"]["exempt_clients"] = ["dev"]
        gate, _ = _gate(settings)
        decision = gate.decide(_msgs("放首歌"), TOOLS, client_id="dev")
        self.assertEqual(decision.path, "exempt")
        self.assertFalse(decision.applied)
        self.assertEqual(gate.apply(decision, {"tools": TOOLS}), {"tools": TOOLS})

    def test_no_tools_skips_gate(self):
        gate, metrics = _gate()
        decision = gate.decide(_msgs("你好"), [], channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, "no_tools")
        self.assertFalse(decision.applied)
        self.assertFalse(decision.drop_tool_fields)

    def test_disabled_config_passes_through(self):
        settings = json.loads(json.dumps(BASE_SETTINGS))
        settings["gateway"]["enabled"] = False
        gate, _ = _gate(settings)
        decision = gate.decide(_msgs("放首歌"), TOOLS, channel_id="qq", actor_id="master")
        self.assertEqual(decision.path, "disabled")
        self.assertFalse(decision.applied)

    def test_max_tools_cap_keeps_always_first(self):
        settings = json.loads(json.dumps(BASE_SETTINGS))
        settings["gateway"]["max_tools"] = 3

        def fake_classifier(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["media", "server"], confidence=0.9)

        gate, _ = _gate(settings, classifier=fake_classifier)
        decision = gate.decide(_msgs("放个歌顺便看下服务器"), TOOLS, channel_id="qq", actor_id="master")
        self.assertLessEqual(len(decision.tools_out), 3)
        self.assertEqual(decision.tools_out[0], "send_message_to_user")


class ClassifierParseTest(unittest.TestCase):
    def test_parse_plain_json(self):
        result = parse_output(
            '{"need_tools":true,"categories":["media"],"confidence":0.8,"reason":"想听歌"}',
            BASE_SETTINGS["categories"],
        )
        self.assertTrue(result.ok)
        self.assertTrue(result.need_tools)
        self.assertEqual(result.categories, ["media"])

    def test_parse_fenced_json_and_invalid_category(self):
        result = parse_output(
            '```json\n{"need_tools":true,"categories":["media","hack"],"confidence":"0.7"}\n```',
            BASE_SETTINGS["categories"],
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.categories, ["media"])

    def test_parse_none_falls_back(self):
        result = parse_output("随便聊两句吧", BASE_SETTINGS["categories"])
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "no_json")

    def test_real_classify_uses_mocked_deepseek(self):
        from agent_core.tool_shelves import classifier as cls_mod
        from agent_core.compute_providers import compute_manager

        fake = {
            "choices": [{"message": {"content": '{"need_tools":true,"categories":["media"],"confidence":0.88}'}}],
            "usage": {"prompt_tokens": 210, "completion_tokens": 18},
        }
        with mock.patch.object(compute_manager, "_candidate_providers",
                               return_value=[{"name": "DeepSeek", "model": "deepseek-chat"}]):
            with mock.patch.object(compute_manager, "_execute_request", return_value=fake):
                result = cls_mod.classify(
                    "放首歌", ["用户：好累", "助手：那就躺着"],
                    BASE_SETTINGS["categories"], BASE_SETTINGS,
                )
        self.assertTrue(result.ok)
        self.assertEqual(result.categories, ["media"])
        self.assertEqual(result.prompt_tokens, 210)

    def test_real_classify_timeout_falls_back(self):
        from agent_core.tool_shelves import classifier as cls_mod
        from agent_core.compute_providers import compute_manager

        with mock.patch.object(compute_manager, "_candidate_providers",
                               return_value=[{"name": "DeepSeek", "model": "deepseek-chat"}]):
            with mock.patch.object(compute_manager, "_execute_request", side_effect=TimeoutError("timed out")):
                result = cls_mod.classify("放首歌", [], BASE_SETTINGS["categories"], BASE_SETTINGS)
        self.assertFalse(result.ok)
        self.assertIn("request_failed", result.error)


# ----------------------------------------------------------------------
# 端到端：本地 mock 上游 + 真起 console/server.py
# ----------------------------------------------------------------------
class _MockUpstream:
    """一个上游管两条路由：/v1（persona）与 /classifier（分类器）。"""

    def __init__(self):
        self.payloads = []
        self.classifier_payloads = []
        self.cleaner_payloads = []
        self.classifier_reply = '{"need_tools":false,"categories":[],"confidence":0.9,"reason":"闲聊"}'
        self.cleaner_reply = ""
        self.persona_reply = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                pass

            def _json(self, data):
                raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                if self.path.endswith("/classifier/chat/completions"):
                    # 同一条 mock 通道：分类器看输出格式；清洗器（P3）走自己的回复
                    body_user = ((body.get("messages") or [{}])[-1] or {}).get("content") or ""
                    if "原始输出" in str(body_user) or "清洗器" in str(body.get("messages") or ""):
                        outer.cleaner_payloads.append(body)
                        reply = outer.cleaner_reply or json.dumps({
                            "ok": True, "summary": "搜索到 3 条结果",
                            "points": [{"k": "结果数", "v": "3"}],
                            "sources": ["https://example.com/a"],
                            "error": None, "truncated": False, "dropped": "HTML 标签",
                        }, ensure_ascii=False)
                    else:
                        outer.classifier_payloads.append(body)
                        reply = outer.classifier_reply
                    return self._json({
                        "id": "cmpl-cls",
                        "object": "chat.completion",
                        "choices": [{"index": 0,
                                     "message": {"role": "assistant", "content": reply},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 210, "completion_tokens": 18},
                    })
                outer.payloads.append(body)
                tools = body.get("tools") or []
                if outer.persona_reply:
                    message = dict(outer.persona_reply)
                else:
                    content = f"echo_tools={bool(tools)};n={len(tools)}"
                    message = {"role": "assistant", "content": content}
                return self._json({
                    "id": "cmpl-main",
                    "object": "chat.completion",
                    "created": 1700000000,
                    "model": body.get("model", "mock"),
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 6},
                })

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reset(self):
        self.payloads.clear()
        self.classifier_payloads.clear()
        self.cleaner_payloads.clear()
        self.persona_reply = None

    def stop(self):
        self.server.shutdown()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(FIXTURE_DB.exists(), "缺少生产 memory.db 夹具")
class GatewayE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="anan_shelves_e2e_"))
        cls.db = cls.tmp / "memory.db"
        shutil.copy2(FIXTURE_DB, cls.db)
        cls.upstream = _MockUpstream()

        cls.compute_cfg = cls.tmp / "compute_providers.json"
        cls.compute_cfg.write_text(json.dumps({
            "active_slots": {"persona_slot": "mock", "logic_slot": "mock_cls"},
            "routing_policy": {"auto_dispatch_logic": False},
            "providers": {
                "mock": {
                    "name": "Mock Persona", "auth_type": "official_api",
                    "base_url": f"http://127.0.0.1:{cls.upstream.port}/v1",
                    "api_key": "k", "model": "mock-model", "temperature": 0.7,
                },
                "mock_cls": {
                    "name": "Mock DeepSeek", "auth_type": "official_api",
                    "base_url": f"http://127.0.0.1:{cls.upstream.port}/classifier",
                    "api_key": "k", "model": "deepseek-chat", "temperature": 0.0,
                },
            },
        }, ensure_ascii=False), encoding="utf-8")

        cls.clients_cfg = cls.tmp / "clients.json"
        cls.clients_cfg.write_text(json.dumps({
            "require_key": False,
            "clients": [
                {
                    "id": "astrbot", "name": "AstrBot", "keys": ["hub_sk_test"],
                    "channel": "qq_test", "default_actor": "master",
                    "allow_recall_memory": False, "allow_ingest_timeline": False,
                    "rate_limit_per_minute": 0,
                },
                {
                    "id": "admin_cls", "name": "Admin", "keys": ["hub_sk_admin"],
                    "channel": "qq_admin", "default_actor": "master",
                    "allow_recall_memory": False, "allow_ingest_timeline": False,
                    "rate_limit_per_minute": 0,
                    "tool_permissions": {"role": "admin"},
                },
            ],
        }, ensure_ascii=False), encoding="utf-8")

        cls.policy_cfg = cls.tmp / "access_policy.json"
        cls.policy_cfg.write_text(json.dumps({
            "guest_enabled": False, "guest_timeline_enabled": False, "guest_default_affection": 50,
        }), encoding="utf-8")

        # 基于项目现有配置改（不手搓，避免测试复刻配置时漏字段）
        shelves = None
        for candidate in (ROOT / "data" / "tool_shelves.json",
                          Path(r"D:\机器人_完整版\data\tool_shelves.json")):
            if candidate.exists():
                shelves = json.loads(candidate.read_text(encoding="utf-8-sig"))
                break
        if shelves is None:
            shelves = json.loads(json.dumps(BASE_SETTINGS))
        shelves.setdefault("gateway", {})["enabled"] = True
        shelves["gateway"]["log_decisions"] = False
        shelves["gateway"]["exempt_clients"] = []
        shelves["categories"] = {
            "media": {"label": "影音类", "brief": "放歌", "tools": ["media_demo"]},
            "search": {"label": "搜索类", "brief": "搜索", "tools": ["mock_tool", "you-search"]},
            "server": {"label": "服务器类", "brief": "系统", "tools": ["control_host_pc_via_antigravity"]},
        }
        shelves["protection"] = {
            "admin_clients": ["admin_cls"],
            "host_tools": ["control_host_pc_via_antigravity"],
            "deny_for_non_admin": True,
        }
        shelves["compression"] = {
            "enabled": True,
            "slot": "logic_slot",
            "timeout_ms": 8000,
            "max_tokens": 700,
            "max_raw_chars": 24000,
            "min_model_chars": 1200,
            "skip_tools": ["send_message_to_user", "future_task"],
            "profiles": {
                "you-search": {"mode": "model", "max_chars": 600, "strip_html": True},
                "you-balance": {"mode": "model", "max_chars": 300, "strip_html": False},
            },
        }
        cls.shelves_cfg = cls.tmp / "tool_shelves.json"
        cls.shelves_cfg.write_text(json.dumps(shelves, ensure_ascii=False), encoding="utf-8")
        cls.shelves_original = json.dumps(shelves, ensure_ascii=False)

        cls.port = _free_port()
        env = os.environ.copy()
        env.update({
            "COMPUTE_CONFIG_PATH": str(cls.compute_cfg),
            "HUB_CLIENTS_PATH": str(cls.clients_cfg),
            "HUB_ACCESS_POLICY_PATH": str(cls.policy_cfg),
            "MEMORY_HUB_DB_PATH": str(cls.db),
            "TOOL_SHELVES_CONFIG_PATH": str(cls.shelves_cfg),
            "TOOL_SHELVES_METRICS_DIR": str(cls.tmp / "metrics"),
            "TOOL_SHELVES_LOG_DIR": str(cls.tmp / "logs"),
            "HUB_AGGREGATOR_DISABLED": "1",
            "HUB_DISCOVERY_DISABLED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "console" / "server.py"), str(cls.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                out = cls.proc.stdout.read() if cls.proc.stdout else ""
                raise RuntimeError(f"server exited: {out}")
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("server did not start in time")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        cls.upstream.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _post(self, payload, key: str = "hub_sk_test", channel: str = "qq_test"):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json", "X-Channel": channel,
                     "Authorization": f"Bearer {key}",
                     "X-User-Id": "1000000001", "X-Platform": "qq"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_no_tools_means_fields_removed(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":false,"categories":[],"confidence":0.95,"reason":"闲聊"}'
        body = self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "今天有点累"}],
            "tools": TOOLS,
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        })
        self.assertEqual(len(self.upstream.payloads), 1)
        sent = self.upstream.payloads[0]
        # 判定不用工具 → 只剩常驻两个
        self.assertEqual(
            [t["function"]["name"] for t in sent.get("tools") or []],
            ["send_message_to_user", "future_task"],
        )
        self.assertNotIn("tool_choice", sent)          # 关键：不能留 "none"
        self.assertNotIn("parallel_tool_calls", sent)
        self.assertIn("echo_tools=True", body["choices"][0]["message"]["content"])

    def test_all_tools_empty_removes_tools_field(self):
        """连常驻工具都被裁掉时，tools 字段本身必须消失。"""
        cfg = json.loads(self.shelves_cfg.read_text(encoding="utf-8"))
        cfg["always_available"] = []
        self.shelves_cfg.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        try:
            self.upstream.reset()
            self.upstream.classifier_reply = '{"need_tools":false,"categories":[],"confidence":0.95,"reason":"闲聊"}'
            self._post({
                "model": "client-model",
                "messages": [{"role": "user", "content": "今天有点累"}],
                "tools": TOOLS,
                "tool_choice": "auto",
                "parallel_tool_calls": True,
            })
            sent = self.upstream.payloads[0]
            self.assertNotIn("tools", sent)
            self.assertNotIn("tool_choice", sent)
            self.assertNotIn("parallel_tool_calls", sent)
        finally:
            self.shelves_cfg.write_text(self.shelves_original, encoding="utf-8")

    def test_selected_category_only(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":true,"categories":["media"],"confidence":0.9,"reason":"想听歌"}'
        self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "放首歌吧"}],
            "tools": TOOLS,
        })
        sent = self.upstream.payloads[0]
        names = [t["function"]["name"] for t in sent.get("tools") or []]
        self.assertIn("media_demo", names)
        self.assertIn("send_message_to_user", names)
        self.assertIn("future_task", names)
        # 未归类工具默认放行；不在类别表里的不会被裁
        self.assertIn("server_demo", names)
        self.assertNotIn("mock_tool", names)

        # 分类器只收到「时间 + 最近三句 + 大类名与一句话简介」
        cls_payload = self.upstream.classifier_payloads[0]
        cls_text = json.dumps(cls_payload, ensure_ascii=False)
        self.assertIn("影音类", cls_text)
        self.assertNotIn("parameters", cls_text)
        self.assertLess(len(cls_text), 4000)

    def test_trivial_skips_classifier(self):
        self.upstream.reset()
        self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "嗯"}],
            "tools": TOOLS,
        })
        self.assertEqual(len(self.upstream.classifier_payloads), 0)
        sent = self.upstream.payloads[0]
        names = [t["function"]["name"] for t in sent.get("tools") or []]
        # 没有历史时只留常驻；未归类工具按 unknown_tool_policy=always 放行
        self.assertEqual(names, ["media_demo", "server_demo", "send_message_to_user", "future_task"])
        self.assertNotIn("tool_choice", sent)

    def test_unknown_tool_passes_through(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":true,"categories":["search"],"confidence":0.9,"reason":"搜"}'
        self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "搜一下 BV1"}],
            "tools": TOOLS,
        })
        names = [t["function"]["name"] for t in self.upstream.payloads[0].get("tools") or []]
        self.assertIn("mock_tool", names)

    # ---------------- P3 / 权限 端到端 ----------------
    def test_non_admin_client_host_tool_denied(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":true,"categories":["server"],"confidence":0.9,"reason":"系统"}'
        host_tool = {
            "type": "function",
            "function": {"name": "control_host_pc_via_antigravity",
                         "description": "宿主机", "parameters": {}},
        }
        self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "看下服务器进程"}],
            "tools": [host_tool] + TOOLS,
        }, key="hub_sk_test")  # astrbot 不是 admin_cls
        names = [t["function"]["name"] for t in self.upstream.payloads[0].get("tools") or []]
        self.assertNotIn("control_host_pc_via_antigravity", names)

    def test_admin_client_keeps_host_tool(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":true,"categories":["server"],"confidence":0.9,"reason":"系统"}'
        host_tool = {
            "type": "function",
            "function": {"name": "control_host_pc_via_antigravity",
                         "description": "宿主机", "parameters": {}},
        }
        self._post({
            "model": "client-model",
            "messages": [{"role": "user", "content": "看下服务器进程"}],
            "tools": [host_tool],
        }, key="hub_sk_admin", channel="qq_admin")
        names = [t["function"]["name"] for t in self.upstream.payloads[0].get("tools") or []]
        self.assertIn("control_host_pc_via_antigravity", names)

    def test_tool_result_compressed_before_main_model(self):
        self.upstream.reset()
        self.upstream.classifier_reply = '{"need_tools":true,"categories":["search"],"confidence":0.9,"reason":"搜"}'
        self.upstream.cleaner_reply = json.dumps({
            "ok": True, "summary": "找到 2 条相关结果",
            "points": [{"k": "标题", "v": "测试视频"}, {"k": "播放量", "v": "123450"}],
            "sources": ["https://example.com/a"], "error": None,
            "truncated": False, "dropped": "HTML 标签与重复片段",
        }, ensure_ascii=False)
        self.upstream.persona_reply = {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call_s1", "type": "function",
                            "function": {"name": "you-search", "arguments": '{"query":"BV1PuxM68Ej7"}'}}],
        }
        raw = "<html><body>" + "".join(
            f'<div class="r"><a href="https://example.com/a">结果 {i}</a> 播放量 123450 '
            f'<span style="color:#f00">热门</span></p>' for i in range(300)
        ) + "</body></html>"

        # 第 2 轮：把工具结果回灌给中枢，前端应当收到压缩后的内容
        self._post({
            "model": "client-model",
            "messages": [
                {"role": "user", "content": "搜一下 BV1PuxM68Ej7"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "call_s1", "type": "function",
                     "function": {"name": "you-search", "arguments": '{"query":"BV1PuxM68Ej7"}'}}]},
                {"role": "tool", "tool_call_id": "call_s1", "content": raw},
            ],
            "tools": TOOLS,
        })
        sent = self.upstream.payloads[0]
        tool_msgs = [m for m in sent.get("messages") or [] if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        content = tool_msgs[0]["content"]
        self.assertIn("找到 2 条相关结果", content)     # 模型摘要生效
        self.assertIn("https://example.com/a", content)  # URL 原样保留
        self.assertNotIn("<div", content)                # HTML 已去
        self.assertNotIn("color:#f00", content)
        self.assertLess(len(content), 800)
        self.assertEqual(len(self.upstream.cleaner_payloads), 1)


# ----------------------------------------------------------------------
# P3 结果压缩：代码粗筛 + 安全快通道 + 数字/URL 防篡改
# ----------------------------------------------------------------------
from agent_core.tool_shelves import cleaner as cl  # noqa: E402

COMP_CFG = {
    "enabled": True,
    "slot": "logic_slot",
    "timeout_ms": 8000,
    "max_tokens": 700,
    "max_raw_chars": 24000,
    "min_model_chars": 1200,
    "skip_tools": ["send_message_to_user", "future_task"],
    "profiles": {
        "you-search": {"mode": "model", "max_chars": 600, "strip_html": True},
        "bilibili_hub": {"mode": "model", "max_chars": 600, "strip_html": True},
        "control_host_pc_via_antigravity": {"mode": "model", "max_chars": 400, "strip_html": False},
    },
}


class CleanerCodeStageTest(unittest.TestCase):
    def test_strip_ansi(self):
        raw = "\x1b[31m错误\x1b[0m: 服务未启动"
        self.assertEqual(cl.strip_ansi(raw), "错误: 服务未启动")

    def test_strip_html_keeps_link_text(self):
        raw = '<p>标题<a href="https://example.com/a">链接</a></p><script>alert(1)</script>'
        out = cl.strip_html(raw)
        self.assertIn("标题", out)
        self.assertIn("链接 (https://example.com/a)", out)
        self.assertNotIn("alert", out)
        self.assertNotIn("<p>", out)

    def test_compact_dedupes_short_repeated_lines(self):
        raw = "第一条\n第一条\n第二条\n\n\n\n第三条"
        out = cl.compact(raw)
        self.assertEqual(out.count("第一条"), 1)
        self.assertIn("第三条", out)

    def test_head_tail_marks_truncation(self):
        out, cut = cl.head_tail("A" * 1000, 100)
        self.assertTrue(cut)
        self.assertIn("中间省略", out)
        self.assertLessEqual(len(out), 160)

    def test_guard_detects_fake_number_and_url(self):
        src = "播放量 12.4万 链接 https://b23.tv/abc BV1PuxM68Ej7"
        guards = cl.extract_guards(src)
        # 具体数值（带小数）必须原样；纯整数视为可推导计数，不拦
        self.assertEqual(cl.guard_violation("播放量 99.9万", guards, src), "数值 ['99.9']")
        self.assertEqual(cl.guard_violation("共 60 条", guards, src), "")
        self.assertIn("URL", cl.guard_violation("见 https://fake.com/x", guards, src))
        # 原文出现过的数字/同路径 URL 不算幻觉
        self.assertEqual(cl.guard_violation("播放量 12.4万 见 https://b23.tv/abc?spm=1", guards, src), "")
        self.assertEqual(cl.guard_violation("总价 $12.34 与 12.34", cl.extract_guards("总价 12.34"), "总价 12.34"), "")


class CleanerCompressTest(unittest.TestCase):
    def test_short_numeric_tool_never_calls_model(self):
        calls = []

        def complete(system, user, cfg):
            calls.append(user)
            return "{}", 1, 1, ""

        result = cl.compress_tool_result("you-balance", "余额 1234 美分", COMP_CFG, complete_once=complete)
        self.assertEqual(calls, [])
        self.assertFalse(result.used_model)
        self.assertIn("1234", result.text)

    def test_large_tool_goes_through_model_and_keeps_url(self):
        calls = []

        def complete(system, user, cfg):
            calls.append(user)
            return json.dumps({
                "ok": True, "summary": "找到 2 条",
                "points": [{"k": "标题", "v": "测试视频"}],
                "sources": ["https://example.com/a"], "error": None,
                "truncated": False, "dropped": "尾部无关内容",
            }, ensure_ascii=False), 300, 60, ""

        raw = "<html><body>" + "".join(
            f'<p>结果 {i} 标题 https://example.com/a 播放 1234</p>' for i in range(200)
        ) + "</body></html>"
        result = cl.compress_tool_result("you-search", raw, COMP_CFG, complete_once=complete)
        self.assertEqual(len(calls), 1)
        self.assertTrue(result.used_model)
        self.assertIn("找到 2 条", result.text)
        self.assertIn("https://example.com/a", result.text)
        self.assertNotIn("<p>", result.text)
        self.assertLess(result.clean_chars, result.raw_chars)

    def test_fake_number_rejected_falls_back_to_code(self):
        def complete(system, user, cfg):
            return json.dumps({"ok": True, "summary": "播放量 99.9万", "points": [],
                               "sources": [], "error": None, "truncated": False}, ensure_ascii=False), 300, 60, ""

        raw = "<p>" + "原视频播放量 12345 " * 300 + "</p>"
        result = cl.compress_tool_result("you-search", raw, COMP_CFG, complete_once=complete)
        self.assertFalse(result.used_model)
        self.assertIn("guard_violation", result.error)
        self.assertNotIn("99.9万", result.text)

    def test_model_error_falls_back_to_code(self):
        def complete(system, user, cfg):
            return "", 0, 0, "request_failed:URLError"

        raw = "<p>" + "很长的搜索结果 " * 500 + "</p>"
        result = cl.compress_tool_result("you-search", raw, COMP_CFG, complete_once=complete)
        self.assertFalse(result.used_model)
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.text), 700)

    def test_code_mode_skips_model(self):
        calls = []

        def complete(system, user, cfg):
            calls.append(user)
            return "{}", 1, 1, ""

        cfg = {"enabled": True, "profiles": {"you-search": {"mode": "code", "max_chars": 200}}}
        raw = "x" * 5000
        result = cl.compress_tool_result("you-search", raw, cfg, complete_once=complete)
        self.assertEqual(calls, [])
        self.assertTrue(result.truncated)

    def test_messages_replaced_by_tool_call_id(self):
        messages = [
            {"role": "user", "content": "搜一下"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "you-balance", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "余额 777 美分"},
        ]
        out, records = cl.compress_messages(messages, COMP_CFG, user_query="查余额")
        self.assertEqual(len(records), 1)
        self.assertIn("777", out[2]["content"])
        self.assertIs(messages[2]["content"], "余额 777 美分")  # 不改原对象

    def test_compression_disabled_is_noop(self):
        messages = [{"role": "tool", "tool_call_id": "x", "content": "raw"}]
        out, records = cl.compress_messages(messages, {"enabled": False})
        self.assertEqual(records, [])
        self.assertEqual(out, messages)


class ProtectionTest(unittest.TestCase):
    HOST_TOOL = {"type": "function", "function": {"name": "control_host_pc_via_antigravity",
                                                  "description": "宿主机", "parameters": {}}}

    def _settings(self):
        settings = json.loads(json.dumps(BASE_SETTINGS))
        settings["protection"] = {
            "admin_clients": ["astrbot"],
            "host_tools": ["control_host_pc_via_antigravity"],
            "deny_for_non_admin": True,
        }
        return settings

    def test_non_admin_client_denied_even_if_not_listed(self):
        gate, _ = _gate(self._settings())
        tools = [self.HOST_TOOL] + TOOLS
        decision = gate.decide(
            _msgs("查一下服务器"), tools,
            channel_id="c", actor_id="guest", client_id="nextchat",
            client_perms={"role": "guest"},
        )
        self.assertIn("control_host_pc_via_antigravity", decision.denied)
        self.assertNotIn("control_host_pc_via_antigravity", decision.tools_out)

    def test_role_admin_claim_still_denied_if_not_in_admin_list(self):
        """客户端自称 role=admin，但不在 admin_clients 名单里 → 依然拒绝。"""
        gate, _ = _gate(self._settings())
        decision = gate.decide(
            _msgs("查一下服务器"), [self.HOST_TOOL],
            channel_id="c", actor_id="x", client_id="nextchat",
            client_perms={"role": "admin"},
        )
        self.assertIn("control_host_pc_via_antigravity", decision.denied)

    def test_admin_client_keeps_host_tool(self):
        gate, _ = _gate(self._settings())

        def fake(*a, **k):
            return ClassifierResult(ok=True, need_tools=True, categories=["server"], confidence=0.9)

        gate._classifier = fake
        decision = gate.decide(
            _msgs("看下服务器进程"), [self.HOST_TOOL],
            channel_id="qq", actor_id="master", client_id="astrbot",
            client_perms={"role": "admin"},
        )
        self.assertEqual(decision.denied, [])
        self.assertIn("control_host_pc_via_antigravity", decision.tools_out)

    def test_deny_applies_even_when_gate_disabled(self):
        settings = self._settings()
        settings["gateway"]["enabled"] = False
        gate, _ = _gate(settings)
        decision = gate.decide(
            _msgs("查一下"), [self.HOST_TOOL, TOOLS[0]],
            channel_id="c", actor_id="g", client_id="cline", client_perms={"role": "guest"},
        )
        self.assertEqual(decision.path, "disabled")
        self.assertTrue(decision.applied)
        self.assertNotIn("control_host_pc_via_antigravity", decision.tools_out)

    def test_client_denied_helper(self):
        gate, _ = _gate(self._settings())
        denied = gate.client_denied([self.HOST_TOOL], "nextchat", {"role": "guest"})
        self.assertEqual(denied, {"control_host_pc_via_antigravity"})


class ToolCleanApiTest(unittest.TestCase):
    """P4 薄壳清洗接口：给外部薄壳 MCP / 执行器插件用。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="anan_clean_api_"))
        cls.db = cls.tmp / "memory.db"
        if FIXTURE_DB.exists():
            shutil.copy2(FIXTURE_DB, cls.db)

        cfg = Path(tempfile.mkdtemp(prefix="clean_api_cfg_"))
        (cfg / "compute.json").write_text(json.dumps({
            "active_slots": {"persona_slot": "mock", "logic_slot": "mock_cls"},
            "routing_policy": {"auto_dispatch_logic": False},
            "providers": {"mock": {"name": "M", "auth_type": "official_api",
                                   "base_url": "http://127.0.0.1:1/v1", "api_key": "k", "model": "m"},
                          "mock_cls": {"name": "C", "auth_type": "official_api",
                                       "base_url": "http://127.0.0.1:1/aux", "api_key": "k", "model": "d"}},
        }), encoding="utf-8")
        (cfg / "clients.json").write_text(json.dumps({
            "require_key": False,
            "clients": [{"id": "astrbot", "keys": ["hub_sk_test"], "channel": "qq",
                         "default_actor": "master", "allow_recall_memory": False,
                         "allow_ingest_timeline": False, "rate_limit_per_minute": 0}],
        }), encoding="utf-8")
        policy = cfg / "policy.json"
        policy.write_text(json.dumps({"guest_enabled": False}), encoding="utf-8")

        shelves = json.loads(json.dumps(BASE_SETTINGS))
        shelves["compression"] = {
            "enabled": True,
            "slot": "logic_slot",
            "timeout_ms": 8000,
            "max_raw_chars": 24000,
            "min_model_chars": 1200,
            "skip_tools": [],
            "profiles": {"you-search": {"mode": "model", "max_chars": 600}},
        }
        cls.shelves_cfg = cfg / "tool_shelves.json"
        cls.shelves_cfg.write_text(json.dumps(shelves, ensure_ascii=False), encoding="utf-8")

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = os.environ.copy()
        env.update({
            "COMPUTE_CONFIG_PATH": str(cfg / "compute.json"),
            "HUB_CLIENTS_PATH": str(cfg / "clients.json"),
            "HUB_ACCESS_POLICY_PATH": str(policy),
            "MEMORY_HUB_DB_PATH": str(cls.db),
            "TOOL_SHELVES_CONFIG_PATH": str(cls.shelves_cfg),
            "TOOL_SHELVES_METRICS_DIR": str(cfg / "metrics"),
            "TOOL_SHELVES_LOG_DIR": str(cfg / "logs"),
            "HUB_AGGREGATOR_DISABLED": "1",
            "HUB_DISCOVERY_DISABLED": "1",
            "PYTHONIOENCODING": "utf-8",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "console" / "server.py"), str(cls.port)],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        deadline = time.time() + 25
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError((cls.proc.stdout.read() if cls.proc.stdout else "")[:2000])
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("clean api server did not start")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
        except Exception:
            cls.proc.kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _post(self, payload, key: str = "hub_sk_test"):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/tools/clean",
            data=data, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_requires_tool(self):
        status, body = self._post({"raw": "x"})
        self.assertEqual(status, 400)
        self.assertFalse(body.get("ok"))

    def test_requires_raw(self):
        status, body = self._post({"tool": "you-search"})
        self.assertEqual(status, 400)

    def test_bad_key_rejected(self):
        status, body = self._post({"tool": "you-balance", "raw": "x"}, key="not-a-key")
        self.assertEqual(status, 401)
        self.assertEqual(body.get("error"), "invalid_key")

    def test_safe_short_passthrough(self):
        status, body = self._post({
            "tool": "you-balance", "args": {}, "raw": "余额：1234 美分", "user_query": "查余额",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("1234", body.get("text"))
        self.assertFalse((body.get("stats") or {}).get("model"))

    def test_large_result_falls_back_without_model(self):
        """上游不可达（mock 地址故意写死）→ 退化为代码粗筛，绝不 500。"""
        raw = "<p>" + "很长的搜索结果 " * 800 + "</p>"
        status, body = self._post({
            "tool": "you-search", "args": {"query": "x"}, "raw": raw, "user_query": "搜一下",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))
        self.assertLessEqual(len(body.get("text") or ""), 800)
        self.assertTrue(body.get("truncated"))
        self.assertTrue((body.get("stats") or {}).get("error"))


if __name__ == "__main__":
    unittest.main(verbosity=2)