# -*- coding: utf-8 -*-
"""JSON / JSONL 事件驱动"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

from ..engine import BridgeDriver
from ..models import BridgeSpec, HubEvent
from .sqlite import _extract_text, _get_pointer, _parse_time, _strip_wrapper


class JsonEventDriver(BridgeDriver):
    kind = "json"

    def __init__(self, spec: BridgeSpec, emit, store=None):
        super().__init__(spec, emit, store)
        self.path = Path(spec.locator).resolve()
        opts = spec.options
        self.items_path = str(opts.get("items_path") or "")
        self.id_field = str(opts.get("id") or "id")
        self.time_field = str(opts.get("time") or "timestamp")
        self.channel_field = str(opts.get("channel") or "channel_id")
        self.channel_name_field = str(opts.get("channel_name") or "")
        self.actor_field = str(opts.get("actor") or "actor_id")
        self.actor_name_field = str(opts.get("actor_name") or "")
        self.content_field = str(opts.get("content") or "content")
        self.platform = str(opts.get("platform") or "unknown")
        self.content_format = str(opts.get("content_format") or "")
        self.strip_wrapper = str(opts.get("strip_wrapper") or "")
        self.poll_interval = max(0.5, float(opts.get("poll_interval") or 2.0))
        self.mode = "jsonl" if self.path.suffix.lower() in (".jsonl", ".ndjson") else "json"
        self.offset = int(store.get_bridge_cursor(spec.source_id) or 0) if store else 0

    def _value(self, item: Dict[str, Any], field: str) -> Any:
        if not field:
            return None
        return _get_pointer(item, field)

    def _event_from_item(self, item: Dict[str, Any], fallback_id: str = "") -> HubEvent:
        event_id = str(self._value(item, self.id_field) or fallback_id)
        raw_content = self._value(item, self.content_field)
        content = _extract_text(
            _strip_wrapper(str(raw_content or ""), self.strip_wrapper),
            self.content_format,
        ).strip()
        if not event_id or not content:
            return None
        return HubEvent(
            source=self.spec.source_id,
            event_key=f"{self.spec.source_id}:{event_id}",
            platform=self.platform,
            channel_id=str(self._value(item, self.channel_field) or ""),
            channel_name=str(self._value(item, self.channel_name_field) or "") if self.channel_name_field else "",
            actor_id=str(self._value(item, self.actor_field) or ""),
            actor_name=str(self._value(item, self.actor_name_field) or "") if self.actor_name_field else "",
            content=content,
            timestamp=_parse_time(self._value(item, self.time_field)),
            raw={"source": "json", "id": event_id},
        )

    def preview(self, limit: int = 20) -> List[HubEvent]:
        if not self.path.exists():
            return []
        events: List[HubEvent] = []
        if self.mode == "jsonl":
            with self.path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(item, dict):
                        event = self._event_from_item(item)
                        if event:
                            events.append(event)
                    if len(events) >= limit:
                        break
            return events

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        items = _get_pointer(data, self.items_path) if self.items_path else data
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            event = self._event_from_item(item, fallback_id=str(idx))
            if event:
                events.append(event)
            if len(events) >= limit:
                break
        return events

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                if self.mode == "jsonl":
                    events = await asyncio.to_thread(self._poll_jsonl)
                else:
                    events = await asyncio.to_thread(self.preview)
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

    def _poll_jsonl(self) -> List[HubEvent]:
        if not self.path.exists():
            return []
        events: List[HubEvent] = []
        with self.path.open("rb") as fh:
            fh.seek(self.offset)
            for raw in fh:
                start = fh.tell()
                text = raw.decode("utf-8", errors="ignore").strip()
                if not text:
                    self.offset = start
                    continue
                try:
                    item = json.loads(text)
                except Exception:
                    self.offset = start
                    continue
                if isinstance(item, dict):
                    event = self._event_from_item(item)
                    if event:
                        events.append(event)
                self.offset = start
                if self.store:
                    self.store.set_bridge_cursor(self.spec.source_id, str(self.offset))
        return events
