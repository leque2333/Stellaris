# -*- coding: utf-8 -*-
"""时间轴缓冲区：被检索顶出去的近期记忆先存这里，轮次结束后回填。

为什么不用纯内存：进程重启后缓冲要还在，否则回填就断了。
为什么不用时间轴表本身：缓冲区里的条目已经不在视图里，必须单独标记。
"""

from typing import Any, Dict, List, Optional

from ..jsonutil import _json, _loads


class BufferOps:
    def push_buffer(self, person_id: str, events: List[Dict[str, Any]]) -> int:
        """把被腾出的条目按顺序写入缓冲区；返回写入条数。"""
        import time

        if not events:
            return 0
        now = float(time.time())
        with self._tx() as conn:
            for event in events:
                conn.execute(
                    "INSERT INTO timeline_buffer (person_id, event_id, payload, released_at) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        str(person_id),
                        str(event.get("event_id") or ""),
                        _json(event, "{}"),
                        now,
                    ),
                )
        return len(events)

    def peek_buffer(self, person_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """按写入顺序（先进先出）看，不删除。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM timeline_buffer WHERE person_id = ? ORDER BY id ASC LIMIT ?",
                (str(person_id), int(limit)),
            ).fetchall()
        return [self._buffer_row(row) for row in rows]

    def pop_buffer(self, person_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """取出并删除（回填用）。"""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, payload FROM timeline_buffer WHERE person_id = ? ORDER BY id ASC LIMIT ?",
                (str(person_id), int(limit)),
            ).fetchall()
            if not rows:
                return []
            ids = [int(row["id"]) for row in rows]
            conn.execute(
                f"DELETE FROM timeline_buffer WHERE id IN ({','.join('?' for _ in ids)})",
                tuple(ids),
            )
        return [self._buffer_row(row) for row in rows]

    def buffer_stats(self, person_id: str) -> Dict[str, Any]:
        """缓冲条目数与总字数（字数按 payload 里的 narrative 算）。"""
        events = self.peek_buffer(person_id, limit=10_000)
        chars = sum(len(str(item.get("narrative") or "")) for item in events)
        return {"events": len(events), "chars": chars}

    def clear_buffer(self, person_id: str) -> int:
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM timeline_buffer WHERE person_id = ?", (str(person_id),)
            )
            return int(cur.rowcount or 0)

    @staticmethod
    def _buffer_row(row) -> Dict[str, Any]:
        payload: Optional[Dict[str, Any]] = _loads(row["payload"], {})
        return payload if isinstance(payload, dict) else {}

    def release_tail(self, person_id: str, keep_chars: int, limit: int = 5000) -> int:
        """把超出 keep_chars 的旧事件从时间轴搬进缓冲区（时间轴因此有界）。"""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT event_id, timestamp, source_module, actor_id, actor_name, narrative, "
                "time_str, importance, detail_id, person_id, metadata, embedding "
                "FROM agent_timeline WHERE person_id = ? ORDER BY timestamp DESC LIMIT ?",
                (str(person_id), int(limit)),
            ).fetchall()
            keep, drop = self._split_by_chars(rows, keep_chars)
            if not drop:
                return 0
            now = __import__("time").time()
            for row in drop:
                payload = {key: row[key] for key in row.keys()}
                payload["embedding"] = None  # 二进制不进 JSON
                conn.execute(
                    "INSERT INTO timeline_buffer (person_id, event_id, payload, released_at) "
                    "VALUES (?, ?, ?, ?)",
                    (str(person_id), str(row["event_id"]), _json(payload, "{}"), float(now)),
                )
                # 软归档：不删行，避免触发 FTS 删除触发器（历史索引不一致会报 malformed）
                conn.execute(
                    "UPDATE agent_timeline SET visibility = 'archived' WHERE event_id = ?",
                    (row["event_id"],),
                )
        return len(drop)

    def restore_buffer(self, person_id: str, limit: int = 50) -> int:
        """把缓冲区里的记忆接回时间轴；返回接回条数。"""
        restored = 0
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id, payload FROM timeline_buffer WHERE person_id = ? ORDER BY id ASC LIMIT ?",
                (str(person_id), int(limit)),
            ).fetchall()
            for row in rows:
                payload = _loads(row["payload"], {})
                if not isinstance(payload, dict) or not payload.get("event_id"):
                    continue
                conn.execute(
                    "UPDATE agent_timeline SET visibility = 'private' WHERE event_id = ?",
                    (payload.get("event_id"),),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO agent_timeline "
                    "(event_id, timestamp, source_module, actor_id, actor_name, narrative, time_str, "
                    "importance, detail_id, person_id, metadata, visibility) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'private')",
                    (
                        payload.get("event_id"), float(payload.get("timestamp") or 0),
                        payload.get("source_module") or "", payload.get("actor_id") or "",
                        payload.get("actor_name") or "", payload.get("narrative") or "",
                        payload.get("time_str") or "", float(payload.get("importance") or 1.0),
                        payload.get("detail_id") or "", payload.get("person_id") or person_id,
                        _json(payload.get("metadata") or {}, "{}"),
                    ),
                )
                conn.execute("DELETE FROM timeline_buffer WHERE id = ?", (int(row["id"]),))
                restored += 1
        return restored

    @staticmethod
    def _split_by_chars(rows, keep_chars: int):
        """按字数切：前面的留、后面的搬。"""
        keep, drop, used = [], [], 0
        limit = max(0, int(keep_chars))
        for index, row in enumerate(rows):
            size = len(str(row["narrative"] or ""))
            if used + size <= limit:
                keep.append(row)
                used += size
                continue
            drop = list(rows[index:])  # 放不下就停，剩下的全部搬走
            break
        return keep, drop
