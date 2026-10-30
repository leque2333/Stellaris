# -*- coding: utf-8 -*-
"""动态插件注册表：扫描/注册插件，代码里不出现具体插件名"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..storage import Store
from .drivers import probe_path

SKIP_DIRS = {".git", "node_modules", "__pycache__", "site-packages", "backups", "temp", "attachments"}


def default_roots() -> List[Path]:
    roots: List[Path] = []
    env = os.getenv("HUB_PLUGIN_ROOTS", "")
    for item in env.split(os.pathsep):
        if item.strip():
            roots.append(Path(item.strip()))
    roots.extend([
        Path.cwd() / "plugins",
        Path.cwd() / "data" / "plugins",
    ])
    out, seen = [], set()
    for root in roots:
        try:
            resolved = root.resolve()
        except Exception:
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _plugin_id(path: Path, protocol: str) -> str:
    digest = hashlib.sha1(f"{path}|{protocol}".encode("utf-8")).hexdigest()[:10]
    return f"plug_{digest}"


class PluginRegistry:
    def __init__(self, store: Store):
        self.store = store

    def list(self, protocol: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.store.list_plugins(protocol=protocol, status=status)

    def get(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_plugin(plugin_id)

    def register_path(
        self,
        path: Path,
        *,
        name: str = "",
        source: str = "scan",
    ) -> Optional[Dict[str, Any]]:
        target = Path(path).resolve()
        if not target.is_file():
            return None
        if target == Path(getattr(self.store, "path", "")).resolve():
            return None
        probe = probe_path(target)
        if not probe:
            return None
        plugin_id = _plugin_id(target, probe["protocol"])
        self.store.upsert_plugin(
            plugin_id,
            name=name or target.stem,
            root_path=str(target),
            protocol=probe["protocol"],
            spec=probe.get("spec") or {"path": str(target)},
            status="active",
            source=source,
            metadata={"confidence": probe.get("confidence", 0.5)},
        )
        return self.store.get_plugin(plugin_id)

    def register_manual(
        self,
        name: str,
        root_path: str,
        protocol: str,
        spec: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        plugin_id = _plugin_id(Path(root_path), protocol)
        self.store.upsert_plugin(
            plugin_id,
            name=name or Path(root_path).stem or plugin_id,
            root_path=root_path,
            protocol=protocol,
            spec=spec or {"path": root_path},
            status="active",
            source="manual",
        )
        return self.store.get_plugin(plugin_id)

    def scan(self, roots: Optional[Iterable[Path]] = None) -> Dict[str, Any]:
        targets = [Path(r) for r in roots] if roots is not None else default_roots()
        registered, scanned, skipped = [], 0, 0
        for root in targets:
            try:
                root = root.resolve()
            except Exception:
                continue
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if scanned >= 3000:
                    break
                if not path.is_file():
                    continue
                if any(part in SKIP_DIRS for part in path.parts):
                    continue
                scanned += 1
                plugin = self.register_path(path)
                if plugin:
                    registered.append(plugin)
                else:
                    skipped += 1
            if scanned >= 3000:
                break
        return {
            "scanned": scanned,
            "registered": registered,
            "skipped": skipped,
        }

    def delete(self, plugin_id: str) -> bool:
        return self.store.delete_plugin(plugin_id)
