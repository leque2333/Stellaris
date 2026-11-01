# -*- coding: utf-8 -*-
"""记忆库快照：列出 / 手动备份 / 恢复 / 删除（全部手动，不做自动清理）。

快照是**整库副本**（`data/memory.db`），不是单独的时间轴或 L3：
- 迁移前：`backups/pre_migrate_*_memory.db`（schema.py 生成）
- 导入前：`data/backups/memory_before_import_*.db`（portability.py 生成）
- 恢复前：`backups/pre_restore_*_memory.db`（本模块生成）
- 手动：`backups/manual_*_memory.db`（本模块生成）

恢复走 SQLite 在线备份 API（覆盖当前库），恢复前会先给当前库再存一份
`pre_restore_*`；`memory_graph.db` 不随快照恢复，为空时会从 user_facts 重建。
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

KIND_LABELS = {
    "migration": "迁移前",
    "import": "导入前",
    "restore": "恢复前",
    "manual": "手动",
}

# 只认这些前缀，避免把备份目录里别的 .db 当成快照
_KINDS = (
    ("pre_migrate_", "migration"),
    ("memory_before_import_", "import"),
    ("pre_restore_", "restore"),
    ("manual_", "manual"),
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.db$")


class SnapshotError(ValueError):
    """快照不存在 / 名字非法 / 文件损坏。"""


def _db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    from ..storage import get_store

    return Path(get_store().path)


def snapshot_dirs(db_path: Optional[Path] = None) -> List[Path]:
    """迁移/手动/恢复快照目录 + 导入快照目录（去重）。"""
    db = _db_path(db_path)
    migration = Path(os.getenv("HUB_SNAPSHOT_DIR", "").strip() or (db.parent.parent / "backups"))
    imports = Path(os.getenv("HUB_IMPORT_BACKUP_DIR", "").strip() or (db.parent / "backups"))
    result: List[Path] = []
    for path in (migration, imports):
        resolved = path.resolve()
        if resolved not in result:
            result.append(resolved)
    return result


def _kind_of(name: str) -> Optional[str]:
    for prefix, kind in _KINDS:
        if name.startswith(prefix):
            return kind
    return None


def _human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _schema_version(path: Path) -> Optional[int]:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    except Exception:
        return None
    try:
        value = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        if not value:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            value = int(row[0] or 0) if row else 0
        return value or None
    except Exception:
        return None
    finally:
        conn.close()


def _info(path: Path, kind: str) -> Dict[str, Any]:
    stat = path.stat()
    created = float(stat.st_mtime)
    return {
        "name": path.name,
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "path": str(path),
        "size": int(stat.st_size),
        "size_text": _human_size(int(stat.st_size)),
        "created_at": created,
        "created_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created)),
        "schema_version": _schema_version(path),
    }


def list_snapshots(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """所有可识别的快照，按时间从新到旧。"""
    items: List[Dict[str, Any]] = []
    seen = set()
    for folder in snapshot_dirs(db_path):
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() != ".db":
                continue
            kind = _kind_of(path.name)
            if not kind:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                items.append(_info(path, kind))
            except OSError:
                continue
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


def total_bytes(db_path: Optional[Path] = None) -> int:
    return sum(item["size"] for item in list_snapshots(db_path))


def _resolve(name: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    text = str(name or "").strip()
    if not text or not _SAFE_NAME.match(text) or "/" in text or "\\" in text or ".." in text:
        raise SnapshotError("非法的快照名")
    for item in list_snapshots(db_path):
        if item["name"] == text:
            return item
    raise SnapshotError("快照不存在")


def _copy(src_path: Path, dst_path: Path) -> None:
    """用 SQLite 在线备份 API 做一致性拷贝（含 WAL）。"""
    src = sqlite3.connect(f"file:{src_path.as_posix()}?mode=ro", uri=True, timeout=15)
    dst = sqlite3.connect(str(dst_path), timeout=15)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _new_name(prefix: str, db: Path) -> Path:
    folder = snapshot_dirs(db)[0]
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = folder / f"{prefix}_{stamp}_{db.stem}.db"
    index = 1
    while target.exists():
        target = folder / f"{prefix}_{stamp}_{index}_{db.stem}.db"
        index += 1
    return target


def create_snapshot(kind: str = "manual", db_path: Optional[Path] = None) -> Dict[str, Any]:
    """给当前库做一份整库快照；kind 只允许 manual / restore。"""
    db = _db_path(db_path)
    if not db.is_file():
        raise SnapshotError("当前记忆库不存在")
    prefix = "pre_restore" if kind == "restore" else "manual"
    target = _new_name(prefix, db)
    try:
        _copy(db, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise SnapshotError(f"备份失败：{type(exc).__name__}: {exc}") from exc
    return _info(target, "restore" if kind == "restore" else "manual")


def _ensure_valid_snapshot(path: Path) -> None:
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5)
    except Exception as exc:
        raise SnapshotError(f"快照打不开：{exc}") from exc
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'agent_timeline'"
        ).fetchone()
        if not row:
            raise SnapshotError("不是有效的 Stellaris 记忆库快照")
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError(f"快照校验失败：{exc}") from exc
    finally:
        conn.close()


def restore_snapshot(name: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """把某个快照覆盖回当前库；返回 {restored, safety}。"""
    db = _db_path(db_path)
    item = _resolve(name, db_path)
    source = Path(item["path"])
    _ensure_valid_snapshot(source)

    safety = create_snapshot("restore", db_path=db)
    try:
        _copy(source, db)
    except Exception as exc:
        try:
            _copy(Path(safety["path"]), db)
        except Exception:
            pass
        raise SnapshotError(f"恢复失败，已尝试回滚：{type(exc).__name__}: {exc}") from exc

    # 恢复的是旧库：把 schema 迁到当前版本，并让下次 FactStore 重新建 FTS/图
    try:
        from ..storage import reset_stores
        from ..storage.schema import apply_migrations

        apply_migrations(db)
        reset_stores()
    except Exception:
        pass
    try:
        from . import facts

        facts.reset_ready_cache()
    except Exception:
        pass

    # 图库是派生数据，不随快照走；删掉让下一次从 user_facts 重建，避免旧图指向已恢复掉的 fact
    graph = db.parent / "memory_graph.db"
    for suffix in ("", "-wal", "-shm"):
        try:
            Path(f"{graph}{suffix}").unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    try:
        conn = sqlite3.connect(str(db), timeout=15)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception:
        pass

    return {
        "restored": item,
        "safety": safety,
        "graph_note": "memory_graph.db 不随快照恢复；已清空，下次访问会从 user_facts 重建",
    }


def delete_snapshot(name: str, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """手动删除一个快照（只删备份文件，不动当前库）。"""
    item = _resolve(name, db_path)
    path = Path(item["path"])
    try:
        path.unlink()
    except OSError as exc:
        raise SnapshotError(f"删除失败：{exc}") from exc
    return item


def status(db_path: Optional[Path] = None) -> Dict[str, Any]:
    items = list_snapshots(db_path)
    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "total_bytes": sum(item["size"] for item in items),
        "total_text": _human_size(sum(item["size"] for item in items)),
        "dirs": [str(path) for path in snapshot_dirs(db_path)],
    }


__all__ = [
    "SnapshotError",
    "status",
    "list_snapshots",
    "create_snapshot",
    "restore_snapshot",
    "delete_snapshot",
    "snapshot_dirs",
]
