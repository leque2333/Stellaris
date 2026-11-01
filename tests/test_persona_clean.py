# -*- coding: utf-8 -*-
"""输出清洗：括号/句号规则生效，但不许动代码块与 Markdown 链接。"""

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "console"))

from modules import persona_service  # noqa: E402

RULES = {
    "speech_regex_rules": [
        {"id": "zh", "pattern": "（[^）]*）", "replacement": "", "is_active": True},
        {"id": "dot", "pattern": r"。+$", "replacement": "", "is_active": True},
    ]
}


class CleanPersonaReplyTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="clean_"))
        self.rules = self.dir / "persona_rules.json"
        self.rules.write_text(json.dumps(RULES, ensure_ascii=False), encoding="utf-8")
        self.patch = mock.patch.object(persona_service, "PERSONA_RULES_PATH", str(self.rules))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_brackets_and_trailing_period_removed(self):
        self.assertEqual(persona_service.clean_persona_reply("好哦（笑着伸懒腰）。"), "好哦")

    def test_code_block_is_shielded(self):
        text = "看这段：\n```python\nprint('（不是动作）。')\n```\n就这样。"
        cleaned = persona_service.clean_persona_reply(text)
        self.assertIn("print('（不是动作）。')", cleaned)
        self.assertNotIn("就这样。", cleaned)  # 真正的句末句号仍然去掉
        self.assertTrue(cleaned.endswith("就这样"))

    def test_inline_code_and_link_are_shielded(self):
        text = "调用 `foo(bar)。` 然后看 [文档](https://example.com/a。b)"
        cleaned = persona_service.clean_persona_reply(text)
        self.assertIn("`foo(bar)。`", cleaned)
        self.assertIn("[文档](https://example.com/a。b)", cleaned)

    def test_empty_input(self):
        self.assertEqual(persona_service.clean_persona_reply(""), "")


if __name__ == "__main__":
    unittest.main()
