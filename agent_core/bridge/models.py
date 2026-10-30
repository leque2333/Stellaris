# -*- coding: utf-8 -*-
"""通用桥接模型：Source / BridgeSpec / HubEvent"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class HubEvent:
    source: str
    event_key: str
    platform: str = "unknown"
    channel_id: str = ""
    channel_name: str = ""
    actor_id: str = ""
    actor_name: str = ""
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    attachments: List[Any] = field(default_factory=list)
    reply_to: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "event_key": self.event_key,
            "platform": self.platform,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "attachments": self.attachments,
            "reply_to": self.reply_to,
        }


@dataclass
class BridgeSpec:
    source_id: str
    kind: str                       # sqlite / http / onebot / mcp ...
    scope: str = "runtime"          # protocol / runtime / plugin
    locator: str = ""               # path / url / command
    options: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BridgeSpec":
        return cls(
            source_id=str(data.get("source_id") or data.get("plugin_id") or "source"),
            kind=str(data.get("kind") or data.get("protocol") or ""),
            scope=str(data.get("scope") or "runtime"),
            locator=str(data.get("locator") or data.get("path") or data.get("url") or ""),
            options=dict(data.get("options") or {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "scope": self.scope,
            "locator": self.locator,
            "options": self.options,
        }
