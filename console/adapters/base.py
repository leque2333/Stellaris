# -*- coding: utf-8 -*-
"""插件适配器的最小契约：只做归一化，不执行工具、不写业务库。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping


@dataclass(frozen=True)
class RequestView:
    """一次入站请求的只读视图；适配器只能看这些字段。"""

    headers: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, Any] = field(default_factory=dict)
    channel_id: str = ""
    session_id: str = ""
    path: str = ""

    def header(self, name: str, default: str = "") -> str:
        for key, value in self.headers.items():
            if str(key).lower() == str(name).lower():
                return str(value or "")
        return default


class Adapter:
    """适配器接口。实现类不要联网、不要执行命令。"""

    plugin_id: str = "unknown"
    priority: int = 0

    def detect(self, request: RequestView) -> float:
        """返回 0~1 的命中置信度。"""
        return 0.0

    def context(self, request: RequestView) -> Dict[str, Any]:
        """返回 platform / uid / name / session / group / host_id 等归一化字段。"""
        return {}

    def group_name(self, group_id: str) -> str:
        """可选：群号 → 群名。"""
        return ""


__all__ = ["Adapter", "RequestView"]
