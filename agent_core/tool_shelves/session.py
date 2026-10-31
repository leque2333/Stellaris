# -*- coding: utf-8 -*-
"""会话短路上下文：最近三句 + 上轮类别 + 工具结果计数。

为什么需要它：
AstrBot 送来的 messages 可能被压缩、可能只带当前一条，所以不能指望从请求里
稳定拼出"最近三句"。这里按 (channel_id, actor_id) 自己留一份环形缓存，
供小模型判断情境用。

刻意很小：每句最多 160 字，最多 8 条，30 分钟过期，最多 2000 个会话。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List

from ..identity import IdentityResolver

MAX_SESSIONS = 2000
TTL_SECONDS = 30 * 60
MAX_TURNS = 8
MAX_CHARS = 160
CONTEXT_TURNS = 3


class SessionStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()

    @staticmethod
    def key(channel_id: str, actor_id: str) -> str:
        return f"{channel_id or 'unknown'}::{actor_id or 'unknown'}"

    # ---------------- 维护 ----------------
    def _drop_expired(self, now: float) -> None:
        dead = [k for k, v in self._data.items() if now - v.get("ts", 0) > TTL_SECONDS]
        for k in dead:
            self._data.pop(k, None)

    def _state(self, key: str, now: float) -> Dict[str, Any]:
        state = self._data.get(key)
        if state is None:
            state = {"ts": now, "turns": [], "cats": [], "tool_turns": 0}
            self._data[key] = state
            self._data.move_to_end(key)
            if len(self._data) > MAX_SESSIONS:
                self._data.popitem(last=False)
        return state

    # ---------------- 写 ----------------
    def remember(self, key: str, role: str, content: str) -> None:
        text = str(content or "").strip()
        if not text:
            return
        now = time.time()
        with self._lock:
            self._drop_expired(now)
            state = self._state(key, now)
            state["ts"] = now
            state["turns"].append({"role": role, "content": text[:MAX_CHARS]})
            if len(state["turns"]) > MAX_TURNS:
                state["turns"] = state["turns"][-MAX_TURNS:]
            self._data.move_to_end(key)

    def set_categories(self, key: str, categories: List[str]) -> None:
        now = time.time()
        with self._lock:
            state = self._state(key, now)
            state["ts"] = now
            state["cats"] = [str(c) for c in (categories or [])]
            state["tool_turns"] = 0

    def note_tool_result(self, key: str) -> int:
        now = time.time()
        with self._lock:
            state = self._state(key, now)
            state["ts"] = now
            state["tool_turns"] = int(state.get("tool_turns") or 0) + 1
            return state["tool_turns"]

    def reset_tool_turns(self, key: str) -> None:
        now = time.time()
        with self._lock:
            state = self._state(key, now)
            state["ts"] = now
            state["tool_turns"] = 0

    # ---------------- 读 ----------------
    def categories(self, key: str) -> List[str]:
        now = time.time()
        with self._lock:
            self._drop_expired(now)
            state = self._data.get(key)
            if not state:
                return []
            return list(state.get("cats") or [])

    def tool_turns(self, key: str) -> int:
        with self._lock:
            state = self._data.get(key)
            return int((state or {}).get("tool_turns") or 0)

    def recent(self, key: str, n: int = CONTEXT_TURNS) -> List[Dict[str, str]]:
        now = time.time()
        with self._lock:
            self._drop_expired(now)
            state = self._data.get(key)
            if not state:
                return []
            return list(state.get("turns") or [])[-max(1, n):]

    def as_lines(self, key: str, n: int = CONTEXT_TURNS) -> List[str]:
        role_name = {"user": "用户", "assistant": IdentityResolver.get_bot_name(), "tool": "工具", "system": "系统"}
        lines = []
        for turn in self.recent(key, n):
            label = role_name.get(str(turn.get("role")), str(turn.get("role")))
            lines.append(f"{label}：{turn.get('content') or ''}")
        return lines

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._data)


session_store = SessionStore()

__all__ = ["SessionStore", "session_store", "CONTEXT_TURNS"]
