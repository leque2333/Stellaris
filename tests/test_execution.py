# -*- coding: utf-8 -*-
"""执行 AI 工作区：读写边界、受保护文件、命令白名单"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.execution import ExecutionAgent, WorkspaceTools  # noqa: E402


class _FakeLLM:
    api_key = "fake"

    async def generate_with_tools(self, **kwargs):
        return "执行完成", []


class WorkspaceToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_ws_"))
        self.tools = WorkspaceTools(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_and_read(self):
        result = self.tools.write_file("src/demo.py", "print(1)\n")
        self.assertIn("已写入", result)
        self.assertIn("print(1)", self.tools.read_file(str(self.tmp / "src" / "demo.py")))

    def test_protected_file_denied(self):
        self.assertIn("受保护", self.tools.write_file("data/memory.db", "x"))

    def test_path_escape_denied(self):
        self.assertIn("不在工作区", self.tools.write_file("../outside.txt", "x"))

    def test_command_allowlist(self):
        allowed = self.tools.run_command("python --version")
        self.assertIn("exit=0", allowed)
        denied = self.tools.run_command("curl http://example.com")
        self.assertIn("不在白名单", denied)
        operator = self.tools.run_command("python --version && echo x")
        self.assertIn("shell 操作符", operator)


class ExecutionAgentTest(unittest.TestCase):
    def test_execute_mode_returns_text(self):
        tmp = Path(tempfile.mkdtemp(prefix="anan_exec_"))
        try:
            agent = ExecutionAgent(workspace=tmp, llm=_FakeLLM())
            result = asyncio.run(agent.run("做点事", mode="execute"))
            self.assertTrue(result.ok)
            self.assertEqual(result.text, "执行完成")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
