# -*- coding: utf-8 -*-
"""适配器生成器：提示词、静态校验、生成物默认关闭。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "console"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import generate_plugin_adapter as gen  # noqa: E402
from adapters import AdapterRegistry  # noqa: E402


class GeneratorTest(unittest.TestCase):
    def test_infer_plugin_id_from_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = Path(tmp)
            (plugin / "metadata.yaml").write_text("name: demo_plugin\n", encoding="utf-8")
            self.assertEqual(gen.infer_plugin_id(plugin), "demo_plugin")

    def test_prompt_contains_contract_and_plugin(self):
        ctx = {"plugin_dir": "/tmp/demo", "files": {"metadata.yaml": "name: demo\n"}}
        prompt = gen.build_prompt("demo", ctx, "uid: 10001")
        self.assertIn("demo", prompt)
        self.assertIn("ENABLED = False", prompt)
        self.assertIn("test_adapter_demo.py", prompt)

    def test_validate_rejects_dangerous_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(
                "import subprocess\nclass Adapter: pass\nENABLED = False\n",
                encoding="utf-8",
            )
            errors = gen.validate_adapter(path, "demo")
        self.assertTrue(any("禁止导入" in item for item in errors))

    def test_validate_accepts_minimal_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "demo.py"
            path.write_text(
                'from adapters.base import Adapter as BaseAdapter\n'
                'ENABLED = False\n'
                'class Adapter(BaseAdapter):\n'
                '    plugin_id = "demo"\n',
                encoding="utf-8",
            )
            self.assertEqual(gen.validate_adapter(path, "demo"), [])


class GeneratedLoadingTest(unittest.TestCase):
    def test_disabled_generated_adapter_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.py").write_text(
                'from adapters.base import Adapter as BaseAdapter\n'
                'ENABLED = False\n'
                'class Adapter(BaseAdapter):\n'
                '    plugin_id = "demo"\n',
                encoding="utf-8",
            )
            with patch("adapters.registry._GENERATED_DIR", root):
                registry = AdapterRegistry()
                self.assertEqual(registry.adapters(), [])

    def test_enabled_generated_adapter_is_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "demo.py").write_text(
                'from adapters.base import Adapter as BaseAdapter\n'
                'ENABLED = True\n'
                'class Adapter(BaseAdapter):\n'
                '    plugin_id = "demo"\n'
                '    def detect(self, request): return 1.0\n',
                encoding="utf-8",
            )
            with patch("adapters.registry._GENERATED_DIR", root):
                registry = AdapterRegistry()
                names = [adapter.plugin_id for adapter in registry.adapters()]
        self.assertIn("demo", names)


if __name__ == "__main__":
    unittest.main()
