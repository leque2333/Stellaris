# -*- coding: utf-8 -*-
"""事件入库：去重 + 写时间轴"""

from __future__ import annotations

import hashlib
import time

from ..models import TimelineEvent, get_cst_now_str
from ..storage import Store
from .models import HubEvent


class BridgeIngestor:
    def __init__(self, store: Store):
        self.store = store

    async def handle(self, event: HubEvent) -> bool:
        if not event.content.strip():
            return False
        if not self.store.mark_ingested(event.event_key, event.source, event.event_key):
            return False
        ts = float(event.timestamp or time.time())
        event_id = f"evt_{int(ts * 1000)}_{hashlib.sha1(event.event_key.encode()).hexdigest()[:6]}"
        primary = "QQ" if event.platform == "qq" else (
            "B站" if event.platform == "bilibili" else event.source
        )
        narrative = (
            f"[{event.channel_name or event.channel_id}] "
            f"{event.actor_name or event.actor_id}: {event.content}"
        ).strip()
        self.store.append_event(TimelineEvent(
            event_id=event_id,
            timestamp=ts,
            source_module=event.source,
            actor_id=event.actor_id or "unknown",
            actor_name=event.actor_name or "",
            narrative=narrative,
            time_str=get_cst_now_str(ts),
            importance=1.0,
            plugin_id=event.source,
            tag_primary=primary,
            tag_secondary=event.channel_name or event.channel_id or None,
            metadata={
                "event_key": event.event_key,
                "platform": event.platform,
                "channel_id": event.channel_id,
            },
        ))
        return True
