import json
import os
import sys
import types
import unittest
from pathlib import Path


PLUGIN_DIR = Path(os.environ.get(
    "TURNFLOW_TEST_PLUGIN",
    Path(__file__).resolve().parents[1],
))
sys.path.insert(0, str(PLUGIN_DIR))


logger = types.SimpleNamespace(
    warning=lambda *args, **kwargs: None,
    debug=lambda *args, **kwargs: None,
    error=lambda *args, **kwargs: None,
)
astrbot = types.ModuleType("astrbot")
astrbot_api = types.ModuleType("astrbot.api")
astrbot_api.logger = logger
astrbot_api_event = types.ModuleType("astrbot.api.event")
astrbot_api_event.AstrMessageEvent = object
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", astrbot_api)
sys.modules.setdefault("astrbot.api.event", astrbot_api_event)

from message_parser import MessageParser
from turn_state import TurnSession, TurnStateStore


class Plain:
    def __init__(self, text):
        self.text = text


class Reply:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, url=None, file=None):
        self.url = url
        self.file = file


class Json:
    def __init__(self, data):
        self.data = data


class Message:
    def __init__(self, *components):
        self.message = list(components)


class MessagePartsTests(unittest.TestCase):
    def setUp(self):
        self.parser = MessageParser(image_component=Image)

    def test_parts_keep_order_ignore_reply_and_fallback_image_file(self):
        message = Message(
            Plain("a"),
            Image(url="https://one.example/a.png"),
            Reply("quoted"),
            Plain("b"),
            Image(file="local.png"),
        )
        self.assertEqual(
            self.parser.parse_message_parts(message),
            [
                {"type": "text", "text": "a"},
                {"type": "image", "url": "https://one.example/a.png"},
                {"type": "text", "text": "b"},
                {"type": "image", "url": "local.png"},
            ],
        )
        self.assertEqual(
            self.parser.parse_message(message),
            ("ab", True, ["https://one.example/a.png", "local.png"]),
        )

    def test_card_text_keeps_legacy_prompt_and_is_a_part(self):
        message = Message(
            Plain("before"),
            Json(json.dumps({"jumpurl": "https://www.bilibili.com/video/BV1xx411c7mD"})),
            Plain("after"),
        )
        parts = self.parser.parse_message_parts(message)
        self.assertEqual(parts[0:2], [
            {"type": "text", "text": "before"},
            {"type": "text", "text": "after"},
        ])
        self.assertEqual(
            parts[2],
            {"type": "text", "text": "\n[卡片链接] https://www.bilibili.com/video/BV1xx411c7mD"},
        )
        self.assertEqual(
            self.parser.parse_message(message)[0],
            "beforeafter\n[卡片链接] https://www.bilibili.com/video/BV1xx411c7mD",
        )


class TurnStateTests(unittest.TestCase):
    def test_parts_are_compatible_and_deep_copied(self):
        session = TurnSession(uid="u")
        parts = [{"type": "text", "text": "hello", "meta": {"x": 1}}]
        item = session.add_item("m", "hello", [], parts)
        serialized = item.to_dict()
        serialized["parts"][0]["meta"]["x"] = 2
        self.assertEqual(item.parts[0]["meta"]["x"], 1)
        clone = item.clone_seed(2)
        clone.parts[0]["meta"]["x"] = 3
        self.assertEqual(item.parts[0]["meta"]["x"], 1)

        legacy = session.add_item("m2", "text", ["img"])
        self.assertEqual(legacy.parts, [
            {"type": "text", "text": "text"},
            {"type": "image", "url": "img"},
        ])
        self.assertIsInstance(session.collection_started_at, float)
        self.assertEqual(session.send_depth, 0)

    def test_compare_delete_and_pending_identity_guard(self):
        store = TurnStateStore()
        first = store.create_session("u")
        replacement = TurnSession(uid="u")
        store.sessions["u"] = replacement
        self.assertIsNone(store.pop_session("u", expected=first))
        self.assertIs(store.get_session("u"), replacement)

        store.mark_pending(first)
        self.assertIs(store.get_session("u"), replacement)
        self.assertIs(store.pending_turns[first.turn_id], first)

        store.mark_pending(replacement)
        self.assertIsNone(store.get_session("u"))
        self.assertIs(store.pending_turns[replacement.turn_id], replacement)


if __name__ == "__main__":
    unittest.main()
