# -*- coding: utf-8 -*-
"""HTTP/JSON 事件驱动：鉴权头、GET/POST、重试、页码/游标分页"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from ..engine import BridgeDriver
from ..models import BridgeSpec, HubEvent


def _get_pointer(data: Any, pointer: str) -> Any:
    cur = data
    for part in str(pointer or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
    return cur


class HttpEventDriver(BridgeDriver):
    kind = "http"

    def __init__(self, spec: BridgeSpec, emit, store=None):
        super().__init__(spec, emit, store)
        opts = spec.options
        self.url = spec.locator
        self.method = str(opts.get("method") or "GET").upper()
        self.headers = dict(opts.get("headers") or {})
        self.params = dict(opts.get("params") or {})
        self.body = opts.get("body") if isinstance(opts.get("body"), dict) else None
        self.items_path = str(opts.get("items_path") or "")
        self.id_field = str(opts.get("id") or "id")
        self.time_field = str(opts.get("time") or "timestamp")
        self.channel_field = str(opts.get("channel") or "channel_id")
        self.actor_field = str(opts.get("actor") or "actor_id")
        self.content_field = str(opts.get("content") or "content")
        self.platform = str(opts.get("platform") or "unknown")
        self.poll_interval = max(1.0, float(opts.get("poll_interval") or 5.0))
        self.timeout = max(1.0, float(opts.get("timeout") or 15.0))

        retry = opts.get("retry") or {}
        self.max_attempts = max(1, min(int(retry.get("max_attempts") or 3), 6))
        self.retry_backoff = max(0.0, float(retry.get("backoff") or 0.5))
        self.retry_statuses = set(retry.get("statuses") or [429, 500, 502, 503, 504])

        paging = opts.get("pagination") or {}
        self.pagination_mode = str(paging.get("mode") or ("cursor" if paging.get("next_path") else "page"))
        self.page_param = str(paging.get("param") or "")
        self.page_start = int(paging.get("start") or 1)
        self.max_pages = max(1, min(int(paging.get("max_pages") or 5), 50))
        self.page_size_param = str(paging.get("page_size_param") or "")
        self.page_size = int(paging.get("page_size") or 50)
        self.next_path = str(paging.get("next_path") or "")
        self.cursor_param = str(paging.get("cursor_param") or "cursor")
        self.cursor = str(paging.get("start_cursor") or "")

    def _params(self, page: int, cursor: str) -> Dict[str, Any]:
        params = dict(self.params)
        if self.pagination_mode == "cursor":
            if self.cursor_param:
                params[self.cursor_param] = cursor
        elif self.page_param:
            params[self.page_param] = page
        if self.page_size_param:
            params[self.page_size_param] = self.page_size
        return params

    async def _request(
        self, session: aiohttp.ClientSession, page: int, cursor: str
    ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        params = self._params(page, cursor)
        kwargs: Dict[str, Any] = {"headers": self.headers}
        if params:
            kwargs["params"] = params
        if self.method != "GET" and self.body is not None:
            kwargs["json"] = self.body

        for attempt in range(self.max_attempts):
            try:
                async with session.request(self.method, self.url, **kwargs) as resp:
                    if resp.status in self.retry_statuses and attempt < self.max_attempts - 1:
                        await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                        continue
                    if resp.status != 200:
                        return [], None
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt >= self.max_attempts - 1:
                    return [], None
                await asyncio.sleep(self.retry_backoff * (2 ** attempt))
                continue
            items = _get_pointer(data, self.items_path) if self.items_path else data
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                items = []
            return [x for x in items if isinstance(x, dict)], data if isinstance(data, dict) else None
        return [], None

    async def poll_once(self) -> List[HubEvent]:
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        events: List[HubEvent] = []
        async with aiohttp.ClientSession(timeout=timeout) as session:
            page = self.page_start
            cursor = self.cursor
            for _ in range(self.max_pages):
                items, data = await self._request(session, page, cursor)
                if not items:
                    break
                for item in items:
                    event_id = str(item.get(self.id_field) or "")
                    content = str(item.get(self.content_field) or "").strip()
                    if not event_id or not content:
                        continue
                    events.append(HubEvent(
                        source=self.spec.source_id,
                        event_key=f"{self.spec.source_id}:{event_id}",
                        platform=self.platform,
                        channel_id=str(item.get(self.channel_field) or ""),
                        actor_id=str(item.get(self.actor_field) or ""),
                        content=content,
                        timestamp=float(item.get(self.time_field) or time.time()),
                        raw=item,
                    ))
                if self.pagination_mode == "cursor":
                    next_token = _get_pointer(data, self.next_path) if self.next_path else None
                    if not next_token:
                        break
                    cursor = str(next_token)
                else:
                    if not self.page_param or len(items) < self.page_size:
                        break
                    page += 1
        return events

    async def run(self) -> None:
        while not self._stopped.is_set():
            try:
                for event in await self.poll_once():
                    await self.emit(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                continue
