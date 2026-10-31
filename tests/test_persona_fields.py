# -*- coding: utf-8 -*-
"""人格设定四段拆分 / 合并测试。"""

import unittest

from console.modules.persona_service import (
    compose_persona_text,
    split_persona_text,
)

OLD_STYLE = """# 【星野：核心系统设定集】

## 一、 人物定位与精神底色
- **身份与称谓**：星野。
- **好感度温度梯度**：面对好感度 80 以上的人会更亲近。

## 二、 语言质感与输出法则
- 自然口语。

## 三、 经典情境黄金对白
星野：“晚上好”

## 四、 称呼与群聊交互规范
- 直接称呼「你」。
"""


class PersonaFieldsTest(unittest.TestCase):
    def test_old_style_splits_into_four_fields(self):
        fields = split_persona_text(OLD_STYLE)
        self.assertEqual(fields["name"], "星野")
        self.assertIn("人物定位", fields["persona"])
        self.assertIn("自然口语", fields["persona"])
        self.assertIn("经典情境", fields["dialogue_examples"])
        self.assertIn("称呼与群聊", fields["other"])
        self.assertNotIn("好感度温度梯度", fields["persona"])

    def test_compose_and_split_roundtrip(self):
        fields = split_persona_text(OLD_STYLE)
        compose = compose_persona_text(fields)
        again = split_persona_text(compose)
        self.assertEqual(again["name"], fields["name"])
        self.assertEqual(again["persona"], fields["persona"])
        self.assertEqual(again["dialogue_examples"], fields["dialogue_examples"])
        self.assertEqual(again["other"], fields["other"])

    def test_new_structured_text_parses(self):
        text = """# 人设：凛

## 人格设定
冷静。

## 对话范例
凛：“嗯。”

## 其他
短句。
"""
        fields = split_persona_text(text)
        self.assertEqual(fields["name"], "凛")
        self.assertEqual(fields["persona"], "冷静。")
        self.assertEqual(fields["dialogue_examples"], "凛：“嗯。”")
        self.assertEqual(fields["other"], "短句。")

    def test_no_affection_section_remains(self):
        fields = split_persona_text(OLD_STYLE)
        composed = compose_persona_text(fields)
        self.assertNotIn("好感度温度梯度", composed)


if __name__ == "__main__":
    unittest.main()
