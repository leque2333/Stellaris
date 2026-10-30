# -*- coding: utf-8 -*-
"""连接器基类：只读扫描外部模块，返回标准化的平台上下文"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class DiscoveredContext:
    platform: str                 # qq / bilibili / host
    external_id: str              # UMO 或 bilibili:uid:xxx
    external_type: str = ""       # group / friend / account
    display_name: str = ""
    source: str = ""              # astrbot / bilibili_plugin
    evidence: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "external_id": self.external_id,
            "external_type": self.external_type,
            "display_name": self.display_name,
            "source": self.source,
            "evidence": list(self.evidence),
            "metadata": dict(self.metadata),
        }


@dataclass
class ConnectorReport:
    connector: str
    ok: bool = True
    source_path: str = ""
    contexts: List[DiscoveredContext] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector": self.connector,
            "ok": self.ok,
            "source_path": self.source_path,
            "contexts": [c.to_dict() for c in self.contexts],
            "count": len(self.contexts),
            "error": self.error,
        }


class Connector:
    name = "base"

    def discover(self) -> ConnectorReport:
        raise NotImplementedError
