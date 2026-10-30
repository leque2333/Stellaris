# -*- coding: utf-8 -*-
"""宿主适配器：无 AstrBot 时不产生硬路径"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from console.adapters import AstrBotAdapter  # noqa: E402


class HostAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_host_"))
        (self.tmp / "cmd_config.json").write_text("{}", encoding="utf-8")
        (self.tmp / "plugins" / "demo").mkdir(parents=True)
        (self.tmp / "mcp.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_env_data_dir(self):
        with patch.dict(os.environ, {"ASTRBOT_DATA_DIR": str(self.tmp)}):
            adapter = AstrBotAdapter()
        self.assertTrue(adapter.available)
        self.assertEqual(Path(adapter.cmd_config_path()), self.tmp / "cmd_config.json")
        self.assertEqual(Path(adapter.plugins_dir()), self.tmp / "plugins")
        self.assertEqual(Path(adapter.mcp_path()), self.tmp / "mcp.json")

    def test_no_env_does_not_crash(self):
        with patch.dict(os.environ, {"ASTRBOT_DATA_DIR": str(self.tmp / "missing")}):
            adapter = AstrBotAdapter()
        self.assertFalse(adapter.available)
        self.assertEqual(adapter.cmd_config_path(), "")
        self.assertEqual(adapter.plugins_dir(), "")

    def test_resolve_hub_db_env(self):
        from console.modules.common import resolve_hub_db_path

        with patch.dict(os.environ, {"MEMORY_HUB_DB_PATH": str(self.tmp / "x.db")}):
            self.assertEqual(resolve_hub_db_path(), str(self.tmp / "x.db"))


if __name__ == "__main__":
    unittest.main()
