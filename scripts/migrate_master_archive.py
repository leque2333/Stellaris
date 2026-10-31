# -*- coding: utf-8 -*-
"""把管理员档案从“QQ 号”迁到独立 archive_id。

用法：
    python scripts/migrate_master_archive.py [--data-dir D:\\Stellaris_test\\data]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.identity import IdentityResolver  # noqa: E402
from agent_core.storage.sqlite_store import SQLiteStore  # noqa: E402


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def _update(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    try:
        return conn.execute(sql, params).rowcount
    except Exception:
        return 0


def migrate_db(data_dir: Path, old_id: str, new_id: str) -> None:
    db_path = data_dir / "memory.db"
    if not db_path.exists():
        print(f"skip missing {db_path}")
        return
    SQLiteStore(db_path)  # apply schema migrations (archive_aliases)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "archives"):
            print("skip: no archives table")
            return
        old = conn.execute("SELECT * FROM archives WHERE archive_id=?", (old_id,)).fetchone()
        new = conn.execute("SELECT * FROM archives WHERE archive_id=?", (new_id,)).fetchone()
        if new:
            print(f"already migrated: {new_id}")
        elif old:
            with conn:
                conn.execute(
                    "UPDATE archives SET archive_id=?, updated_at=? WHERE archive_id=?",
                    (new_id, time.time(), old_id),
                )
                _update(conn, "UPDATE archive_identities SET archive_id=? WHERE archive_id=?", (new_id, old_id))
                _update(conn, "UPDATE agent_timeline SET person_id=? WHERE person_id=?", (new_id, old_id))
                _update(conn, "UPDATE memory_facts SET subject_id=? WHERE subject_id=?", (new_id, old_id))
                _update(conn, "UPDATE user_profiles SET user_id=? WHERE user_id=?", (new_id, old_id))
                _update(conn, "UPDATE user_facts SET user_id=? WHERE user_id=?", (new_id, old_id))
                _update(conn, "UPDATE platform_bindings SET archive_id=? WHERE archive_id=?", (new_id, old_id))
                _update(conn, "UPDATE platform_contexts SET archive_id=? WHERE archive_id=?", (new_id, old_id))
                if _table_exists(conn, "archive_aliases"):
                    conn.execute(
                        "INSERT OR REPLACE INTO archive_aliases (alias_id, archive_id, created_at) VALUES (?, ?, ?)",
                        (old_id, new_id, time.time()),
                    )
            print(f"memory.db: {old_id} -> {new_id}")
        else:
            print(f"no old archive {old_id}; nothing to rename")

        # 确保管理员身份仍存在并指向新 ID
        if _table_exists(conn, "archive_identities"):
            with conn:
                conn.execute(
                    "UPDATE archive_identities SET archive_id=? WHERE platform='qq' AND platform_user_id=?",
                    (new_id, old_id),
                )
                for platform, uid in (
                    ("qq", old_id),
                    ("bilibili", os.getenv("MASTER_BILIBILI_ID", "<B站UID>")),
                ):
                    if uid:
                        conn.execute(
                            "INSERT OR IGNORE INTO archive_identities "
                            "(identity_id, archive_id, platform, platform_user_id, display_name, is_primary, confidence, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, 1.0, ?)",
                            (
                                f"ident_{platform}_{uid}",
                                new_id,
                                platform,
                                uid,
                                "主人" if platform == "qq" else "主人2333",
                                1 if platform == "qq" else 0,
                                time.time(),
                            ),
                        )
    finally:
        conn.close()


def migrate_graph(data_dir: Path, old_id: str, new_id: str) -> None:
    path = data_dir / "memory_graph.db"
    if not path.exists():
        print(f"skip missing {path}")
        return
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        with conn:
            _update(conn, "UPDATE graph_edges SET subject_id=? WHERE subject_id=?", (new_id, old_id))
            _update(
                conn,
                "UPDATE graph_nodes SET person_id=?, name=? WHERE entity_type='person' AND (person_id=? OR name=?)",
                (new_id, new_id, old_id, old_id),
            )
        print(f"memory_graph.db: {old_id} -> {new_id}")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--old-id", default=IdentityResolver.get_master_qq())
    parser.add_argument("--new-id", default=os.getenv("HUB_MASTER_ARCHIVE_ID", "arc_master"))
    args = parser.parse_args()
    data_dir = Path(args.data_dir)
    print(f"data_dir={data_dir} old={args.old_id} new={args.new_id}")
    migrate_db(data_dir, args.old_id, args.new_id)
    migrate_graph(data_dir, args.old_id, args.new_id)


if __name__ == "__main__":
    main()
