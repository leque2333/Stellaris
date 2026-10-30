# -*- coding: utf-8 -*-
"""通用协议探针：只识别协议，不认识具体插件"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional


class ProtocolDriver:
    name = "base"

    def probe(self, path: Path) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class SqliteDriver(ProtocolDriver):
    name = "sqlite"

    def probe(self, path: Path) -> Optional[Dict[str, Any]]:
        if path.suffix.lower() not in (".db", ".sqlite", ".sqlite3"):
            return None
        try:
            with path.open("rb") as fh:
                if fh.read(16) != b"SQLite format 3\x00":
                    return None
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )]
            conn.close()
            return {
                "protocol": self.name,
                "spec": {"path": str(path), "tables": tables[:50]},
                "confidence": 0.9 if tables else 0.5,
            }
        except Exception:
            return None


class JsonDriver(ProtocolDriver):
    name = "json"

    def probe(self, path: Path) -> Optional[Dict[str, Any]]:
        if path.suffix.lower() != ".json" or path.stat().st_size > 5 * 1024 * 1024:
            return None
        if any(x in path.name.lower() for x in ("secret", "credential", "token", "key")):
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        keys = list(data.keys())[:30] if isinstance(data, dict) else []
        return {
            "protocol": self.name,
            "spec": {
                "path": str(path),
                "top_type": type(data).__name__,
                "keys": keys,
            },
            "confidence": 0.5,
        }


class LogDriver(ProtocolDriver):
    name = "log"

    def probe(self, path: Path) -> Optional[Dict[str, Any]]:
        if path.suffix.lower() not in (".log", ".jsonl"):
            return None
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                lines = [next(fh, "") for _ in range(20)]
        except Exception:
            return None
        lines = [x for x in lines if x.strip()]
        if not lines:
            return None
        return {
            "protocol": self.name,
            "spec": {"path": str(path), "sample_lines": lines[:3]},
            "confidence": 0.4,
        }


DRIVERS = [SqliteDriver(), JsonDriver(), LogDriver()]


def probe_path(path: Path) -> Optional[Dict[str, Any]]:
    for driver in DRIVERS:
        try:
            result = driver.probe(path)
        except Exception:
            result = None
        if result:
            return result
    return None
