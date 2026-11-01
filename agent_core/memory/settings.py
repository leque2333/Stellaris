# -*- coding: utf-8 -*-
"""记忆调参：一份可持久化的运行时参数表。

优先级固定为 **环境变量 > data/memory_settings.json > 内置默认值**。

为什么环境变量优先：部署脚本 / docker-compose 里显式设过的值，不能被一次
网页点击悄悄覆盖；面板会把这些字段标成「环境变量」并禁用输入。

所有参数在读取时都会裁剪到安全区间；写接口只接受白名单字段，避免把配置
文件当成任意 JSON 仓库。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = BASE_DIR / "data" / "memory_settings.json"

# 只有这些字段允许通过面板修改；顺序就是面板里的顺序。
PARAMS: List[Dict[str, Any]] = [
    {
        "key": "timeline_budget_chars",
        "group": "timeline",
        "label": "时间轴注入预算",
        "hint": "每轮对话整段注入提示词的上限。调大记得更细，也更费 token。",
        "kind": "int",
        "default": 3_000,
        "min": 500,
        "max": 200_000,
        "ui_max": 50_000,
        "step": 100,
        "unit": "字",
        "env": "HUB_TIMELINE_BUDGET",
    },
    {
        "key": "timeline_keep_chars",
        "group": "timeline",
        "label": "时间轴常驻上限",
        "hint": "超出这个字数的旧事件会被搬进缓冲区，主动回忆时再回填。",
        "kind": "int",
        "default": 4_000,
        "min": 1_000,
        "max": 500_000,
        "ui_max": 50_000,
        "step": 500,
        "unit": "字",
        "env": "HUB_TIMELINE_KEEP",
    },
    {
        "key": "recall_max_chars",
        "group": "recall",
        "label": "单次回忆预算",
        "hint": "模型执行一次「记忆检索」最多能带回多少字；超出的整段丢弃。",
        "kind": "int",
        "default": 2_000,
        "min": 300,
        "max": 50_000,
        "ui_max": 20_000,
        "step": 100,
        "unit": "字",
        "env": "HUB_RECALL_MAX_CHARS",
    },
    {
        "key": "candidate_chars",
        "group": "recall",
        "label": "候选池字数",
        "hint": "召回打分前先按新到旧铺满的字数预算；比按条数更能保住长记忆。",
        "kind": "int",
        "default": 300_000,
        "min": 1_000,
        "max": 5_000_000,
        "ui_max": 1_000_000,
        "step": 10_000,
        "unit": "字",
        "env": "HUB_RECALL_CHARS",
    },
    {
        "key": "candidate_limit",
        "group": "recall",
        "label": "候选条数上限",
        "hint": "数据库层的安全阀：候选池最多取这么多条，防止一次扫全库。",
        "kind": "int",
        "default": 3_000,
        "min": 10,
        "max": 100_000,
        "ui_max": 20_000,
        "step": 10,
        "unit": "条",
        "env": "HUB_RECALL_CANDIDATES",
    },
    {
        "key": "lookback_days",
        "group": "recall",
        "label": "回看天数",
        "hint": "只看最近 N 天的记忆；0 表示不按时间窗，交给候选池字数控制。",
        "kind": "float",
        "default": 0.0,
        "min": 0,
        "max": 3_650,
        "ui_max": 365,
        "step": 1,
        "unit": "天",
        "env": "HUB_RECALL_DAYS",
    },
    {
        "key": "half_life_hours",
        "group": "recall",
        "label": "近因半衰期",
        "hint": "时间衰减到一半所需的小时数。越长，旧记忆越容易被语义召回。",
        "kind": "float",
        "default": 168.0,
        "min": 1,
        "max": 8_760,
        "ui_max": 720,
        "step": 1,
        "unit": "小时",
        "env": "HUB_RECALL_HALF_LIFE_HOURS",
    },
    {
        "key": "aggregator_idle_seconds",
        "group": "aggregation",
        "label": "静默切段窗口",
        "hint": "同一会话安静这么久之后，L1 调用会结算成一条 L2 摘要。",
        "kind": "int",
        "default": 600,
        "min": 30,
        "max": 86_400,
        "ui_max": 7_200,
        "step": 30,
        "unit": "秒",
        "env": "HUB_AGGREGATOR_IDLE_SECONDS",
    },
    {
        "key": "aggregator_max_duration_seconds",
        "group": "aggregation",
        "label": "单段最长时长",
        "hint": "再活跃的会话也会在这个时长强制切段，避免一段永远不结算。",
        "kind": "int",
        "default": 7_200,
        "min": 600,
        "max": 604_800,
        "ui_max": 43_200,
        "step": 600,
        "unit": "秒",
        "env": "HUB_AGGREGATOR_MAX_DURATION_SECONDS",
    },
    {
        "key": "aggregator_interval_seconds",
        "group": "aggregation",
        "label": "聚合扫描间隔",
        "hint": "后台聚合器多久 tick 一次；调小更及时，调大更省资源。",
        "kind": "int",
        "default": 30,
        "min": 5,
        "max": 3_600,
        "ui_max": 600,
        "step": 5,
        "unit": "秒",
        "env": "HUB_AGGREGATOR_INTERVAL_SECONDS",
    },
    {
        "key": "memory_tool_enabled",
        "group": "features",
        "label": "记忆检索指令",
        "hint": "允许模型在回复开头写 [[记忆检索: ...]]，由中枢查库后让它重答。",
        "kind": "bool",
        "default": True,
        "env": "HUB_MEMORY_TOOL",
    },
    {
        "key": "prefetch_enabled",
        "group": "features",
        "label": "自动预取记忆",
        "hint": "每轮先按用户原话猜关键词查一次旧记忆；默认关闭，避免无谓开销。",
        "kind": "bool",
        "default": False,
        "env": "HUB_MEMORY_PREFETCH",
    },
]

PARAM_BY_KEY = {item["key"]: item for item in PARAMS}
DEFAULTS = {item["key"]: item["default"] for item in PARAMS}
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

_CACHE: Dict[str, Any] = {"key": None, "raw": {}}
_LOCK = threading.Lock()


def config_path() -> Path:
    """配置文件位置；测试可用 HUB_MEMORY_SETTINGS_PATH 指到临时目录。"""
    env_path = os.getenv("HUB_MEMORY_SETTINGS_PATH", "").strip()
    if env_path:
        return Path(env_path)
    return DEFAULT_CONFIG_PATH


def _invalidate_cache() -> None:
    with _LOCK:
        _CACHE["key"] = None
        _CACHE["raw"] = {}


def _read_saved() -> Dict[str, Any]:
    """读已保存的覆盖值；文件缺失/损坏就当没保存过。"""
    path = config_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cache_key = (str(path), mtime)
    with _LOCK:
        if _CACHE["key"] == cache_key:
            return dict(_CACHE["raw"])
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        data = {}
    raw = data if isinstance(data, dict) else {}
    with _LOCK:
        _CACHE["key"] = cache_key
        _CACHE["raw"] = raw
    return dict(raw)


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY or text == "":
        return False
    raise ValueError(f"无法识别的开关值: {value!r}")


def _parse_number(param: Dict[str, Any], value: Any, strict: bool) -> Any:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        if strict:
            raise ValueError(f"{param['label']} 必须是数字") from exc
        raise
    if param["kind"] == "int":
        number = int(round(number))
    if strict and not (param["min"] <= number <= param["max"]):
        raise ValueError(
            f"{param['label']} 需在 {param['min']}~{param['max']} 之间"
        )
    return number


def _coerce(param: Dict[str, Any], value: Any, strict: bool = False) -> Any:
    """把外部值转成参数类型；非严格模式只做裁剪，不抛错。"""
    if param["kind"] == "bool":
        return _parse_bool(value)
    try:
        number = _parse_number(param, value, strict)
    except (TypeError, ValueError):
        if strict:
            raise
        return param["default"]
    if not strict:
        number = max(param["min"], min(param["max"], number))
    return number


def _source_value(param: Dict[str, Any], saved: Dict[str, Any]) -> Tuple[Any, str]:
    """优先级：环境变量 > 保存值 > 默认值。"""
    env_text = os.getenv(param["env"], "")
    if env_text != "":
        try:
            return _coerce(param, env_text, strict=False), "env"
        except (TypeError, ValueError):
            pass
    if param["key"] in saved:
        try:
            return _coerce(param, saved[param["key"]], strict=False), "saved"
        except (TypeError, ValueError):
            pass
    return param["default"], "default"


def effective() -> Dict[str, Any]:
    """当前生效值（环境变量优先，其次保存值，最后默认值）。"""
    saved = _read_saved()
    return {item["key"]: _source_value(item, saved)[0] for item in PARAMS}


def sources() -> Dict[str, str]:
    """每个参数的来源：env / saved / default。"""
    saved = _read_saved()
    return {item["key"]: _source_value(item, saved)[1] for item in PARAMS}


def get(key: str, fallback: Any = None) -> Any:
    return effective().get(key, fallback)


def _validate_patch(patch: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("settings 必须是对象")
    cleaned: Dict[str, Any] = {}
    errors: List[str] = []
    for key, value in patch.items():
        param = PARAM_BY_KEY.get(key)
        if param is None:
            errors.append(f"未知参数: {key}")
            continue
        try:
            cleaned[key] = _coerce(param, value, strict=True)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("；".join(errors))
    current_sources = sources()
    locked = [key for key in cleaned if current_sources.get(key) == "env"]
    if locked:
        labels = "、".join(PARAM_BY_KEY[key]["label"] for key in locked)
        raise ValueError(f"由环境变量控制，面板不能修改：{labels}")
    return cleaned


def save(patch: Dict[str, Any]) -> Dict[str, Any]:
    """校验并合并写入配置文件，返回保存后的完整生效配置。"""
    cleaned = _validate_patch(patch)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _read_saved()
    merged.update(cleaned)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    _invalidate_cache()
    return effective()


def reset() -> Dict[str, Any]:
    """删掉保存的覆盖值，回到环境变量 / 默认值。"""
    path = config_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    _invalidate_cache()
    return effective()


def public_payload() -> Dict[str, Any]:
    """给控制台面板的完整元数据（含输入范围、来源、锁定原因）。"""
    values = effective()
    current_sources = sources()
    params = []
    for item in PARAMS:
        env_value = os.getenv(item["env"], "")
        params.append({
            **item,
            "value": values[item["key"]],
            "source": current_sources[item["key"]],
            "env_set": bool(env_value),
            "locked": current_sources[item["key"]] == "env",
        })
    return {
        "ok": True,
        "settings": values,
        "defaults": dict(DEFAULTS),
        "sources": current_sources,
        "params": params,
        "path": str(config_path()),
    }


def _count_and_span(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(LENGTH(narrative)), 0), MIN(timestamp), MAX(timestamp) "
        "FROM agent_timeline WHERE COALESCE(visibility, 'private') != 'archived'"
    ).fetchone()
    count, chars, oldest, newest = row
    span = float(newest or 0) - float(oldest or 0)
    recent = conn.execute(
        "SELECT COUNT(*) FROM agent_timeline WHERE COALESCE(visibility, 'private') != 'archived' "
        "AND timestamp >= ?",
        (time.time() - 86_400,),
    ).fetchone()[0]
    archived = conn.execute(
        "SELECT COUNT(*) FROM agent_timeline WHERE COALESCE(visibility, 'private') = 'archived'"
    ).fetchone()[0]
    return {
        "active_events": int(count or 0),
        "active_chars": int(chars or 0),
        "archived_events": int(archived or 0),
        "recent_24h": int(recent or 0),
        "oldest_ts": float(oldest or 0),
        "newest_ts": float(newest or 0),
        "coverage_days": round(span / 86_400, 2) if span > 0 else 0.0,
    }


def _details_stats(conn: sqlite3.Connection) -> Dict[str, Any]:
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM memory_details"
        ).fetchone()
        return {"details": int(row[0] or 0), "detail_bytes": int(row[1] or 0)}
    except sqlite3.Error:
        return {"details": 0, "detail_bytes": 0}


def _buffer_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM timeline_buffer").fetchone()[0] or 0)
    except sqlite3.Error:
        return 0


def memory_stats(store=None) -> Dict[str, Any]:
    """记忆库规模：面板只需要计数，不读取正文。"""
    try:
        if store is None:
            from ..storage import get_store

            store = get_store()
        path = Path(store.path)
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            stats = _count_and_span(conn)
            stats.update(_details_stats(conn))
            stats["buffer_events"] = _buffer_count(conn)
        finally:
            conn.close()
        stats["db_bytes"] = path.stat().st_size if path.exists() else 0
        stats["wal_bytes"] = sum(
            p.stat().st_size for p in (
                path.with_name(path.name + "-wal"),
                path.with_name(path.name + "-shm"),
            ) if p.exists()
        )
        stats["db_disk_bytes"] = stats["db_bytes"] + stats["wal_bytes"]
        stats["ok"] = True
        return stats
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "active_events": 0,
            "active_chars": 0,
            "archived_events": 0,
            "recent_24h": 0,
            "coverage_days": 0.0,
            "details": 0,
            "detail_bytes": 0,
            "buffer_events": 0,
            "db_bytes": 0,
            "wal_bytes": 0,
            "db_disk_bytes": 0,
        }


def env_keys() -> List[str]:
    """当前被环境变量接管的参数名（面板展示用）。"""
    return [item["key"] for item in PARAMS if os.getenv(item["env"], "") != ""]
