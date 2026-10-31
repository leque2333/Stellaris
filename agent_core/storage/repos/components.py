# -*- coding: utf-8 -*-
"""组件与宿主机：模块识别结果。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class ComponentOps:
    """组件与宿主机：模块识别结果。"""

    def upsert_component(
        self,
        component_id: str,
        host_id: str,
        name: str,
        *,
        version: str = "",
        kind: str = "secondary",
        path: str = "",
        entry: str = "",
        config_schema: str = "",
        category: str = "二级插件",
        capabilities: Optional[List[str]] = None,
        status: str = "active",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
        fingerprint: str = "",
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO components
                (component_id, host_id, name, version, kind, path, entry,
                 config_schema, category, capabilities, status, source, manifest,
                 fingerprint, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(component_id) DO UPDATE SET
                    host_id = excluded.host_id,
                    name = excluded.name,
                    version = excluded.version,
                    kind = excluded.kind,
                    path = excluded.path,
                    entry = excluded.entry,
                    config_schema = excluded.config_schema,
                    category = excluded.category,
                    capabilities = excluded.capabilities,
                    status = excluded.status,
                    source = excluded.source,
                    manifest = excluded.manifest,
                    fingerprint = excluded.fingerprint,
                    last_seen = excluded.last_seen,
                    removed_at = NULL,
                    updated_at = excluded.updated_at
                """,
                (
                    component_id, host_id, name, version, kind, path, entry,
                    config_schema, category, _json(capabilities or [], "[]"),
                    status, source, _json(manifest or {}, "{}"), fingerprint, now, now, now,
                ),
            )

    def list_components(self, host_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM components"
        params: List[Any] = []
        if host_id:
            sql += " WHERE host_id = ?"
            params.append(host_id)
        sql += " ORDER BY host_id, name"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["capabilities"] = _loads(data.get("capabilities"), [])
            data["manifest"] = _loads(data.get("manifest"), {})
            result.append(data)
        return result

    def delete_component(self, component_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM components WHERE component_id = ?", (component_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    def upsert_host(
        self,
        host_id: str,
        name: str,
        *,
        platform: str = "",
        internal_uid: str = "",
        root: str = "",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO hosts
                (host_id, name, platform, internal_uid, root, source, manifest, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(host_id) DO UPDATE SET
                    name = excluded.name,
                    platform = excluded.platform,
                    internal_uid = excluded.internal_uid,
                    root = excluded.root,
                    source = excluded.source,
                    manifest = excluded.manifest,
                    updated_at = excluded.updated_at
                """,
                (
                    host_id, name, platform, internal_uid, root, source,
                    _json(manifest or {}, "{}"), now, now,
                ),
            )

    def list_hosts(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM hosts ORDER BY updated_at DESC").fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["manifest"] = _loads(data.get("manifest"), {})
            result.append(data)
        return result
