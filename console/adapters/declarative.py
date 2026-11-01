# -*- coding: utf-8 -*-
"""声明式适配器：JSON 字段映射，不执行任意代码。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .base import Adapter, RequestView


class DeclarativeAdapter(Adapter):
    def __init__(self, config: Dict[str, Any], path: str = "") -> None:
        self.config = dict(config or {})
        self.plugin_id = str(self.config.get("plugin_id") or "").strip() or "declarative"
        self.priority = int(self.config.get("priority") or 0)
        self.source_path = path

    @classmethod
    def from_file(cls, path: Path) -> "DeclarativeAdapter":
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("adapter.json 必须是对象")
        return cls(data, str(path))

    def detect(self, request: RequestView) -> float:
        match = self.config.get("match") or {}
        if not isinstance(match, dict) or not match:
            return 0.0
        if not self._match(match, request):
            return 0.0
        return float(self.config.get("score") or 0.8)

    def context(self, request: RequestView) -> Dict[str, Any]:
        fields = self.config.get("fields") or {}
        result: Dict[str, Any] = {"plugin_id": self.plugin_id}
        if isinstance(fields, dict):
            for key, expression in fields.items():
                result[str(key)] = _resolve(str(expression or ""), request)
        result.setdefault("host_id", self.plugin_id)
        return result

    @staticmethod
    def _match(match: Dict[str, Any], request: RequestView) -> bool:
        path_prefix = str(match.get("path_prefix") or "")
        if path_prefix and not request.path.startswith(path_prefix):
            return False
        ua = str(match.get("user_agent") or "").lower()
        if ua and ua not in request.header("User-Agent").lower():
            return False
        session = str(match.get("session_contains") or "").lower()
        if session and session not in request.session_id.lower():
            return False
        header_rules = match.get("header") or {}
        if isinstance(header_rules, dict):
            for name, expected in header_rules.items():
                if request.header(str(name)) != str(expected):
                    return False
        return True


def _resolve(expression: str, request: RequestView) -> Any:
    """支持 `a.b | c.d | 'literal'`；只做只读取值，不 eval。"""
    for raw in str(expression or "").split("|"):
        part = raw.strip()
        if not part:
            continue
        if (part.startswith("'") and part.endswith("'")) or (
            part.startswith('"') and part.endswith('"')
        ):
            return part[1:-1]
        value = _walk(part, request)
        if value not in (None, ""):
            return value
    return ""


def _walk(path: str, request: RequestView) -> Any:
    value: Any = {
        "headers": request.headers,
        "body": request.body,
        "channel_id": request.channel_id,
        "session_id": request.session_id,
        "path": request.path,
    }
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(value, Mapping):
            value = _lookup(value, part)
        else:
            return ""
    return "" if value is None else value


def _lookup(mapping: Mapping, key: str) -> Any:
    if key in mapping:
        return mapping[key]
    lowered = key.lower()
    for name, value in mapping.items():
        if str(name).lower() == lowered:
            return value
    return None


__all__ = ["DeclarativeAdapter"]
