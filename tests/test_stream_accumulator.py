# -*- coding: utf-8 -*-
"""流式累加器回归：工具名要从 SSE 增量里还原出来。

背景：以前累加器只记一个 bool，`_gateway_record` 拿它当列表去迭代，
流式返回 tool_calls 时直接 TypeError（账本与时间轴都写不进去）。
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from server import _StreamAccumulator, _join_tool_names  # noqa: E402


def _chunk(payload: dict) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


class StreamAccumulatorTest(unittest.TestCase):
    def test_content_usage_and_finish_reason(self):
        acc = _StreamAccumulator()
        acc.feed(_chunk({"choices": [{"delta": {"content": "你"}, "finish_reason": None}]}))
        acc.feed(_chunk({"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]}))
        acc.feed(_chunk({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}}))
        self.assertEqual("".join(acc.content), "你好")
        self.assertEqual(acc.finish_reason, "stop")
        self.assertEqual(acc.usage["prompt_tokens"], 7)

    def test_tool_call_names_are_collected(self):
        acc = _StreamAccumulator()
        acc.feed(_chunk({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "mock_tool", "arguments": "{}"}},
        ]}, "finish_reason": None}]}))
        acc.feed(_chunk({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "mock_tool", "arguments": "{\"x\":1}"}},
            {"index": 1, "function": {"name": "other_tool", "arguments": "{}"}},
        ]}, "finish_reason": "tool_calls"}]}))
        self.assertTrue(acc.tool_calls)
        self.assertEqual(acc.tool_call_names, ["mock_tool", "other_tool"])

    def test_done_marker(self):
        acc = _StreamAccumulator()
        acc.feed(b"data: [DONE]\n\n")
        self.assertTrue(acc.saw_done)

    def test_partial_line_waits_for_rest(self):
        acc = _StreamAccumulator()
        payload = _chunk({"choices": [{"delta": {"content": "拼"}, "finish_reason": None}]})
        acc.feed(payload[:12])
        self.assertEqual(acc.content, [])
        acc.feed(payload[12:])
        self.assertEqual(acc.content, ["拼"])

    def test_join_tool_names_dedupes_and_tolerates_bool(self):
        calls = [
            {"function": {"name": "a"}},
            {"function": {"name": "b"}},
            {"function": {"name": "a"}},
            {"function": {}},
        ]
        self.assertEqual(_join_tool_names(calls), "a,b")
        self.assertEqual(_join_tool_names(True), "")
        self.assertEqual(_join_tool_names(None), "")


if __name__ == "__main__":
    unittest.main()
