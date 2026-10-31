# -*- coding: utf-8 -*-
"""用户画像：好感度与事实快照。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class ProfileOps:
    """用户画像：档案式的好感度与事实快照。"""

    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return None
        profile = dict(row)
        profile["facts"] = _loads(profile.get("facts"), [])
        profile["affection_history"] = _loads(profile.get("affection_history"), [])
        return profile

    def insert_profile(
        self,
        user_id: str,
        nickname: str,
        role_type: str,
        facts: List[str],
        affection: int,
        affection_history: List[Dict[str, Any]],
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, nickname, role_type, _json(facts, "[]"), time.time(),
                    affection, _json(affection_history, "[]"),
                ),
            )

    def set_profile_facts(self, user_id: str, facts: List[str]) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE user_profiles SET facts = ?, updated_at = ? WHERE user_id = ?",
                (_json(facts, "[]"), time.time(), user_id),
            )

    def get_affection(self, user_id: str) -> Optional[int]:
        with self._connect() as conn:
            row = conn.execute("SELECT affection FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row and row["affection"] is not None:
            return int(row["affection"])
        return None

    def update_affection(
        self,
        user_id: str,
        delta: int,
        reason: str = "",
        default: int = 50,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> int:
        """按档案的浮动窗口更新好感度。

        窗口来源优先级：显式传参 > archives.affection_min/max > 默认 0~100。
        到边界后 delta 无效，但历史仍记一条，方便观察"想加但加不动"。
        """
        now_ts = time.time()
        with self._tx() as conn:
            if min_value is None or max_value is None:
                arch = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (user_id,),
                ).fetchone()
                if arch:
                    if min_value is None:
                        min_value = int(arch["affection_min"] if arch["affection_min"] is not None else 0)
                    if max_value is None:
                        max_value = int(arch["affection_max"] if arch["affection_max"] is not None else 100)
                else:
                    if min_value is None:
                        min_value = 0
                    if max_value is None:
                        max_value = 100
            lo, hi = int(min_value), int(max_value)
            if lo > hi:
                lo, hi = hi, lo

            row = conn.execute(
                "SELECT affection, affection_history FROM user_profiles WHERE user_id = ?", (user_id,)
            ).fetchone()
            current = int(row["affection"]) if row and row["affection"] is not None else default
            history = _loads(row["affection_history"], []) if row else []
            new_value = min(hi, max(lo, current + int(delta)))
            history.append({
                "time": now_ts,
                "delta": int(delta),
                "old": current,
                "new": new_value,
                "reason": reason,
                "range": [lo, hi],
            })
            history = history[-50:]
            if row:
                conn.execute(
                    "UPDATE user_profiles SET affection = ?, affection_history = ?, updated_at = ? WHERE user_id = ?",
                    (new_value, _json(history, "[]"), now_ts, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                    VALUES (?, ?, 'member', '[]', ?, ?, ?)
                    """,
                    (user_id, f"用户_{user_id}", now_ts, new_value, _json(history, "[]")),
                )
            return new_value

    def set_affection(
        self,
        user_id: str,
        value: int,
        reason: str = "",
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ) -> int:
        """直接设定好感度（不累加），clamp 到档案窗口。"""
        now_ts = time.time()
        with self._tx() as conn:
            if min_value is None or max_value is None:
                arch = conn.execute(
                    "SELECT affection_min, affection_max FROM archives WHERE archive_id = ?",
                    (user_id,),
                ).fetchone()
                if arch:
                    if min_value is None:
                        min_value = int(arch["affection_min"] if arch["affection_min"] is not None else 0)
                    if max_value is None:
                        max_value = int(arch["affection_max"] if arch["affection_max"] is not None else 100)
                else:
                    if min_value is None:
                        min_value = 0
                    if max_value is None:
                        max_value = 100
            lo, hi = int(min_value), int(max_value)
            if lo > hi:
                lo, hi = hi, lo

            row = conn.execute(
                "SELECT affection, affection_history FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current = int(row["affection"]) if row and row["affection"] is not None else None
            history = _loads(row["affection_history"], []) if row else []
            new_value = min(hi, max(lo, int(value)))
            history.append({
                "time": now_ts,
                "delta": (new_value - current) if current is not None else None,
                "old": current,
                "new": new_value,
                "reason": reason,
                "range": [lo, hi],
            })
            history = history[-50:]
            if row:
                conn.execute(
                    "UPDATE user_profiles SET affection = ?, affection_history = ?, updated_at = ? WHERE user_id = ?",
                    (new_value, _json(history, "[]"), now_ts, user_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, nickname, role_type, facts, updated_at, affection, affection_history)
                    VALUES (?, ?, 'member', '[]', ?, ?, ?)
                    """,
                    (user_id, f"用户_{user_id}", now_ts, new_value, _json(history, "[]")),
                )
            return new_value
