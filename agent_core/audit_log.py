# -*- coding: utf-8 -*-
"""安全审计日志：登录、密钥轮换、访问限制、签名拒绝都记到这里。

时间轴（agent_timeline）是给 AI 看的记忆，运维事件不该混进去，
所以单独写 logs/audit-YYYY-MM-DD.log（JSON Lines）。

保留策略：
    ・按天分文件；单文件超过 5 MB 就切 audit-日期.1.log（每天最多 3 段）
    ・默认保留 90 天，超过就删最旧的
    ・总大小超过 256 MB 时从最旧的开始删，避免日志撑爆磁盘
    ・以上都可用环境变量覆盖：HUB_AUDIT_DIR / HUB_AUDIT_RETENTION_DAYS /
      HUB_AUDIT_MAX_TOTAL_MB / HUB_AUDIT_MAX_FILE_MB
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_DEDUPE: Dict[str, float] = {}
DEDUPE_SECONDS = 60.0
_LAST_PRUNE = 0.0
PRUNE_INTERVAL = 3600.0
SEGMENTS_PER_DAY = 3
DEFAULT_COMPRESS_AFTER_DAYS = 7
LOG_NAME = re.compile(r"^audit-(\d{4}-\d{2}-\d{2})(?:\.(\d))?\.log(\.gz)?$")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def log_dir() -> Path:
    env = os.getenv("HUB_AUDIT_DIR", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "logs"


def retention_days() -> float:
    return _env_float("HUB_AUDIT_RETENTION_DAYS", 90)


def max_total_bytes() -> int:
    return int(_env_float("HUB_AUDIT_MAX_TOTAL_MB", 256) * 1024 * 1024)


def max_file_bytes() -> int:
    return int(_env_float("HUB_AUDIT_MAX_FILE_MB", 5) * 1024 * 1024)


def compress_after_days() -> float:
    return _env_float("HUB_AUDIT_COMPRESS_AFTER_DAYS", DEFAULT_COMPRESS_AFTER_DAYS)


def log_path(day: Optional[time.struct_time] = None) -> Path:
    """当天的审计文件；测试或排查时可以传指定日期。"""
    stamp = time.strftime("%Y-%m-%d", day or time.localtime())
    return log_dir() / f"audit-{stamp}.log"


def record(event: str, level: str = "info", dedupe: bool = True, **fields: Any) -> Optional[Dict[str, Any]]:
    """追加一条审计记录；可去重的按 (event, ip, error) 60 秒内只记一次。"""
    key = f"{event}|{fields.get('ip', '')}|{fields.get('error', '')}"
    now = time.time()
    with _LOCK:
        if dedupe and DEDUPE_SECONDS > 0 and key in _DEDUPE and now - _DEDUPE[key] < DEDUPE_SECONDS:
            return None
        if dedupe:
            _DEDUPE[key] = now
        entry = {
            "ts": int(now),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "event": event,
            "level": level,
            **fields,
        }
        try:
            path = _writable_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return None
        _maybe_prune(now)
    return entry


def _writable_path() -> Path:
    """当天文件写满了就顺延到 .1 / .2；三段都满就继续写最后一段。"""
    base = log_path()
    if not base.exists() or base.stat().st_size < max_file_bytes():
        return base
    for index in range(1, SEGMENTS_PER_DAY + 1):
        candidate = base.with_name(f"{base.stem}.{index}.log")
        if not candidate.exists() or candidate.stat().st_size < max_file_bytes():
            return candidate
    return base.with_name(f"{base.stem}.{SEGMENTS_PER_DAY}.log")


def _segments() -> List[Path]:
    """所有审计文件，按日期 + 段号升序。"""
    directory = log_dir()
    if not directory.exists():
        return []
    found = [item for item in directory.iterdir() if item.is_file() and LOG_NAME.match(item.name)]
    return sorted(found, key=lambda item: item.name)


def _maybe_prune(now: float) -> None:
    global _LAST_PRUNE
    if now - _LAST_PRUNE < PRUNE_INTERVAL:
        return
    _LAST_PRUNE = now
    compress_old(now)
    prune()


def compress_old(now: Optional[float] = None) -> List[str]:
    """超过 compress_after_days 的明文日志压成 .gz，原文件删掉。"""
    import gzip

    now_ts = time.time() if now is None else now
    threshold = compress_after_days() * 86400
    done: List[str] = []
    for path in _segments():
        if path.suffix == ".gz" or LOG_NAME.match(path.name) is None:
            continue
        try:
            if now_ts - path.stat().st_mtime <= threshold:
                continue
            target = path.with_name(path.name + ".gz")
            stamp = path.stat().st_mtime
            with open(path, "rb") as source, gzip.open(target, "wb") as sink:
                sink.write(source.read())
            os.utime(target, (stamp, stamp))  # 保留原时间，否则保留天数会从压缩时刻重算
            path.unlink()
            done.append(target.name)
        except OSError:
            continue
    return done


def prune(now: Optional[float] = None) -> List[str]:
    """按天数 + 总大小清理，返回被删掉的文件名。"""
    now_ts = time.time() if now is None else now
    removed: List[str] = []
    keep: List[Path] = []
    # 1) 超过保留天数
    for path in _segments():
        if now_ts - path.stat().st_mtime > retention_days() * 86400:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                pass
        else:
            keep.append(path)
    # 2) 总大小超限，从最旧的删
    total = sum(path.stat().st_size for path in keep)
    for path in list(keep):
        if total <= max_total_bytes():
            break
        total -= path.stat().st_size
        try:
            path.unlink()
            removed.append(path.name)
        except OSError:
            pass
    return removed


def _read_lines(path: Path) -> List[str]:
    """普通文件直接读，归档文件走 gzip。"""
    if path.suffix == ".gz":
        import gzip

        with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
            return handle.readlines()
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.readlines()


def tail(limit: int = 200) -> List[Dict[str, Any]]:
    """读最近 N 条（最新在前），跨文件拼接。"""
    if limit <= 0:
        return []
    collected: List[Dict[str, Any]] = []
    for path in reversed(_segments()):
        try:
            lines = _read_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                collected.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(collected) >= limit:
                return collected
    return collected


def stats() -> Dict[str, Any]:
    """文件数、总大小、起止文件、保留策略（给接口/控制台看）。"""
    segments = _segments()
    return {
        "dir": str(log_dir()),
        "files": len(segments),
        "bytes": sum(path.stat().st_size for path in segments),
        "oldest": segments[0].name if segments else "",
        "newest": segments[-1].name if segments else "",
        "retention_days": retention_days(),
        "max_total_mb": round(max_total_bytes() / 1024 / 1024, 1),
        "max_file_mb": round(max_file_bytes() / 1024 / 1024, 1),
        "compressed": sum(1 for path in segments if path.suffix == ".gz"),
        "compress_after_days": compress_after_days(),
    }


def migrate_legacy() -> Optional[str]:
    """把旧的单文件 audit.log 收编成当天文件。"""
    legacy = log_dir() / "audit.log"
    if not legacy.exists():
        return None
    stamp = time.strftime("%Y-%m-%d", time.localtime(legacy.stat().st_mtime))
    target = log_dir() / f"audit-{stamp}.log"
    index = 0
    while target.exists():
        index += 1
        target = log_dir() / f"audit-{stamp}.{index}.log"
    try:
        legacy.replace(target)
        return target.name
    except OSError:
        return None


def reset_dedupe() -> None:
    """测试用：清掉去重窗口。"""
    with _LOCK:
        _DEDUPE.clear()
