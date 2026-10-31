# -*- coding: utf-8 -*-
"""中枢自带的记忆检索「特殊接口」：指令解析、清洗、提示词挂载。"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

import server  # noqa: E402


class DirectiveTest(unittest.TestCase):
    def test_keywords_only(self):
        parsed = server._parse_memory_directive("[[记忆检索: 小鱼干 科技新闻]]")
        self.assertEqual(parsed["keywords"], "小鱼干 科技新闻")
        self.assertNotIn("start", parsed)

    def test_full_form(self):
        parsed = server._parse_memory_directive(
            "[[记忆检索: 小鱼干 | 2026-10-29 | 2026-10-31 | qq]]"
        )
        self.assertEqual(parsed["keywords"], "小鱼干")
        self.assertEqual(parsed["start"], "2026-10-29")
        self.assertEqual(parsed["end"], "2026-10-31")
        self.assertEqual(parsed["source"], "qq")

    def test_no_directive(self):
        self.assertEqual(server._parse_memory_directive("就是普通回复"), {})

    def test_half_width_colon(self):
        parsed = server._parse_memory_directive("[[记忆检索: 泡茶]]")
        self.assertEqual(parsed["keywords"], "泡茶")


class StripTest(unittest.TestCase):
    def test_strip_leading_directive(self):
        message = {"role": "assistant",
                   "content": "[[记忆检索: 小鱼干]]\n那天你确实说过。"}
        cleaned = server._strip_memory_directive(message)
        self.assertEqual(cleaned["content"], "那天你确实说过。")

    def test_keep_plain_content(self):
        message = {"role": "assistant", "content": "普通回复"}
        self.assertEqual(server._strip_memory_directive(message)["content"], "普通回复")

    def test_none_passthrough(self):
        self.assertIsNone(server._strip_memory_directive(None))


class StreamHeadTest(unittest.TestCase):
    def test_clear_for_normal_text(self):
        self.assertEqual(server._stream_head_state("在呢，今天想聊什么"), "clear")

    def test_wait_for_leading_space(self):
        self.assertEqual(server._stream_head_state("  "), "wait")

    def test_directive_when_closed(self):
        self.assertEqual(server._stream_head_state("[[记忆检索: 小鱼干]]"), "directive")

    def test_wait_before_closing(self):
        self.assertEqual(server._stream_head_state("[[记忆检索: 小鱼"), "wait")

    def test_delta_text_from_chunk(self):
        chunk = b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
        self.assertEqual(server._stream_delta_text(chunk), "hi")

    def test_delta_text_ignores_done(self):
        self.assertEqual(server._stream_delta_text(b"data: [DONE]\n\n"), "")


class PromptMountTest(unittest.TestCase):
    def test_enabled_by_default(self):
        os.environ.pop("HUB_MEMORY_TOOL", None)
        self.assertIn("记忆检索", server._memory_tool_prompt())

    def test_stream_never_mounted(self):
        os.environ.pop("HUB_MEMORY_TOOL", None)
        self.assertEqual(server._memory_tool_prompt(stream=True), "")

    def test_can_disable(self):
        os.environ["HUB_MEMORY_TOOL"] = "0"
        try:
            self.assertEqual(server._memory_tool_prompt(), "")
        finally:
            os.environ.pop("HUB_MEMORY_TOOL", None)


if __name__ == "__main__":
    unittest.main()
