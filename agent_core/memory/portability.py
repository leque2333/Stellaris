# -*- coding: utf-8 -*-
"""记忆库导出 / 一键导入。

原生格式保留事件、详情、事实、摘要和缓冲区；外部格式只做尽力转换，
不承诺无损。导入前一定先备份。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List

FORMAT = "stellaris.memory.v1"
MAX_IMPORT_CHARS = 50 * 1024 * 1024
MEMORY_TABLES = (
    "agent_timeline",
    "memory_details",
    "user_facts",
    "memory_digests",
    "timeline_buffer",
    "user_profiles",
)
EMBEDDING_COLUMNS = {"embedding"}


def _store(store=None):
    if store is not None:
        return store
    from ..storage import get_store

    return get_store()


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def export_bundle(store=None) -> Dict[str, Any]:
    store = _store(store)
    path = Path(store.path)
    payload: Dict[str, Any] = {
        "format": FORMAT,
        "exported_at": time.time(),
        "source": str(path),
        "tables": {},
    }
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        for table in MEMORY_TABLES:
            try:
                rows = []
                for row in con.execute(f"SELECT * FROM {table}"):
                    item = dict(row)
                    for col in EMBEDDING_COLUMNS:
                        item.pop(col, None)
                    rows.append(item)
                payload["tables"][table] = rows
            except sqlite3.Error:
                continue
    finally:
        con.close()
    return payload


def _backup(store) -> str:
    path = Path(store.path)
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"memory_before_import_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(str(path))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(target)


def _insert_rows(conn: sqlite3.Connection, table: str, rows: Iterable[Dict[str, Any]]) -> int:
    columns = _columns(conn, table)
    usable = [col for col in columns if col not in EMBEDDING_COLUMNS]
    if not usable:
        return 0
    sql = (
        f"INSERT OR IGNORE INTO {table} ({', '.join(usable)}) "
        f"VALUES ({', '.join('?' for _ in usable)})"
    )
    count = 0
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        values = [row.get(col) for col in usable]
        cur = conn.execute(sql, values)
        count += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    return count


def import_bundle(bundle: Dict[str, Any], mode: str = "merge", store=None) -> Dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        raise ValueError("不是 Stellaris 原生记忆库格式")
    tables = bundle.get("tables")
    if not isinstance(tables, dict):
        raise ValueError("缺少 tables")
    store = _store(store)
    backup = _backup(store)
    counts: Dict[str, int] = {}
    con = sqlite3.connect(str(store.path), timeout=20)
    try:
        with con:
            if mode == "replace":
                for table in MEMORY_TABLES:
                    con.execute(f"DELETE FROM {table}")
            for table in MEMORY_TABLES:
                rows = tables.get(table)
                if isinstance(rows, list):
                    counts[table] = _insert_rows(con, table, rows)
    finally:
        con.close()
    return {"ok": True, "mode": mode, "backup": backup, "counts": counts}


def _default_person(store) -> str:
    """导入的记忆默认挂在主人档案下，否则任何档案都召回不到（审计 B26）。"""
    try:
        from ..profile.archives import ArchiveManager

        return str(ArchiveManager(store).admin().get("archive_id") or "")
    except Exception:
        return ""


def _insert_events(store, records: Iterable[Dict[str, Any]], source: str,
                   person_id: str = "") -> Dict[str, Any]:
    backup = _backup(store)
    con = sqlite3.connect(str(store.path), timeout=20)
    inserted = 0
    now = time.time()
    person = str(person_id or "").strip() or _default_person(store)
    try:
        with con:
            for index, item in enumerate(records):
                text = str(item.get("content") or item.get("text") or "").strip()
                if not text:
                    continue
                timestamp = float(item.get("timestamp") or now)
                event_id = f"evt_import_{uuid.uuid4().hex[:12]}"
                role = str(item.get("role") or "import")
                narrative = f"{role}: {text[:20000]}"
                con.execute(
                    "INSERT OR IGNORE INTO agent_timeline "
                    "(event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative, "
                    "emotional_impact, importance, metadata, plugin_id, tag_primary, tag_secondary, "
                    "tag_permission, started_at, ended_at, visibility, person_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id, timestamp, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)),
                        source, person or "import", "导入记录", narrative, 0, 1.0,
                        json.dumps({"import_source": source, "role": role,
                                    "person_id": person}, ensure_ascii=False),
                        "import", "import", "import", "system", timestamp, timestamp, "private",
                        person or "import",
                    ),
                )
                inserted += 1
    finally:
        con.close()
    return {"ok": True, "mode": "merge", "backup": backup,
            "counts": {"agent_timeline": inserted}, "person_id": person}


def import_jsonl(store, content: str, person_id: str = "") -> Dict[str, Any]:
    records = []
    for line in str(content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return _insert_events(store, records, "import_jsonl", person_id=person_id)


def import_text(store, content: str, person_id: str = "") -> Dict[str, Any]:
    records = [
        {"role": "import", "text": part.strip()}
        for part in str(content or "").split("\n\n")
        if part.strip()
    ]
    return _insert_events(store, records, "import_text", person_id=person_id)


def import_chatgpt(store, content: str, person_id: str = "") -> Dict[str, Any]:
    data = json.loads(content)
    conversations = data if isinstance(data, list) else [data]
    records = []
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        title = str(conv.get("title") or "ChatGPT")
        mapping = conv.get("mapping") or {}
        if isinstance(mapping, dict):
            for node in mapping.values():
                msg = (node or {}).get("message") or {}
                author = (msg.get("author") or {}).get("role") or "import"
                content_obj = msg.get("content") or {}
                parts = content_obj.get("parts") if isinstance(content_obj, dict) else None
                text = " ".join(str(p) for p in (parts or []) if isinstance(p, (str, int, float)))
                if text.strip():
                    records.append({
                        "role": author,
                        "text": f"[{title}] {text.strip()}",
                        "timestamp": msg.get("create_time"),
                    })
    return _insert_events(store, records[:2000], "import_chatgpt", person_id=person_id)


def detect_format(content: str) -> str:
    text = str(content or "").strip()
    if not text:
        return "empty"
    # ChatGPT 导出是顶层数组，以 "[" 开头；只判断 "{" 会把它当纯文本（审计 B27）
    if text.startswith(("{", "[")):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("format") == FORMAT:
            return "native"
        if isinstance(data, list) and any(isinstance(x, dict) and "mapping" in x for x in data):
            return "chatgpt"
    first = text.splitlines()[0].strip()
    if first.startswith("{") and first.endswith("}"):
        return "jsonl"
    return "text"


def import_content(content: str, fmt: str = "auto", mode: str = "merge", store=None,
                   person_id: str = "") -> Dict[str, Any]:
    raw = str(content or "")
    if len(raw) > MAX_IMPORT_CHARS:
        raise ValueError("导入内容过大（上限 50MB）")
    store = _store(store)
    kind = detect_format(raw) if fmt in ("", "auto") else str(fmt)
    if kind == "native":
        return import_bundle(json.loads(raw), mode=mode, store=store)
    if kind == "jsonl":
        return import_jsonl(store, raw, person_id=person_id)
    if kind == "chatgpt":
        return import_chatgpt(store, raw, person_id=person_id)
    if kind == "text":
        return import_text(store, raw, person_id=person_id)
    raise ValueError(f"不支持导入格式: {kind}")


__all__ = [
    "FORMAT",
    "MEMORY_TABLES",
    "export_bundle",
    "import_bundle",
    "import_content",
    "detect_format",
]
