# -*- coding: utf-8 -*-
"""
Stellaris Core - 存储兼容层
历史上这里直接实现 SQLite DAL；现已迁移到 agent_core.storage，
本文件只保留旧导入路径，避免系统各处改动。
"""

from ..storage import DEFAULT_DB_PATH, SQLiteStore, find_hub_root, get_store

# 兼容旧命名：StorageDAL(db_path) 等价于 SQLiteStore(db_path)
StorageDAL = SQLiteStore


def resolve_default_db_path():
    return DEFAULT_DB_PATH


__all__ = ["DEFAULT_DB_PATH", "StorageDAL", "find_hub_root", "get_store", "resolve_default_db_path"]
