# -*- coding: utf-8 -*-
"""日志事件驱动：按行 + 正则命名分组"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from typing import List

from ..engine import BridgeDriver
from ..models import BridgeSpec, HubEvent


class LogEventDriver(BridgeDriver):
    kind = "log"

    def __init__(self, spec: BridgeSpec, emit, store=None):
        super().__init__(spec, emit, store)
        self.path = Path(spec.locator).resolve()
        opts = spec.options
        self.pattern = re.compile(str(opts.get("pattern") or "")) if opts.get("pattern") else None
        self.platform = str(opts.get("platform") or "unknown")
        self.poll_interval = max(0.5, float(opts.get("poll_interval") or 2.0))
        self.offset = int(store.get_bridge_cursor(spec.source_id) or 0) if store else 0

    def _event(self, line: str) -> HubEvent:
        groups = {}
        if self.pattern:
            match = self.pattern.search(line)
            if not match:
                return None
            groups = match.groupdict()
        content = str(groups.get("content") or line).strip()
        if not content:
            return None
        event_id = str(groups.get("id") or hashlib.sha1(line.encode()).hexdigest()[:12])
        ts = groups.get("time")
        try:
            timestamp = float(ts) if ts else time.time()
        except (TypeError, ValueError):
            timestamp = time.time()
        return HubEvent(
            source=self.spec.source_id,
            event_key=f"{self.spec.source_id}:{event_id}",
            platform=self.platform,
            channel_id=str(groups.get("channel") or ""),
            channel_name=str(groups.get("channel_name") or ""),
            actor_id=str(groups.get("actor") or ""),
            actor_name=str(groups.get("actor_name") or ""),
            content=content,
            timestamp=timestamp,
            raw={"source": "log"},
        )

    def preview(self, limit: int = 20) -> List[HubEvent]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                event = self._event(line)
                if event:
                    events.append(event)
                if len(events) >= limit:
                    break
        return events

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                events = await asyncio.to_thread(self._poll)
                for event in events:
                    await self.emit(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue

    def _poll(self) -> List[HubEvent]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            for raw in fh:
                end = fh.tell()
                text = raw.decode("utf-8", errors="ignore").strip()
                if text:
                    event = self._event(text)
                    if event:
                        events.append(event)
                self.offset = end
                if self.store:
                    self.store.set_bridge_cursor(self.spec.source_id, str(self.offset))
        return events
