# -*- coding: utf-8 -*-
"""MCP 客户端：stdio / Streamable HTTP，JSON-RPC 2.0"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

import aiohttp


class MCPError(RuntimeError):
    pass


class MCPClient:
    def __init__(
        self,
        name: str,
        transport: str = "stdio",
        command: str = "",
        args: Optional[List[str]] = None,
        url: str = "",
        env: Optional[Dict[str, str]] = None,
    ):
        self.name = name
        self.transport = transport
        self.command = command
        self.args = list(args or [])
        self.url = url
        self.env = dict(env or {})
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.transport == "stdio":
            if not self.command:
                raise MCPError("stdio 需要 command")
            env = os.environ.copy()
            env.update(self.env)
            self.proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        elif self.transport in ("http", "streamable_http"):
            if not self.url:
                raise MCPError("http 需要 url")
            self.session = aiohttp.ClientSession()
        else:
            raise MCPError(f"不支持的 MCP transport: {self.transport}")
        await self.initialize()

    async def initialize(self) -> None:
        await self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "anan-hub", "version": "0.1"},
        }, timeout=90)
        if self.transport == "stdio":
            await self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})
        else:
            try:
                await self._http_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
            except Exception:
                pass

    async def list_tools(self) -> List[Dict[str, Any]]:
        result = await self._request("tools/list", {}, timeout=60)
        tools = result.get("tools") if isinstance(result, dict) else None
        return tools if isinstance(tools, list) else []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = await self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return result if isinstance(result, dict) else {"content": result}

    async def _write(self, payload: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin:
            raise MCPError("stdio 进程未启动")
        self.proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await self.proc.stdin.drain()

    async def _request(self, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Any:
        async with self._lock:
            request_id = self._next_id
            self._next_id += 1
            payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            if self.transport == "stdio":
                await self._write(payload)
                return await asyncio.wait_for(self._read_response(request_id), timeout=timeout)
            data = await self._http_request(payload)
            if isinstance(data, list):
                for item in data:
                    if item.get("id") == request_id:
                        return self._unwrap(item)
            return self._unwrap(data)

    async def _read_response(self, request_id: int) -> Any:
        if not self.proc or not self.proc.stdout:
            raise MCPError("stdio 进程未启动")
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise MCPError("MCP stdio 进程已退出")
            try:
                msg = json.loads(line.decode("utf-8", errors="ignore"))
            except Exception:
                continue
            if msg.get("id") != request_id:
                continue
            return self._unwrap(msg)

    @staticmethod
    def _unwrap(msg: Dict[str, Any]) -> Any:
        if msg.get("error"):
            raise MCPError(str(msg["error"]))
        return msg.get("result")

    async def _http_request(self, payload: Dict[str, Any]) -> Any:
        if not self.session or not self.url:
            raise MCPError("HTTP MCP 未启动")
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        async with self.session.post(self.url, json=payload, headers=headers, timeout=30) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MCPError(f"HTTP {resp.status}: {text[:300]}")
            content_type = resp.headers.get("Content-Type", "")
        if "text/event-stream" in content_type:
            for line in text.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data and data != "[DONE]":
                        return json.loads(data)
            return {}
        return json.loads(text or "{}")

    async def close(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
                await asyncio.wait_for(self.proc.wait(), timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.session:
            await self.session.close()
