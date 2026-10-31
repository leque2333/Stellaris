# -*- coding: utf-8 -*-
"""插件 / 桥接 / 服务：外部能力注册表。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class PluginOps:
    """插件 / 桥接 / 服务：外部能力注册表。"""

    def upsert_plugin(
        self,
        plugin_id: str,
        *,
        name: str = "",
        root_path: str = "",
        protocol: str = "",
        spec: Optional[Dict[str, Any]] = None,
        status: str = "active",
        source: str = "",
        last_error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            row = conn.execute(
                "SELECT plugin_id FROM plugins WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE plugins
                       SET name = COALESCE(NULLIF(?, ''), name),
                           root_path = COALESCE(NULLIF(?, ''), root_path),
                           protocol = COALESCE(NULLIF(?, ''), protocol),
                           spec_json = ?, status = ?, source = ?,
                           last_error = ?, metadata = ?, updated_at = ?
                     WHERE plugin_id = ?
                    """,
                    (
                        name, root_path, protocol, _json(spec or {}, "{}"),
                        status, source, last_error, _json(metadata or {}, "{}"),
                        now, plugin_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO plugins
                    (plugin_id, name, root_path, protocol, spec_json, status,
                     source, last_error, metadata, discovered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plugin_id, name or plugin_id, root_path, protocol,
                        _json(spec or {}, "{}"), status, source, last_error,
                        _json(metadata or {}, "{}"), now, now,
                    ),
                )
        return plugin_id

    def list_plugins(
        self,
        *,
        protocol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conditions: List[str] = []
        params: List[Any] = []
        if protocol:
            conditions.append("protocol = ?")
            params.append(protocol)
        if status:
            conditions.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM plugins {where} ORDER BY updated_at DESC",
                tuple(params),
            ).fetchall()
        return [self._plugin_row(r) for r in rows]

    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM plugins WHERE plugin_id = ?", (plugin_id,)
            ).fetchone()
        return self._plugin_row(row) if row else None

    def delete_plugin(self, plugin_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM plugins WHERE plugin_id = ?", (plugin_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    @staticmethod
    def _plugin_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["spec"] = _loads(data.pop("spec_json", "{}"), {})
        data["metadata"] = _loads(data.get("metadata"), {})
        return data

    def mark_ingested(self, event_key: str, source: str = "", event_id: str = "") -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO ingested_events (event_key, source, event_id, ingested_at) "
                "VALUES (?, ?, ?, ?)",
                (event_key, source, event_id, time.time()),
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    def get_bridge_cursor(self, source_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM bridge_cursors WHERE source_id = ?", (source_id,)
            ).fetchone()
        return row["cursor"] if row else None

    def set_bridge_cursor(self, source_id: str, cursor: str) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO bridge_cursors (source_id, cursor, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (source_id, str(cursor), time.time()),
            )

    def upsert_bridge_spec(
        self, spec: Dict[str, Any], *, auto_start: bool = False, status: str = "stopped"
    ) -> str:
        source_id = str(spec.get("source_id") or "")
        if not source_id:
            raise ValueError("source_id 必填")
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO bridge_specs
                (source_id, kind, scope, locator, options_json, status, auto_start, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind = excluded.kind,
                    scope = excluded.scope,
                    locator = excluded.locator,
                    options_json = excluded.options_json,
                    status = excluded.status,
                    auto_start = excluded.auto_start,
                    updated_at = excluded.updated_at
                """,
                (
                    source_id, str(spec.get("kind") or ""), str(spec.get("scope") or ""),
                    str(spec.get("locator") or ""), _json(spec.get("options") or {}, "{}"),
                    status, 1 if auto_start else 0, now, now,
                ),
            )
        return source_id

    def list_bridge_specs(self, *, auto_start: Optional[bool] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if auto_start is None:
                rows = conn.execute("SELECT * FROM bridge_specs ORDER BY updated_at DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM bridge_specs WHERE auto_start = ? ORDER BY updated_at DESC",
                    (1 if auto_start else 0,),
                ).fetchall()
        return [self._bridge_spec_row(r) for r in rows]

    def get_bridge_spec(self, source_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bridge_specs WHERE source_id = ?", (source_id,)
            ).fetchone()
        return self._bridge_spec_row(row) if row else None

    def delete_bridge_spec(self, source_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM bridge_specs WHERE source_id = ?", (source_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    def set_bridge_spec_status(
        self, source_id: str, status: str, *, auto_start: Optional[bool] = None
    ) -> None:
        with self._tx() as conn:
            if auto_start is None:
                conn.execute(
                    "UPDATE bridge_specs SET status = ?, updated_at = ? WHERE source_id = ?",
                    (status, time.time(), source_id),
                )
            else:
                conn.execute(
                    "UPDATE bridge_specs SET status = ?, auto_start = ?, updated_at = ? WHERE source_id = ?",
                    (status, 1 if auto_start else 0, time.time(), source_id),
                )

    @staticmethod
    def _bridge_spec_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["options"] = _loads(data.pop("options_json", "{}"), {})
        data["auto_start"] = bool(data.get("auto_start"))
        return data

    def upsert_service(
        self,
        service_id: str,
        *,
        name: str = "",
        kind: str = "",
        endpoint: str = "",
        auth_token: str = "",
        capabilities: Optional[List[str]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        status: str = "active",
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO services
                (service_id, name, kind, endpoint, auth_token, capabilities,
                 manifest, status, last_seen, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(service_id) DO UPDATE SET
                    name = COALESCE(NULLIF(excluded.name, ''), name),
                    kind = COALESCE(NULLIF(excluded.kind, ''), kind),
                    endpoint = COALESCE(NULLIF(excluded.endpoint, ''), endpoint),
                    auth_token = COALESCE(NULLIF(excluded.auth_token, ''), auth_token),
                    capabilities = excluded.capabilities,
                    manifest = excluded.manifest,
                    status = excluded.status,
                    last_seen = excluded.last_seen,
                    updated_at = excluded.updated_at
                """,
                (
                    service_id, name, kind, endpoint, auth_token,
                    _json(capabilities or [], "[]"), _json(manifest or {}, "{}"),
                    status, now, now, now,
                ),
            )
        return service_id

    def list_services(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM services WHERE status = ? ORDER BY updated_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM services ORDER BY updated_at DESC").fetchall()
        return [self._service_row(r) for r in rows]

    def get_service(self, service_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM services WHERE service_id = ?", (service_id,)
            ).fetchone()
        return self._service_row(row) if row else None

    def delete_service(self, service_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute("DELETE FROM services WHERE service_id = ?", (service_id,))
            return bool(cur.rowcount and cur.rowcount > 0)

    def touch_service(self, service_id: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE services SET last_seen = ?, updated_at = ? WHERE service_id = ?",
                (time.time(), time.time(), service_id),
            )

    @staticmethod
    def _service_row(row) -> Dict[str, Any]:
        data = dict(row)
        data["capabilities"] = _loads(data.get("capabilities"), [])
        data["manifest"] = _loads(data.get("manifest"), {})
        return data
