# -*- coding: utf-8 -*-
"""主 AI 按意图申请工具：只做选择与加载，不执行。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

from agent_core.tool_shelves import broker  # noqa: E402


def _tool(name, description=""):
    return {"type": "function", "function": {"name": name, "description": description}}


class BrokerTest(unittest.TestCase):
    def test_spec_is_a_tool(self):
        spec = broker.tool_spec()
        self.assertEqual(spec["function"]["name"], broker.TOOL_NAME)
        self.assertIn("intent", spec["function"]["parameters"]["properties"])

    def test_parse_request_clamps_limit(self):
        call = {"function": {"arguments": '{"intent":"画图","max_tools":99}'}}
        parsed = broker.parse_request(call)
        self.assertEqual(parsed["intent"], "画图")
        self.assertEqual(parsed["limit"], broker.MAX_LIMIT)

    def test_select_by_intent(self):
        tools = [
            _tool("generate_image", "生成图片、画图"),
            _tool("bilibili_hub", "查询 B 站评论和视频"),
            _tool("get_weather", "查询天气"),
        ]
        picked = broker.select_tools("帮我生成图片", tools, limit=2)
        self.assertEqual(picked[0]["function"]["name"], "generate_image")

    def test_exclude_visible_tools(self):
        tools = [_tool("generate_image", "生成图片"), _tool("get_weather", "天气")]
        picked = broker.select_tools("图片", tools, exclude=["generate_image"], limit=2)
        self.assertNotIn("generate_image", [item["function"]["name"] for item in picked])

    def test_result_payload_is_compact(self):
        payload = broker.result_payload([_tool("generate_image", "生成图片")])
        self.assertIn("generate_image", payload)
        self.assertIn("loaded", payload)


class BrokerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.handler = server.ConsoleHandler.__new__(server.ConsoleHandler)
        self.tool = _tool("generate_image", "生成图片")
        self.handler._select_dynamic_tools = lambda ctx, calls: [self.tool]
        self.calls = []

    def _reply(self, ctx, messages, extra):
        self.calls.append(extra)
        return {"content": "画好了", "tool_calls": []}, "stop", {"prompt_tokens": 2, "completion_tokens": 3}, ""

    def test_broker_reasks_with_selected_tools(self):
        self.handler._persona_request = self._reply
        ctx = {
            "dynamic_tools": True,
            "extra": {"tools": [broker.tool_spec()]},
        }
        message = {
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "function": {"name": broker.TOOL_NAME, "arguments": '{"intent":"画图"}'},
            }],
        }
        again, finish, usage = self.handler._maybe_expand_tools(
            ctx, [{"role": "system", "content": "x"}], message, "tool_calls",
            {"prompt_tokens": 1, "completion_tokens": 1},
        )
        self.assertEqual(again["content"], "画好了")
        self.assertEqual(finish, "stop")
        self.assertEqual(usage["prompt_tokens"], 3)
        names = [item["function"]["name"] for item in ctx["extra"]["tools"]]
        self.assertIn("generate_image", names)
        # 保留申请入口，允许同一轮继续申请；次数由 dynamic_rounds 控制
        self.assertIn(broker.TOOL_NAME, names)

    def test_dynamic_rounds_allow_second_request(self):
        self.handler._dynamic_round_limit = lambda: 2
        self.handler._note_dynamic_request = lambda *args, **kwargs: None
        self.handler._select_dynamic_tools = lambda ctx, calls: [self.tool]
        broker_call = {
            "id": "call_1",
            "function": {"name": broker.TOOL_NAME, "arguments": '{"intent":"画图"}'},
        }
        actual_call = {
            "id": "call_2",
            "function": {"name": "generate_image", "arguments": "{}"},
        }
        replies = [
            ({"content": "", "tool_calls": [dict(broker_call)]}, "tool_calls", {}, ""),
            ({"content": "", "tool_calls": [dict(actual_call)]}, "tool_calls", {}, ""),
        ]
        self.handler._persona_request = lambda ctx, messages, extra: replies.pop(0)
        ctx = {"dynamic_tools": True, "extra": {"tools": [broker.tool_spec()]}, "dynamic_rounds": 0}
        message = {"content": "", "tool_calls": [dict(broker_call)]}
        message, _, _ = self.handler._maybe_expand_tools(ctx, [], message, "tool_calls", {})
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "generate_image")
        self.assertEqual(ctx["dynamic_rounds"], 2)

    def test_dynamic_request_is_audited(self):
        call = {"function": {"name": broker.TOOL_NAME, "arguments": '{"intent":"画图"}'}}
        with patch.object(server.audit_log, "record") as audit, patch(
            "agent_core.tool_shelves.metrics.metrics.record_dynamic"
        ) as metric:
            self.handler._note_dynamic_request({"actor_id": "master", "client_id": "astrbot"}, [call], [self.tool], 12)
        self.assertEqual(audit.call_args.kwargs["loaded"], ["generate_image"])
        metric.assert_called_once()

    def test_real_tool_call_wins_over_broker(self):
        ctx = {"dynamic_tools": True, "extra": {"tools": [broker.tool_spec()]}}
        message = {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "function": {"name": broker.TOOL_NAME, "arguments": '{"intent":"画图"}'}},
                {"id": "call_2", "function": {"name": "generate_image", "arguments": "{}"}},
            ],
        }
        again, _, _ = self.handler._maybe_expand_tools(
            ctx, [], message, "tool_calls", {},
        )
        names = [item["function"]["name"] for item in again["tool_calls"]]
        self.assertEqual(names, ["generate_image"])


if __name__ == "__main__":
    unittest.main()
