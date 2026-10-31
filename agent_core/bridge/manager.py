# -*- coding: utf-8 -*-
"""在线桥管理器：把异步 BridgeEngine 放进后台事件循环"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict

from .engine import BridgeEngine
from .models import BridgeSpec


class BridgeManager:
    def __init__(self, store):
        self.store = store
        self.engine = BridgeEngine(store)
        from .drivers import HttpEventDriver, JsonEventDriver, LogEventDriver, SqliteEventDriver
        self.engine.register(SqliteEventDriver)
        self.engine.register(JsonEventDriver)
        self.engine.register(LogEventDriver)
        self.engine.register(HttpEventDriver)
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="bridge-loop")
        self._thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def start(self, spec: BridgeSpec, persist: bool = True) -> Dict[str, Any]:
        async def _start():
            source_id = self.engine.start(spec)
            return {"ok": True, "source_id": source_id}

        future = asyncio.run_coroutine_threadsafe(_start(), self.loop)
        result = future.result(timeout=10)
        if persist and self.store:
            self.store.upsert_bridge_spec(
                spec.to_dict(), auto_start=True, status="running"
            )
        return result

    def stop(self, source_id: str) -> Dict[str, Any]:
        async def _stop():
            await self.engine.stop(source_id)
            return {"ok": True, "source_id": source_id}

        future = asyncio.run_coroutine_threadsafe(_stop(), self.loop)
        result = future.result(timeout=10)
        if self.store:
            self.store.set_bridge_spec_status(source_id, "stopped", auto_start=False)
        return result

    def restore(self) -> Dict[str, Any]:
        if not self.store:
            return {"restored": 0}
        restored = 0
        for spec_data in self.store.list_bridge_specs(auto_start=True):
            try:
                spec = BridgeSpec(
                    source_id=spec_data["source_id"],
                    kind=spec_data["kind"],
                    scope=spec_data.get("scope") or "runtime",
                    locator=spec_data.get("locator") or "",
                    options=spec_data.get("options") or {},
                )
                self.start(spec, persist=False)
                self.store.set_bridge_spec_status(spec.source_id, "running")
                restored += 1
            except Exception:
                self.store.set_bridge_spec_status(spec_data["source_id"], "error", auto_start=False)
        return {"restored": restored}

    def status(self) -> Dict[str, Any]:
        data = self.engine.status()
        if self.store:
            data["saved"] = self.store.list_bridge_specs()
        else:
            data["saved"] = []
        return data
