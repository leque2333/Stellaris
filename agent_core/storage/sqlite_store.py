# -*- coding: utf-8 -*-
"""
Stellaris Core - SQLite 存储门面
连接与事务留在这里，具体 SQL 按领域放在 repos/ 下。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .config import default_db_path
from .repos.archives import ArchiveOps
from .repos.buffer import BufferOps
from .repos.components import ComponentOps
from .repos.contexts import ContextOps
from .repos.facts import FactOps
from .repos.invocations import InvocationOps
from .repos.knowledge import KnowledgeOps
from .repos.ledger import LedgerOps
from .repos.plugins import PluginOps
from .repos.profiles import ProfileOps
from .repos.timeline import TimelineOps
from .schema import apply_migrations


class SQLiteStore(TimelineOps, ProfileOps, FactOps, InvocationOps, ArchiveOps, ContextOps, PluginOps, KnowledgeOps, ComponentOps, LedgerOps, BufferOps):
    """SQLite 后端：连接与事务在这里，具体 SQL 按领域放在 repos/ 下。"""

    def __init__(self, path: Optional[Path] = None, *, read_only: bool = False, migrate: bool = True):
        self.path = Path(path) if path else default_db_path()
        self.read_only = read_only
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if migrate:
                apply_migrations(self.path)
        elif not self.path.exists():
            raise FileNotFoundError(f"storage not found: {self.path}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            conn = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, timeout=15.0)
        else:
            conn = sqlite3.connect(str(self.path), timeout=15.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def close(self) -> None:
        # 连接按需创建，无需常驻连接池；保留接口供未来后端使用
        return None

    @property
    def db_path(self) -> Path:
        return self.path

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def schema_version(self) -> int:
        with self._connect() as conn:
            try:
                row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                return int(row[0] or 0) if row else 0
            except sqlite3.OperationalError:
                return 0
