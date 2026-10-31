# -*- coding: utf-8 -*-
"""BridgeEngine：按 kind 找通用驱动，统一把事件推给 sink"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict, Optional

from .ingest import BridgeIngestor
from .models import BridgeSpec, HubEvent

Emit = Callable[[HubEvent], Awaitable[Any]]


class BridgeDriver:
    kind = "base"

    def __init__(self, spec: BridgeSpec, emit: Emit, store: Optional[Any] = None):
        self.spec = spec
        self.emit = emit
        self.store = store
        self._stopped = asyncio.Event()

    async def run(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        self._stopped.set()


class BridgeEngine:
    def __init__(self, store: Optional[Any] = None):
        self.store = store
        self.drivers: Dict[str, type[BridgeDriver]] = {}
        self.running: Dict[str, Dict[str, Any]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    def register(self, driver_cls: type[BridgeDriver]) -> None:
        self.drivers[driver_cls.kind] = driver_cls

    def start(self, spec: BridgeSpec, emit: Optional[Emit] = None) -> str:
        driver_cls = self.drivers.get(spec.kind)
        if not driver_cls:
            raise ValueError(f"没有 kind={spec.kind} 的桥驱动")
        sink = emit if emit is not None else (
            BridgeIngestor(self.store).handle if self.store else None
        )
        if sink is None:
            raise ValueError("没有 store 时必须提供 emit")
        driver = driver_cls(spec, sink, self.store)
        task = asyncio.create_task(driver.run(), name=f"bridge:{spec.source_id}")
        self._tasks[spec.source_id] = task
        self.running[spec.source_id] = {
            "source_id": spec.source_id,
            "kind": spec.kind,
            "scope": spec.scope,
            "locator": spec.locator,
            "status": "running",
        }
        task.add_done_callback(lambda _t, sid=spec.source_id: self._mark_done(sid))
        return spec.source_id

    def _mark_done(self, source_id: str) -> None:
        item = self.running.get(source_id)
        if item:
            item["status"] = "stopped"
        self._tasks.pop(source_id, None)

    async def stop(self, source_id: str) -> None:
        task = self._tasks.get(source_id)
        if not task:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        self._mark_done(source_id)

    async def stop_all(self) -> None:
        for source_id in list(self._tasks):
            await self.stop(source_id)

    def status(self) -> Dict[str, Any]:
        return {"running": list(self.running.values())}
