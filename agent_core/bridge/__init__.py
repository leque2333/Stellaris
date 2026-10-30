# -*- coding: utf-8 -*-
"""通用桥接层：Source / BridgeSpec / BridgeEngine"""

from .drivers import HttpEventDriver, JsonEventDriver, LogEventDriver, SqliteEventDriver
from .engine import BridgeDriver, BridgeEngine
from .ingest import BridgeIngestor
from .infer import BridgeSpecInferer
from .manager import BridgeManager
from .models import BridgeSpec, HubEvent


def default_engine(store=None) -> BridgeEngine:
    engine = BridgeEngine(store)
    engine.register(SqliteEventDriver)
    engine.register(JsonEventDriver)
    engine.register(LogEventDriver)
    engine.register(HttpEventDriver)
    return engine


__all__ = [
    "BridgeDriver",
    "BridgeEngine",
    "BridgeIngestor",
    "BridgeManager",
    "BridgeSpec",
    "BridgeSpecInferer",
    "HubEvent",
    "HttpEventDriver",
    "JsonEventDriver",
    "LogEventDriver",
    "SqliteEventDriver",
    "default_engine",
]
