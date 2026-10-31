# -*- coding: utf-8 -*-
"""多模态内容清洗：图片只留短语，二进制不进时间轴/记忆库。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))

from server import _scrub_binary, _scrub_messages, _summarize_parts  # noqa: E402

DATA_URL = "data:image/png;base64," + "A" * 500


class ScrubTest(unittest.TestCase):
    def test_plain_text_untouched(self):
        self.assertEqual(_scrub_binary("今天天气不错"), "今天天气不错")

    def test_data_url_replaced(self):
        out = _scrub_binary(f"看这张图 {DATA_URL} 怎么样")
        self.assertNotIn("base64", out)
        self.assertNotIn("AAAA", out)
        self.assertIn("看这张图", out)
        self.assertIn("图片数据已省略", out)

    def test_quoted_payload_with_spaces(self):
        out = _scrub_binary(DATA_URL.replace("A" * 500, " ".join(["AAAA"] * 50)))
        self.assertNotIn("base64", out)

    def test_none_and_empty(self):
        self.assertEqual(_scrub_binary(None), "")
        self.assertEqual(_scrub_binary(""), "")


class PartsTest(unittest.TestCase):
    def test_text_only(self):
        self.assertEqual(
            _summarize_parts([{"type": "text", "text": "你好"}]),
            "你好",
        )

    def test_image_counted_not_embedded(self):
        out = _summarize_parts([
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ])
        self.assertIn("看图", out)
        self.assertIn("[图片]×1", out)
        self.assertNotIn("base64", out)

    def test_two_images(self):
        out = _summarize_parts([
            {"type": "image_url", "image_url": {"url": DATA_URL}},
            {"type": "image_url", "image_url": {"url": DATA_URL}},
        ])
        self.assertEqual(out, "[图片]×2")


class MessagesTest(unittest.TestCase):
    def test_messages_scrubbed(self):
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": DATA_URL}}]},
            {"role": "assistant", "content": "收到"},
        ]
        cleaned = _scrub_messages(messages)
        self.assertEqual(cleaned[0]["content"], "你是助手")
        self.assertIn("[图片]", cleaned[1]["content"])
        self.assertNotIn("base64", str(cleaned))
        self.assertEqual(messages[1]["content"][0]["type"], "image_url")  # 不改原对象

    def test_non_list_passthrough(self):
        self.assertEqual(_scrub_messages("x"), "x")


if __name__ == "__main__":
    unittest.main()
