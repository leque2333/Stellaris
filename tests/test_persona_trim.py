# -*- coding: utf-8 -*-
"""人格卡裁剪：对话范例保留，其余压到限额。"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core import persona_trim  # noqa: E402

SAMPLE = """# 人设：测试
## 一、人物定位
""" + "定位说明。" * 200 + """
## 二、语言质感
""" + "语言法则。" * 200 + """
## 对话范例
""" + "范例句。" * 200 + """
## 三、称呼规范
""" + "称呼说明。" * 100


class TrimTest(unittest.TestCase):
    def test_sections_split(self):
        sections = persona_trim.split_sections(SAMPLE)
        titles = [title for title, _ in sections]
        self.assertIn("对话范例", titles)

    def test_dialogue_kept_whole(self):
        out = persona_trim.trim_persona(SAMPLE, limit=200)
        self.assertIn("范例句。" * 50, out)

    def test_others_trimmed(self):
        out = persona_trim.trim_persona(SAMPLE, limit=200)
        self.assertLess(len(out), len(SAMPLE) // 2)

    def test_short_prompt_untouched(self):
        short = "## 一、设定\n很短。"
        self.assertEqual(persona_trim.compact_prompt(short, limit=1000), short)

    def test_zero_limit_disables(self):
        self.assertEqual(persona_trim.trim_persona(SAMPLE, limit=0), SAMPLE)
