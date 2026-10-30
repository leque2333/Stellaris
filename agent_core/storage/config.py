# -*- coding: utf-8 -*-
"""
Stellaris Core - 存储配置与路径解析
将「数据库放在哪、用什么后端、是否只读」从业务代码里抽离出来。
默认仍为 SQLite，可通过 HUB_STORAGE_URL 切换到其他后端（预留）。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def find_hub_root() -> Path:
    """自动探测全局中枢系统的最顶层根目录，实现所有 AI 模块零配置统合"""
    env_root = os.getenv("AGENT_HUB_ROOT")
    if env_root and Path(env_root).exists():
        return Path(env_root)
    if os.name != "nt" and Path("/.dockerenv").exists():
        return Path("/app")

    curr = Path(__file__).resolve()
    for p in [curr] + list(curr.parents):
        if (p / "console").exists() and (p / "data").exists():
            return p
    return Path(__file__).resolve().parent.parent.parent


def default_db_path() -> Path:
    """默认记忆中枢数据库路径（单文件 SQLite）"""
    env_db = os.getenv("MEMORY_HUB_DB_PATH")
    if env_db:
        return Path(env_db)
    if os.name != "nt" and Path("/.dockerenv").exists():
        return Path("/app/data/memory.db")
    return find_hub_root() / "data" / "memory.db"


@dataclass(frozen=True)
class StorageConfig:
    """存储配置：目前只实现 sqlite，url 已按可替换方式设计"""

    url: str
    backend: str = "sqlite"
    path: Path | None = None
    read_only: bool = False

    @property
    def db_path(self) -> Path:
        if self.path is None:
            raise ValueError("sqlite backend requires a path")
        return self.path


def parse_storage_url(url: str) -> StorageConfig:
    """解析 HUB_STORAGE_URL。

    支持：
      - sqlite:///D:/data/memory.db
      - sqlite:///D:/data/memory.db?mode=ro
      - 直接给绝对路径 D:/data/memory.db
      - 预留：postgresql://user:pass@host/db（尚未实现）
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty storage url")

    if raw.startswith("sqlite://"):
        remember = raw[len("sqlite://"):]
        path_part, _, query = remember.partition("?")
        # sqlite:///D:/x -> /D:/x ; sqlite:///:memory: -> /:memory:
        if path_part.startswith("/") and re.match(r"^/[A-Za-z]:", path_part):
            path_part = path_part[1:]
        read_only = "mode=ro" in query or "immutable=1" in query
        return StorageConfig(
            url=raw,
            backend="sqlite",
            path=Path(path_part),
            read_only=read_only,
        )

    if raw.startswith(("postgres://", "postgresql://", "mysql://")):
        return StorageConfig(url=raw, backend=raw.split(":", 1)[0], path=None)

    # 纯路径
    return StorageConfig(url=f"sqlite:///{Path(raw).as_posix()}", backend="sqlite", path=Path(raw))


def load_storage_config() -> StorageConfig:
    url = os.getenv("HUB_STORAGE_URL")
    if url:
        return parse_storage_url(url)
    path = default_db_path()
    return StorageConfig(url=f"sqlite:///{path.as_posix()}", backend="sqlite", path=path)
