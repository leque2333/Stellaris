# -*- coding: utf-8 -*-
"""人格名称必须来自可替换的人设卡，而不是项目代码。"""

import os
import tempfile
import unittest
from pathlib import Path

import agent_core.persona_identity as pi


class PersonaIdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="stellaris_persona_"))
        self.persona = self.tmp / "persona.md"
        self._old_candidates = pi.PERSONA_CANDIDATES
        pi.PERSONA_CANDIDATES = [self.persona]
        pi.clear_persona_cache()
        self._old_bot_name = os.environ.pop("BOT_NAME", None)

    def tearDown(self):
        pi.PERSONA_CANDIDATES = self._old_candidates
        pi.clear_persona_cache()
        if self._old_bot_name is not None:
            os.environ["BOT_NAME"] = self._old_bot_name
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replacing_persona_replaces_name(self):
        self.persona.write_text("# 人设：星野\n\n你是星野。", encoding="utf-8")
        self.assertEqual(pi.get_persona_name(), "星野")

        self.persona.write_text("# 【凛：雪国设定】\n\n你是凛。", encoding="utf-8")
        pi.clear_persona_cache()
        self.assertEqual(pi.get_persona_name(), "凛")

    def test_yaml_name_wins(self):
        self.persona.write_text("---\nname: 阿澄\n---\n# 随便的标题\n", encoding="utf-8")
        self.assertEqual(pi.get_persona_name(), "阿澄")

    def test_missing_persona_uses_generic_default(self):
        self.assertEqual(pi.get_persona_name(), pi.DEFAULT_PERSONA_NAME)

    def test_env_override(self):
        os.environ["BOT_NAME"] = "临时角色"
        try:
            self.assertEqual(pi.get_persona_name(), "临时角色")
        finally:
            os.environ.pop("BOT_NAME", None)

    def test_parse_ignores_generic_heading(self):
        self.assertEqual(pi._parse_persona_name("# 人设：助手"), "助手")
        self.assertEqual(pi._parse_persona_name("没有标题的人设正文"), pi.DEFAULT_PERSONA_NAME)


if __name__ == "__main__":
    unittest.main()
