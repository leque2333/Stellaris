# -*- coding: utf-8 -*-
"""工具注册表：记住"网关见过哪些工具"。

配置里手工登记的工具由 tool_shelves.json 管；这里补的是**请求里出现过、
但配置没登记**的工具，方便在控制台里补归类。

存储：data/tool_registry.json
    {"version": 1, "tools": {"工具名": {"first_seen": ts, "last_seen": ts,
                                        "desc": "", "plugin": "bloub_voice"}}}

写入是节流的：同一个工具 10 分钟内只更新一次 last_seen，避免每轮请求都落盘。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable

_LOCK = threading.Lock()
_CACHE: Dict[str, Dict[str, Any]] = {}
_LOADED = False
_LAST_FLUSH = 0.0
_FLUSH_INTERVAL = 10.0


def _path() -> Path:
    env = os.getenv("TOOL_REGISTRY_PATH", "").strip()
    if env:
        return Path(env)
    root = Path(__file__).resolve().parent.parent.parent
    return root / "data" / "tool_registry.json"


def _load() -> Dict[str, Dict[str, Any]]:
    global _LOADED, _CACHE
    if _LOADED:
        return _CACHE
    data: Dict[str, Dict[str, Any]] = {}
    path = _path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            tools = raw.get("tools") if isinstance(raw, dict) else None
            if isinstance(tools, dict):
                for name, info in tools.items():
                    if isinstance(info, dict):
                        data[str(name)] = info
        except Exception:
            pass
    _CACHE = data
    _LOADED = True
    return _CACHE


def _flush(force: bool = False) -> None:
    global _LAST_FLUSH
    now = time.time()
    if not force and now - _LAST_FLUSH < _FLUSH_INTERVAL:
        return
    _LAST_FLUSH = now
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": 1, "tools": _CACHE}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except Exception:
        pass


def record(tools: Iterable[Any], plugin: str = "") -> None:
    """记录一批工具（名字、描述、来源插件）。"""
    from .catalog import tool_description, tool_name

    plugin = str(plugin or "").strip()[:120]
    now = time.time()
    changed = False
    with _LOCK:
        data = _load()
        for tool in tools or []:
            name = tool_name(tool)
            if not name:
                continue
            desc = tool_description(tool)[:200]
            item = data.get(name)
            if item is None:
                data[name] = {
                    "first_seen": now, "last_seen": now, "desc": desc,
                    "plugin": plugin,
                }
                changed = True
            else:
                item["last_seen"] = now
                if desc and not item.get("desc"):
                    item["desc"] = desc
                old_plugin = str(item.get("plugin") or "")
                if plugin and (
                    not old_plugin
                    or ("astrbot_plugin_" not in old_plugin and "astrbot_plugin_" in plugin)
                ):
                    item["plugin"] = plugin
                    changed = True
    if changed:
        with _LOCK:
            _flush(force=True)


def all_tools() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return dict(_load())


def clear() -> None:
    global _LOADED, _CACHE
    with _LOCK:
        _CACHE = {}
        _LOADED = True
        _flush(force=True)


__all__ = ["record", "all_tools", "clear"]
