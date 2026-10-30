# -*- coding: utf-8 -*-
"""
Stellaris Core - 存储层入口
业务代码统一通过 get_store() 获取 Store，不直接依赖 sqlite3。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .config import StorageConfig, default_db_path, find_hub_root, load_storage_config, parse_storage_url
from .repositories import Store
from .schema import apply_migrations, classify_source, current_version
from .sqlite_store import SQLiteStore

DEFAULT_DB_PATH = default_db_path()

_LOCK = threading.Lock()
_STORES: dict[tuple[str, bool], Store] = {}


def create_store(
    db_path: Optional[Path] = None,
    *,
    read_only: bool = False,
    migrate: bool = True,
) -> Store:
    """显式创建一个独立 Store（测试、多库场景）"""
    from .sqlite_store import SQLiteStore

    config = load_storage_config() if db_path is None else None
    if db_path is None and config and config.backend != "sqlite":
        raise NotImplementedError(f"storage backend not implemented: {config.backend}")
    path = Path(db_path) if db_path is not None else (config.db_path if config else DEFAULT_DB_PATH)
    return SQLiteStore(path, read_only=read_only, migrate=migrate)


def get_store(db_path: Optional[Path] = None, *, read_only: bool = False, reload: bool = False) -> Store:
    """获取进程内共享 Store（默认走 HUB_STORAGE_URL / data/memory.db）"""
    path = Path(db_path) if db_path is not None else None
    if path is None:
        config = load_storage_config()
        if config.backend != "sqlite":
            raise NotImplementedError(f"storage backend not implemented: {config.backend}")
        path = config.db_path
        read_only = read_only or config.read_only
    key = (str(path.resolve()), bool(read_only))
    with _LOCK:
        if reload or key not in _STORES:
            _STORES[key] = create_store(path, read_only=read_only)
        return _STORES[key]


def reset_stores() -> None:
    """测试用：清空缓存"""
    with _LOCK:
        for store in _STORES.values():
            try:
                store.close()
            except Exception:
                pass
        _STORES.clear()


__all__ = [
    "DEFAULT_DB_PATH",
    "StorageConfig",
    "Store",
    "apply_migrations",
    "classify_source",
    "create_store",
    "current_version",
    "default_db_path",
    "find_hub_root",
    "get_store",
    "load_storage_config",
    "parse_storage_url",
    "reset_stores",
]
