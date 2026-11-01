# -*- coding: utf-8 -*-
"""档案：人物档案、身份绑定、自定义设定。"""

from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from ..jsonutil import _json


class ArchiveOps:
    """档案：人物档案、身份绑定、自定义设定。"""

    def ensure_archive(
        self,
        archive_id: str,
        role: str = "user",
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        now = time.time()
        # 档案是人物，不再承载 AI 人设；旧列为兼容保留，永远写空
        persona = ""
        sections_json = "[]"
        with self._tx() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO archives
                (archive_id, role, display_name, status, created_at, updated_at, metadata, persona_id, persona_sections)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    archive_id, role, display_name or archive_id, now, now,
                    _json(metadata or {}, "{}"), persona, sections_json,
                ),
            )

    def get_archive(self, archive_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM archives WHERE archive_id = ?", (archive_id,)).fetchone()
        return dict(row) if row else None

    def list_archives(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if role:
                rows = conn.execute(
                    "SELECT * FROM archives WHERE role = ? ORDER BY created_at ASC", (role,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM archives ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def link_identity(
        self,
        archive_id: str,
        platform: str,
        platform_user_id: str,
        *,
        display_name: Optional[str] = None,
        is_primary: bool = False,
        confidence: float = 1.0,
    ) -> str:
        now = time.time()
        with self._tx() as conn:
            existing = conn.execute(
                "SELECT identity_id FROM archive_identities WHERE platform = ? AND platform_user_id = ?",
                (platform, str(platform_user_id)),
            ).fetchone()
            if existing:
                return existing["identity_id"]
            identity_id = f"id_{platform}_{uuid.uuid4().hex[:8]}"
            conn.execute(
                """
                INSERT INTO archive_identities
                (identity_id, archive_id, platform, platform_user_id, display_name,
                 is_primary, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    identity_id, archive_id, platform, str(platform_user_id),
                    display_name, 1 if is_primary else 0, float(confidence), now,
                ),
            )
        return identity_id

    def unlink_identity(self, platform: str, platform_user_id: str) -> bool:
        """解绑一个平台身份；返回是否真的删掉了（console 的「改回不配置」）。"""
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM archive_identities WHERE platform = ? AND platform_user_id = ?",
                (str(platform), str(platform_user_id)),
            )
        return bool(cur.rowcount)

    def find_archive_by_identity(self, platform: str, platform_user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT a.* FROM archives a
                JOIN archive_identities i ON i.archive_id = a.archive_id
                WHERE i.platform = ? AND i.platform_user_id = ?
                LIMIT 1
                """,
                (platform, str(platform_user_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_identities(self, archive_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archive_identities WHERE archive_id = ? ORDER BY created_at ASC",
                (archive_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_archive(
        self,
        archive_id: str,
        *,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
        affection_min: Optional[int] = None,
        affection_max: Optional[int] = None,
    ) -> None:
        fields: List[str] = []
        params: List[Any] = []
        if display_name is not None:
            fields.append("display_name = ?")
            params.append(display_name)
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if affection_min is not None or affection_max is not None:
            # 读取当前窗口，合并后统一校验
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (archive_id,),
                ).fetchone()
            cur_min = int(row["affection_min"]) if row and row["affection_min"] is not None else 0
            cur_max = int(row["affection_max"]) if row and row["affection_max"] is not None else 100
            lo = int(affection_min) if affection_min is not None else cur_min
            hi = int(affection_max) if affection_max is not None else cur_max
            lo = max(0, min(100, lo))
            hi = max(0, min(100, hi))
            if lo > hi:
                raise ValueError("affection_min 不能大于 affection_max")
            fields.append("affection_min = ?")
            params.append(lo)
            fields.append("affection_max = ?")
            params.append(hi)
        if not fields:
            return
        fields.append("updated_at = ?")
        params.append(time.time())
        params.append(archive_id)
        with self._tx() as conn:
            conn.execute(f"UPDATE archives SET {', '.join(fields)} WHERE archive_id = ?", tuple(params))

    def list_archive_settings(self, archive_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM archive_settings WHERE archive_id = ? "
                "ORDER BY sort_order ASC, created_at ASC",
                (archive_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_archive_setting(
        self,
        archive_id: str,
        label: str,
        content: str,
        *,
        setting_id: str = "",
        sort_order: Optional[int] = None,
    ) -> Dict[str, Any]:
        label = str(label or "").strip()
        content = str(content or "").strip()
        if not label:
            raise ValueError("label 不能为空")
        now = time.time()
        with self._tx() as conn:
            existing = None
            if setting_id:
                existing = conn.execute(
                    "SELECT * FROM archive_settings WHERE setting_id = ? AND archive_id = ?",
                    (setting_id, archive_id),
                ).fetchone()
            if existing:
                order = int(existing["sort_order"] or 0) if sort_order is None else int(sort_order)
                conn.execute(
                    "UPDATE archive_settings SET label = ?, content = ?, sort_order = ?, updated_at = ? "
                    "WHERE setting_id = ?",
                    (label, content, order, now, setting_id),
                )
                sid = setting_id
            else:
                sid = setting_id or f"set_{uuid.uuid4().hex[:10]}"
                if sort_order is None:
                    row = conn.execute(
                        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS nxt FROM archive_settings WHERE archive_id = ?",
                        (archive_id,),
                    ).fetchone()
                    order = int(row["nxt"] or 1)
                else:
                    order = int(sort_order)
                conn.execute(
                    "INSERT INTO archive_settings "
                    "(setting_id, archive_id, label, content, sort_order, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, archive_id, label, content, order, now, now),
                )
        return {
            "setting_id": sid, "archive_id": archive_id, "label": label,
            "content": content, "sort_order": order, "created_at": now, "updated_at": now,
        }

    def delete_archive_setting(self, archive_id: str, setting_id: str) -> bool:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM archive_settings WHERE setting_id = ? AND archive_id = ?",
                (setting_id, archive_id),
            )
            return cur.rowcount > 0
