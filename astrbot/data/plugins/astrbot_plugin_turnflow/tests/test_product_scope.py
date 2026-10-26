import ast
import json
import socket
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

from test_core import Event, PLUGIN, Plain, main


REMOVED_KEYS = {
    "enable_link_parsing", "enable_local_sample_learning", "local_sample_paths",
}
LEGACY_CONFIG = {
    "enable_link_parsing": True,
    "link_parser_proxy": "http://127.0.0.1:1",
    "link_parser_timeout": "invalid legacy value",
    "link_parser_total_timeout": "invalid legacy value",
    "link_parser_max_links": "invalid legacy value",
    "link_parser_disabled_platforms": [],
    "enable_local_sample_learning": True,
    "local_sample_paths": ["private-chat-history.txt", "private-chat-directory"],
}


def forbid_external_io():
    stack = ExitStack()
    for target in (
        "builtins.open", "pathlib.Path.open", "pathlib.Path.read_bytes",
        "pathlib.Path.read_text", "pathlib.Path.glob", "pathlib.Path.rglob",
        "os.scandir", "socket.create_connection",
    ):
        stack.enter_context(patch(target, side_effect=AssertionError(target)))
    stack.enter_context(patch.object(
        socket.socket, "connect", side_effect=AssertionError("network connect")
    ))
    return stack


class PredictorTests(unittest.TestCase):
    def test_fast_complete_excludes_open_endings(self):
        predictor = main.ContinuationPredictor()
        complete = (
            "\u8fd9\u662f\u4e00\u6bb5\u5df2\u7ecf\u5199\u5b8c\u7684\u8bf4\u660e\uff0c"
            "\u5176\u4e2d\u5305\u542b\u4e86\u5145\u8db3\u7684\u80cc\u666f\u4fe1\u606f\uff0c"
            "\u76f8\u5173\u6761\u4ef6\u90fd\u5217\u597d\u4e86"
        )
        self.assertTrue(predictor._looks_fast_complete(complete + "\u3002", False))
        for ending in (",", "\uff0c", "\u4ee5\u53ca", "\u6216\u8005"):
            with self.subTest(ending=ending):
                self.assertFalse(predictor._looks_fast_complete(complete + ending, False))

    def test_legacy_sample_configuration_never_opens_or_scans_files(self):
        with forbid_external_io():
            predictor = main.ContinuationPredictor(LEGACY_CONFIG)
            default = main.ContinuationPredictor()
            for text, image in (("hello", False), ("\u5148\u522b\u56de", False), ("", True)):
                self.assertEqual(predictor.delay(text, image), default.delay(text, image))
        self.assertFalse(hasattr(predictor, "_load_local_signals"))
        self.assertFalse(hasattr(predictor, "local_sample_paths"))

    def test_dynamic_debounce_still_uses_continuation_and_images(self):
        predictor = main.ContinuationPredictor()
        self.assertGreater(
            predictor.delay("\u5148\u522b\u56de"),
            predictor.delay("\u4eca\u5929\u600e\u4e48\u6837\uff1f"),
        )
        self.assertGreater(predictor.delay("", True), predictor.delay("", False))
        self.assertLessEqual(predictor.delay("\u5148\u522b\u56de", True, 10), predictor.max_delay)

    def test_disabled_dynamic_debounce_keeps_fixed_delay(self):
        predictor = main.ContinuationPredictor({
            **LEGACY_CONFIG, "enable_dynamic_debounce": False, "debounce_time": 2.5,
        })
        self.assertEqual(predictor.delay("\u5148\u522b\u56de", True, 10), 2.5)


class ProductScopeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context = types.SimpleNamespace(conversation_manager=types.SimpleNamespace(
            update_conversation=AsyncMock(),
            get_curr_conversation_id=AsyncMock(return_value=None),
        ))
        with forbid_external_io():
            self.plugin = main.ContinuousMessagePlugin(self.context, {
                **LEGACY_CONFIG,
                "enable_ordered_image_caption": False,
                "enable_dynamic_debounce": False,
                "debounce_time": 0.001,
                "history_rollback_delays": [],
            })

    async def asyncTearDown(self):
        await self.plugin.terminate()

    async def test_legacy_network_settings_do_not_create_adapter(self):
        self.assertFalse(hasattr(self.plugin, "link_parser"))
        self.assertFalse(hasattr(main, "LinkParserAdapter"))

    async def test_raw_url_passes_through_without_fetching(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        event = Event()
        event.message_obj.message = [Plain(url)]
        event.message_str = url
        with forbid_external_io():
            await self.plugin.handle_private_msg(event)
        self.assertIn(url, event.message_str)
        self.assertNotIn("[\u94fe\u63a5\u89e3\u6790]", event.message_str)
        session = self.plugin.turns.get_turn(event.get_extra(main.EXTRA_TURN_ID))
        self.assertEqual(session.all_images(), [])
        self.assertFalse(event.stopped)

    async def test_card_url_extraction_is_local_and_still_available(self):
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        event = Event()
        event.message_obj.message = [
            {"type": "json", "data": {"jumpurl": url}},
        ]
        event.message_str = ""
        with forbid_external_io():
            await self.plugin.handle_private_msg(event)
        self.assertIn(url, event.message_str)
        self.assertIn("[\u5361\u7247\u94fe\u63a5]", event.message_str)
        self.assertFalse(event.stopped)

    def test_removed_options_are_absent_from_schema(self):
        schema = json.loads((PLUGIN / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertFalse(REMOVED_KEYS.intersection(schema))
        self.assertFalse(any(key.startswith("link_parser_") for key in schema))
        for key in (
            "enable_qq_card_parsing", "enable_forward_analysis",
            "enable_ordered_image_caption", "enable_dynamic_debounce",
        ):
            self.assertIn(key, schema)

    def test_removed_modules_and_dependencies_are_absent(self):
        self.assertFalse((PLUGIN / "link_parser_adapter.py").exists())
        self.assertFalse(list((PLUGIN / "lite_link_parser").rglob("*.py")))
        requirements = (PLUGIN / "requirements.txt").read_text(encoding="utf-8")
        self.assertFalse([
            line for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ])

    def test_runtime_has_no_scraper_or_external_package_imports(self):
        forbidden = {
            "aiohttp", "msgspec", "bs4", "curl_cffi", "bilibili_api",
            "requests", "httpx", "link_parser_adapter", "lite_link_parser",
        }
        for path in PLUGIN.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imports.add((node.module or "").split(".")[0])
            with self.subTest(file=path.name):
                self.assertFalse(forbidden.intersection(imports))


if __name__ == "__main__":
    unittest.main()
