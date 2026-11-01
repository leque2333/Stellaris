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

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

KIND_LABELS = {
    "migration": "迁移前",
    "import": "导入前",
    "restore": "恢复前",
    "manual": "手动",
    "scheduled": "定时",
}

# 只认这些前缀，避免把备份目录里别的 .db 当成快照
_KINDS = (
    ("pre_migrate_", "migration"),
    ("memory_before_import_", "import"),
    ("pre_restore_", "restore"),
    ("manual_", "manual"),
    ("scheduled_", "scheduled"),
)
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.db$")

BACKUP_CONFIG_ENV = "HUB_MEMORY_BACKUP_CONFIG"
CHECK_INTERVAL_SECONDS = 86400.0
RETRY_SECONDS = 1800.0
_BACKUP_LOCK = threading.Lock()

_BACKUP_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "interval_hours": 24,
    "backup_dir": "",
    "check_enabled": True,
    "last_backup_at": 0.0,
    "last_backup_name": "",
    "last_backup_error": "",
    "last_backup_error_at": 0.0,
    "last_check_at": 0.0,
    "last_check_ok": None,
    "last_check_message": "",
}


class SnapshotError(ValueError):
    """快照不存在 / 名字非法 / 文件损坏。"""


def _db_path(db_path: Optional[Path] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    from ..storage import get_store

    return Path(get_store().path)


# ----------------------------------------------------------------------
# 自动备份 / 巡检配置（data/memory_backup.json）
# ----------------------------------------------------------------------
def backup_config_path() -> Path:
    env = os.getenv(BACKUP_CONFIG_ENV, "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "data" / "memory_backup.json"


def load_backup_config() -> Dict[str, Any]:
    try:
        raw = json.loads(backup_config_path().read_text(encoding="utf-8-sig"))
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    config = dict(_BACKUP_DEFAULTS)
    config.update({key: raw[key] for key in _BACKUP_DEFAULTS if key in raw})
    config["enabled"] = bool(config.get("enabled"))
    config["check_enabled"] = bool(config.get("check_enabled"))
    try:
        config["interval_hours"] = max(1, min(720, int(config.get("interval_hours") or 24)))
    except (TypeError, ValueError):
        config["interval_hours"] = 24
    config["backup_dir"] = str(config.get("backup_dir") or "")
    return config


def _write_backup_config(config: Dict[str, Any]) -> None:
    path = backup_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def save_backup_config(
    enabled: Optional[bool] = None,
    interval_hours: Optional[int] = None,
    backup_dir: Optional[str] = None,
    check_enabled: Optional[bool] = None,
) -> Dict[str, Any]:
    with _BACKUP_LOCK:
        config = load_backup_config()
        if enabled is not None:
            config["enabled"] = bool(enabled)
        if check_enabled is not None:
            config["check_enabled"] = bool(check_enabled)
        if interval_hours is not None:
            try:
                value = int(interval_hours)
            except (TypeError, ValueError) as exc:
                raise SnapshotError("间隔必须是 1–720 的整数小时") from exc
            if value < 1 or value > 720:
                raise SnapshotError("间隔必须是 1–720 的整数小时")
            config["interval_hours"] = value
        if backup_dir is not None:
            text = str(backup_dir).strip()
            if text:
                path = Path(os.path.expandvars(os.path.expanduser(text)))
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except OSError as exc:
                    raise SnapshotError(f"备份目录不可用：{exc}") from exc
                if not path.is_dir():
                    raise SnapshotError("备份目录不是文件夹")
                config["backup_dir"] = str(path.resolve())
            else:
                config["backup_dir"] = ""
        _write_backup_config(config)
    return config


def _update_backup_state(**fields: Any) -> Dict[str, Any]:
    with _BACKUP_LOCK:
        config = load_backup_config()
        config.update(fields)
        _write_backup_config(config)
    return config


def _configured_backup_dir() -> Optional[Path]:
    text = str(load_backup_config().get("backup_dir") or "").strip()
    return Path(text) if text else None


def backup_due(config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> bool:
    config = config or load_backup_config()
    now = time.time() if now is None else now
    if not config.get("enabled"):
        return False
    error_at = float(config.get("last_backup_error_at") or 0)
    if error_at and now - error_at < RETRY_SECONDS:
        return False
    last = float(config.get("last_backup_at") or 0)
    return now - last >= float(config.get("interval_hours") or 24) * 3600


def check_due(config: Optional[Dict[str, Any]] = None, now: Optional[float] = None) -> bool:
    config = config or load_backup_config()
    now = time.time() if now is None else now
    if not config.get("check_enabled"):
        return False
    return now - float(config.get("last_check_at") or 0) >= CHECK_INTERVAL_SECONDS


def next_backup_at(config: Optional[Dict[str, Any]] = None) -> Optional[float]:
    config = config or load_backup_config()
    if not config.get("enabled"):
        return None
    last = float(config.get("last_backup_at") or 0)
    return last + float(config.get("interval_hours") or 24) * 3600


def next_check_at(config: Optional[Dict[str, Any]] = None) -> Optional[float]:
    config = config or load_backup_config()
    if not config.get("check_enabled"):
        return None
    return float(config.get("last_check_at") or 0) + CHECK_INTERVAL_SECONDS


def check_integrity(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """对当前库跑 PRAGMA quick_check（只读，不锁写）。"""
    db = _db_path(db_path)
    started = time.time()
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
        try:
            rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        finally:
            conn.close()
    except Exception as exc:
        return {
            "ok": False,
            "message": f"{type(exc).__name__}: {exc}",
            "checked_at": time.time(),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    ok = not rows or rows == ["ok"]
    return {
        "ok": ok,
        "message": "ok" if ok else "；".join(rows[:5]),
        "checked_at": time.time(),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def run_check(db_path: Optional[Path] = None) -> Dict[str, Any]:
    result = check_integrity(db_path)
    _update_backup_state(
        last_check_at=result["checked_at"],
        last_check_ok=bool(result["ok"]),
        last_check_message=str(result.get("message") or ""),
    )
    return result


def run_scheduled_backup(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """做一份定时快照；失败也记录，避免每分钟重试。"""
    try:
        info = create_snapshot("scheduled", db_path=db_path)
    except Exception as exc:
        _update_backup_state(last_backup_error=str(exc), last_backup_error_at=time.time())
        raise
    _update_backup_state(
        last_backup_at=time.time(),
        last_backup_name=info["name"],
        last_backup_error="",
        last_backup_error_at=0.0,
    )
    return info


def backup_tick(db_path: Optional[Path] = None) -> Dict[str, Any]:
    """调度器每轮调用：到点就备份 / 巡检。"""
    config = load_backup_config()
    result: Dict[str, Any] = {"backup": None, "check": None}
    if backup_due(config):
        try:
            result["backup"] = run_scheduled_backup(db_path)
        except Exception as exc:
            result["backup_error"] = str(exc)
    if check_due(config):
        result["check"] = run_check(db_path)
    return result


def backup_status(db_path: Optional[Path] = None) -> Dict[str, Any]:
    config = load_backup_config()
    snapshots = status(db_path)
    return {
        "ok": True,
        "config": config,
        "next_backup_at": next_backup_at(config),
        "next_check_at": next_check_at(config),
        "snapshot_count": snapshots["count"],
        "snapshot_total_bytes": snapshots["total_bytes"],
        "snapshot_total_text": snapshots["total_text"],
    }


def snapshot_dirs(db_path: Optional[Path] = None) -> List[Path]:
    """迁移/手动/定时/恢复快照目录 + 导入快照目录 + 自定义备份目录（去重）。"""
    db = _db_path(db_path)
    migration = Path(os.getenv("HUB_SNAPSHOT_DIR", "").strip() or (db.parent.parent / "backups"))
    imports = Path(os.getenv("HUB_IMPORT_BACKUP_DIR", "").strip() or (db.parent / "backups"))
    paths = [migration, imports]
    configured = _configured_backup_dir()
    if configured is not None:
        paths.append(configured)
    result: List[Path] = []
    for path in paths:
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


def _new_name(prefix: str, db: Path, folder: Optional[Path] = None) -> Path:
    folder = Path(folder) if folder is not None else snapshot_dirs(db)[0]
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    target = folder / f"{prefix}_{stamp}_{db.stem}.db"
    index = 1
    while target.exists():
        target = folder / f"{prefix}_{stamp}_{index}_{db.stem}.db"
        index += 1
    return target


def create_snapshot(
    kind: str = "manual",
    db_path: Optional[Path] = None,
    target_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """给当前库做一份整库快照；kind 允许 manual / scheduled / restore。"""
    db = _db_path(db_path)
    if not db.is_file():
        raise SnapshotError("当前记忆库不存在")
    prefix = {"restore": "pre_restore", "scheduled": "scheduled"}.get(kind, "manual")
    if kind == "restore":
        folder = Path(target_dir) if target_dir is not None else snapshot_dirs(db)[0]
    else:
        folder = (
            Path(target_dir) if target_dir is not None
            else _configured_backup_dir() or snapshot_dirs(db)[0]
        )
    target = _new_name(prefix, db, folder)
    try:
        _copy(db, target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise SnapshotError(f"备份失败：{type(exc).__name__}: {exc}") from exc
    final_kind = kind if kind in ("manual", "scheduled", "restore") else "manual"
    return _info(target, final_kind)


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
