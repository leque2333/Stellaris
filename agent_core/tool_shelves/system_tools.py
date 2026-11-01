# -*- coding: utf-8 -*-
"""系统工具注册：不属于某个宿主插件的通用能力。

工具页只认这里的元数据（系统/宿主/传输方式），不认 AstrBot。
以后接别的宿主，只需要注册新的 system tool，不需要改页面逻辑。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

SYSTEM_TOOLS: Dict[str, Dict[str, Any]] = {
    "control_host_pc_via_antigravity": {
        "label": "宿主机控制（主入口）",
        "kind": "host",
        "transport": "astrbot-plugin",
    },
    "hub_host_control": {
        "label": "宿主机控制（中枢备用）",
        "kind": "host",
        "transport": "hub-native",
    },
    "hub_ledger_query": {
        "label": "账本查询（中枢原生）",
        "kind": "ledger",
        "transport": "hub-native",
    },
}


def _path() -> Path:
    raw = os.getenv("HUB_SYSTEM_TOOLS_PATH", "")
    return Path(raw) if raw else Path(__file__).resolve().parents[2] / "data" / "system_tools.json"


def _load() -> Dict[str, bool]:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            return {str(k): bool(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def enabled(name: str) -> bool:
    return _load().get(str(name), True)


def set_enabled(name: str, value: bool) -> None:
    data = _load()
    data[str(name)] = bool(value)
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def entries() -> Dict[str, Dict[str, Any]]:
    return {name: dict(meta) for name, meta in SYSTEM_TOOLS.items()}


def is_system(name: str) -> bool:
    return str(name) in SYSTEM_TOOLS


__all__ = ["SYSTEM_TOOLS", "entries", "enabled", "set_enabled", "is_system"]
