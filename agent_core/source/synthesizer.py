# -*- coding: utf-8 -*-
"""源码 → 候选 BridgeSpec：用现有推断器对源码里发现的 DB/配置做试读"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from ..bridge.infer import BridgeSpecInferer
from .analyzer import SourceAnalyzer

CONTENT_KEYS = {"content", "text", "message", "msg", "body"}
ID_KEYS = {"id", "message_id", "event_id", "uuid"}


def _json_looks_like_events(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    items = data if isinstance(data, list) else None
    if items is None and isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value:
                items = value
                break
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return False
    keys = {str(k).lower() for k in items[0].keys()}
    return bool(keys & CONTENT_KEYS) and bool(keys & ID_KEYS)


class SourceSynthesizer:
    def __init__(self, analyzer: SourceAnalyzer = None):
        self.analyzer = analyzer or SourceAnalyzer()
        self.inferer = BridgeSpecInferer()

    async def generate(self, repo_path: str) -> Dict[str, Any]:
        analysis = self.analyzer.analyze(repo_path)
        if not analysis.get("ok"):
            return {"ok": False, "error": analysis.get("error"), "analysis": analysis, "specs": []}
        root = Path(analysis["repo"])
        candidates: List[tuple] = []
        for db in analysis.get("databases") or []:
            candidates.append((root / db["path"], "sqlite"))
        for rel in analysis.get("configs") or []:
            path = root / rel
            name = path.name.lower()
            if path.suffix.lower() != ".json":
                continue
            if any(x in name for x in ("schema", "package", "tsconfig", "lock")):
                continue
            if not _json_looks_like_events(path):
                continue
            candidates.append((path, "json"))
        for item in analysis.get("logs") or []:
            path = root / item["path"]
            if path.suffix.lower() in (".log", ".jsonl"):
                candidates.append((path, "log"))
                break

        specs = []
        seen = set()
        for path, kind in candidates:
            key = (str(path), kind)
            if key in seen or len(specs) >= 6:
                continue
            seen.add(key)
            try:
                result = await self.inferer.infer(str(path), kind)
            except Exception as exc:
                result = {"ok": False, "error": str(exc), "preview": []}
            specs.append({
                "target": str(path),
                "kind": kind,
                "ok": bool(result.get("ok")),
                "spec": result.get("spec"),
                "preview": result.get("preview") or [],
                "confidence": result.get("confidence"),
                "error": result.get("error") or "",
            })
        return {
            "ok": any(x["ok"] for x in specs),
            "analysis": analysis,
            "specs": specs,
        }
