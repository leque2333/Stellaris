# -*- coding: utf-8 -*-
"""MCP stdio 客户端测试（本地 mock server）"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.mcp import MCPManager  # noqa: E402

SERVER = r'''
import json, sys
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    if "id" not in msg:
        continue
    method = msg.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "mock"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "echo", "description": "echo", "inputSchema": {"type": "object"}}]}
    elif method == "tools/call":
        result = {"content": [{"type": "text", "text": json.dumps(msg.get("params", {}).get("arguments", {}), ensure_ascii=False)}]}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False), flush=True)
'''


class MCPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_mcp_"))
        self.server = self.tmp / "server.py"
        self.server.write_text(SERVER, encoding="utf-8")
        self.manager = MCPManager()

    def tearDown(self):
        for name in list(self.manager.sessions):
            self.manager.close(name)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_stdio_connect_list_and_call(self):
        result = self.manager.connect({
            "name": "mock",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(self.server)],
        })
        self.assertEqual(result["name"], "mock")
        self.assertEqual(result["tools"][0]["name"], "echo")
        called = self.manager.call_tool("mock", "echo", {"x": 1})
        text = called["content"][0]["text"]
        self.assertEqual(json.loads(text)["x"], 1)
        self.assertEqual(len(self.manager.list()), 1)
        self.manager.close("mock")
        self.assertEqual(self.manager.list(), [])


if __name__ == "__main__":
    unittest.main()
