# -*- coding: utf-8 -*-
"""MCP 管理器：后台事件循环 + 会话表"""

from __future__ import annotations

import asyncio
import re
import threading
from typing import Any, Dict, List

from .client import MCPClient


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") or "mcp"


class MCPManager:
    def __init__(self, store=None):
        self.store = store
        self.loop = asyncio.new_event_loop()
        self.sessions: Dict[str, Dict[str, Any]] = {}
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="mcp-loop")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def connect(self, config: Dict[str, Any]) -> Dict[str, Any]:
        async def _connect():
            name = str(config.get("name") or "").strip()
            if not name:
                raise ValueError("name 必填")
            if name in self.sessions:
                return self._public(name)
            client = MCPClient(
                name=name,
                transport=str(config.get("transport") or "stdio"),
                command=str(config.get("command") or ""),
                args=[str(x) for x in (config.get("args") or [])],
                url=str(config.get("url") or ""),
                env={str(k): str(v) for k, v in (config.get("env") or {}).items()},
            )
            await client.start()
            tools = await client.list_tools()
            self.sessions[name] = {
                "name": name,
                "client": client,
                "tools": tools,
                "transport": client.transport,
            }
            if self.store:
                from ..services import ServiceRegistry
                ServiceRegistry(self.store).register({
                    "service_id": f"mcp_{_safe(name)}",
                    "name": name,
                    "kind": "mcp",
                    "endpoint": f"mcp://{name}",
                    "capabilities": ["tools"],
                    "manifest": {"transport": client.transport, "tools": tools, "mcp_name": name},
                })
            return self._public(name)

        future = asyncio.run_coroutine_threadsafe(_connect(), self.loop)
        return future.result(timeout=120)

    def _public(self, name: str) -> Dict[str, Any]:
        item = self.sessions[name]
        return {
            "name": name,
            "transport": item["transport"],
            "tools": item["tools"],
            "service_id": f"mcp_{_safe(name)}",
        }

    def list(self) -> List[Dict[str, Any]]:
        return [self._public(name) for name in list(self.sessions)]

    def call_tool(self, name: str, tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name not in self.sessions:
            raise ValueError("MCP 会话不存在")
        client: MCPClient = self.sessions[name]["client"]

        async def _call():
            return await client.call_tool(tool, arguments or {})

        future = asyncio.run_coroutine_threadsafe(_call(), self.loop)
        return future.result(timeout=40)

    def close(self, name: str) -> Dict[str, Any]:
        item = self.sessions.pop(name, None)
        if not item:
            return {"ok": False, "error": "会话不存在"}
        client: MCPClient = item["client"]

        async def _close():
            await client.close()

        future = asyncio.run_coroutine_threadsafe(_close(), self.loop)
        future.result(timeout=10)
        if self.store:
            from ..services import ServiceRegistry
            ServiceRegistry(self.store).delete(f"mcp_{_safe(name)}")
        return {"ok": True, "name": name}
