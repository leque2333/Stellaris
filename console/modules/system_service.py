# -*- coding: utf-8 -*-
"""
Stellaris Console - System Service
系统状态概览、自传时间轴与审计日志服务
"""

import os
import time

from agent_core.memory.embedding import embed_text_bytes
from agent_core.persona_identity import get_persona_name
from agent_core.storage import classify_source, get_store

from .common import (
    resolve_astrbot_cmd_config_path,
    sanitize_clean_text,
)


def ingest_timeline_event(
    source: str,
    actor_name: str,
    narrative: str,
    payload: dict = None,
    session_window_seconds: int = 0,
) -> bool:
    """写入一条经历；标签由 source + payload 自动推断（插件可显式覆盖）"""
    try:
        payload = payload or {}
        plugin_id, tag_primary, tag_secondary = classify_source(source, payload)
        store = get_store()
        evt = store.append_event(
            _timeline_event(
                source_module=source,
                actor_id=payload.get("actor_id") or "hub_system",
                actor_name=actor_name,
                narrative=narrative,
                importance=float(payload.get("importance", 1.0)),
                emotional_impact=int(payload.get("emotional_impact", 0)),
                metadata=payload,
                plugin_id=payload.get("plugin_id") or plugin_id,
                tag_primary=payload.get("tag_primary") or tag_primary,
                tag_secondary=payload.get("tag_secondary") or tag_secondary,
                tag_permission=payload.get("tag_permission") or "unknown",
                detail_id=payload.get("detail_id") or "",
                person_id=payload.get("person_id") or "",
                embedding=embed_text_bytes(narrative),
            ),
            session_window_seconds=session_window_seconds,
        )
        # 返回 event_id，便于 L1 调用索引回链 L2 事件；旧调用按布尔判断仍然成立
        return evt.event_id
    except Exception as e:
        print(f"[Timeline] Ingest failed: {e}")
        return False

def _timeline_event(**kwargs):
    from agent_core.models import TimelineEvent
    return TimelineEvent(
        event_id="",
        timestamp=kwargs.get("timestamp") or time.time(),
        source_module=kwargs["source_module"],
        actor_id=kwargs["actor_id"],
        actor_name=kwargs["actor_name"],
        narrative=kwargs["narrative"],
        importance=kwargs.get("importance", 1.0),
        emotional_impact=kwargs.get("emotional_impact", 0),
        metadata=kwargs.get("metadata") or {},
        detail_id=kwargs.get("detail_id") or "",
        person_id=kwargs.get("person_id") or "",
        embedding=kwargs.get("embedding"),
        plugin_id=kwargs.get("plugin_id"),
        tag_primary=kwargs.get("tag_primary"),
        tag_secondary=kwargs.get("tag_secondary"),
        tag_permission=kwargs.get("tag_permission"),
        segment_id=kwargs.get("segment_id"),
        started_at=kwargs.get("started_at"),
        ended_at=kwargs.get("ended_at"),
    )

def _price_for_model(model_id, at=None):
    try:
        from .model_service import get_model_pricing_map, get_models_config
        pricing = get_model_pricing_map(at)
        if model_id in pricing:
            return pricing[model_id]
        active = str(get_models_config().get("active_model") or "")
        if active in pricing:
            return pricing[active]
    except Exception:
        pass
    return {}


def backfill_token_costs():
    """旧流水 cost_usd=0 的，按模型单价补算一次。"""
    try:
        from .common import get_db_connection
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT id, model, prompt_tokens, completion_tokens, cached_tokens, timestamp "
            "FROM token_usage WHERE COALESCE(cost_usd, 0) = 0"
        ).fetchall()
        for row in rows:
            price = _price_for_model(str(row["model"] or ""), row["timestamp"])
            if not price:
                continue
            cost = (
                float(row["prompt_tokens"] or 0) * float(price.get("input", 0))
                + float(row["completion_tokens"] or 0) * float(price.get("output", 0))
                + float(row["cached_tokens"] or 0) * float(price.get("cache", 0))
            ) / 1_000_000.0
            if cost > 0:
                conn.execute("UPDATE token_usage SET cost_usd = ? WHERE id = ?", (cost, row["id"]))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[TokenLedger] backfill failed: {exc}")


def credential_meta_for_model(model_id):
    """根据模型绑定推断账本归属：master/function + 币种。"""
    model = str(model_id or "").strip()
    try:
        from .model_service import get_models_config
        cfg = get_models_config() or {}
        for pool, kind in (("master_credentials", "master"), ("function_credentials", "function")):
            for cred in cfg.get(pool, []) or []:
                if str(cred.get("model") or "").strip() != model:
                    continue
                currency = str(cred.get("currency") or "").upper()
                if currency not in ("USD", "CNY", "RMB"):
                    haystack = f"{cred.get('provider', '')} {cred.get('model', '')} {cred.get('name', '')}".lower()
                    currency = "CNY" if any(k in haystack for k in (
                        "deepseek", "qwen", "千问", "通义", "glm", "智谱", "baidu", "文心",
                        "ernie", "minimax", "moonshot", "kimi", "百炼", "国产",
                    )) else "USD"
                return kind, "CNY" if currency == "RMB" else currency
    except Exception:
        pass
    lowered = model.lower()
    if model in ("memory.fact_extraction", "anan_hub_main") or lowered.startswith("gemini-"):
        return "master", "USD"
    if any(k in lowered for k in ("deepseek", "qwen", "glm", "moonshot", "kimi")):
        return "function", "CNY"
    return "", "USD"


def backfill_usage_meta():
    """为旧流水补齐主/执行凭证归属和币种。"""
    try:
        from .common import get_db_connection
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT id, model FROM token_usage "
            "WHERE COALESCE(source, '') NOT IN ('人工充值', '账户充值', '系统注资') "
            "AND (credential_type IS NULL OR credential_type = '')"
        ).fetchall()
        for row in rows:
            kind, currency = credential_meta_for_model(row["model"])
            if kind:
                conn.execute(
                    "UPDATE token_usage SET credential_type = ?, currency = ? WHERE id = ?",
                    (kind, currency, row["id"]),
                )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[TokenLedger] meta backfill failed: {exc}")


def record_token_usage_event(
    model_id,
    input_tokens,
    output_tokens,
    cache_tokens=0,
    source="universal_proxy",
    cost_usd=0.0,
    credential_type=None,
    currency=None,
    tools="",
    timestamp=None,
):
    """统一账本：所有通道都写入 token_usage（token_usage_ledger 为兼容视图）"""
    try:
        if not cost_usd:
            price = _price_for_model(
                str(model_id or ""), timestamp if timestamp is not None else time.time()
            )
            if price:
                cost_usd = (
                    float(input_tokens or 0) * float(price.get("input", 0))
                    + float(output_tokens or 0) * float(price.get("output", 0))
                    + float(cache_tokens or 0) * float(price.get("cache", 0))
                ) / 1_000_000.0
        meta_type, meta_currency = credential_meta_for_model(model_id)
        get_store().record_usage(
            session_type=source,
            model=model_id,
            prompt_tokens=int(input_tokens or 0),
            completion_tokens=int(output_tokens or 0),
            cached_tokens=int(cache_tokens or 0),
            cost_usd=float(cost_usd or 0.0),
            source=source,
            credential_type=credential_type or meta_type,
            currency=currency or meta_currency,
            tools=str(tools or ""),
        )
    except Exception as e:
        print(f"[TokenLedger] Record failed: {e}")

def _timeline_platform(source, payload, secondary):
    platform = str(payload.get("platform") or "").strip().lower()
    if platform:
        return platform
    text = f"{source} {payload.get('channel', '')} {secondary}".lower()
    if "napcat" in text or "qq" in text or "astrbot" in text:
        return "qq"
    if "bilibili" in text:
        return "bilibili"
    if "telegram" in text:
        return "telegram"
    if "discord" in text:
        return "discord"
    if "web" in text or "universal_proxy" in text:
        return "web"
    return ""


def _timeline_uid(payload, secondary, actor_id):
    uid = str(payload.get("sender_uid") or payload.get("uid") or payload.get("raw_user_id") or "").strip()
    if uid:
        return uid
    text = str(secondary or "").strip()
    if ":" in text:
        return text.rsplit(":", 1)[-1]
    actor = str(actor_id or "").strip()
    return actor if actor.isdigit() else ""


def get_timeline_events(limit=60):
    from agent_core.memory.episodic_stream import _drop_empty_summaries

    events = []
    raw_events = get_store().list_events(limit=max(limit * 5, 200), order="desc")
    archives = {}
    for evt in _drop_empty_summaries(raw_events):
        if len(events) >= limit:
            break
        payload = evt.metadata or {}
        raw_source = evt.source_module or "system"
        raw_actor = evt.actor_name or "用户"
        raw_narrative = sanitize_clean_text(evt.narrative or "")
        secondary = str(evt.tag_secondary or "").strip()
        archive = None
        if secondary.startswith("arc_"):
            if secondary not in archives:
                try:
                    archives[secondary] = get_store().get_archive(secondary)
                except Exception:
                    archives[secondary] = None
            archive = archives[secondary]
        archive_name = str((archive or {}).get("display_name") or "").strip()
        uid = _timeline_uid(payload, secondary, evt.actor_id)
        platform = _timeline_platform(raw_source, payload, secondary)
        display_name = archive_name or uid or raw_actor
        events.append({
            "id": evt.event_id,
            "timestamp": evt.timestamp,
            "time_str": evt.time_str,
            "source_module": raw_source,
            "event_type": raw_source,
            "actor_name": raw_actor,
            "source": raw_actor,
            "narrative": raw_narrative,
            "content": raw_narrative,
            "payload": payload,
            # 标签拆成：插件名 / 平台 / 人员
            "plugin_id": evt.plugin_id,
            "tag_primary": evt.tag_primary,
            "tag_secondary": evt.tag_secondary,
            "tag_permission": evt.tag_permission,
            "platform": platform,
            "uid": uid,
            "archive_id": secondary if secondary.startswith("arc_") else "",
            "archive_name": archive_name,
            "display_name": display_name,
            "segment_id": evt.segment_id,
            "started_at": evt.started_at,
            "ended_at": evt.ended_at,
            "importance": evt.importance,
            "emotional_impact": evt.emotional_impact,
        })
    return events

def get_system_summary():
    timeline_count = get_store().timeline_count()
    memory_count = 0  # 旧 memories 表仅作历史兼容，不再作为主口径

    astrbot_cfg_path = resolve_astrbot_cmd_config_path()
    astrbot_linked = os.path.exists(astrbot_cfg_path)

    return {
        "status": "online",
        "agent_name": get_persona_name(),
        "memory_count": memory_count,
        "timeline_count": timeline_count,
        "astrbot_linked": astrbot_linked,
        "active_modules_count": 8,
        "timestamp": time.time()
    }


_resources_cache = None
_resources_cache_at = 0.0


def _parse_nvidia_smi_line(raw: str) -> dict:
    """nvidia-smi 输出是 "名字, 利用率, 总显存, 已用显存, 温度"。"""
    parts = [item.strip() for item in raw.split(",")]
    total = int(parts[2]) if parts[2].isdigit() else 1
    used = int(parts[3]) if parts[3].isdigit() else 0
    return {
        "name": parts[0],
        "util_percent": float(parts[1]) if parts[1].replace(".", "", 1).isdigit() else 0.0,
        "memory_total_mb": total,
        "memory_used_mb": used,
        "memory_percent": round(used / total * 100, 1),
        "temperature": int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None,
    }


def _read_nvidia_gpu_metrics():
    """读 N 卡显存 / 利用率 / 温度；没有 nvidia-smi 就返回 None。"""
    import subprocess

    try:
        raw = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu",
             "--format=csv,noheader,nounits"],
            encoding="utf-8", timeout=2,
        ).strip()
    except Exception:
        return None
    return _parse_nvidia_smi_line(raw) if raw else None


def _apply_gpu_metrics(target: dict, gpu: dict) -> None:
    """把 GPU 数据同时写进顶层 gpu 和 ai_metrics，前端两处都读。"""
    target["gpu"] = gpu
    ai = target["ai_metrics"]
    ai["gpu_ready"] = True
    ai["gpu_name"] = gpu["name"]
    ai["gpu_memory_used_mb"] = gpu["memory_used_mb"]
    ai["gpu_memory_total_mb"] = gpu["memory_total_mb"]
    ai["gpu_memory_percent"] = gpu["memory_percent"]
    ai["gpu_temperature"] = gpu["temperature"]


def _build_ai_service_rules() -> list:
    """本机可能出现的 AI 进程特征。命中即归类，不命中就不算。"""
    return [
        {"name": "Console Hub 控制台中枢", "match": lambda cmd, name: ("console" in cmd and "server.py" in cmd), "port": 18895},
        {"name": "Host Bridge 宿主机控制桥", "match": lambda cmd, name: "host_bridge" in cmd or "antigravity_bridge" in cmd, "port": 8765},
        {"name": "AI Language Server 推理引擎", "match": lambda cmd, name: "language_server" in name or "language_server" in cmd, "port": None},
        {"name": "Local Ollama 本地推理池", "match": lambda cmd, name: "ollama" in name or "ollama" in cmd, "port": 11434},
        {"name": "AstrBot 机器人智能体核心", "match": lambda cmd, name: "astrbot" in cmd or "astrbot" in name, "port": None},
    ]


def _find_matching_rule(rules, seen_names, cmdline, process_name):
    """找第一条命中且还没用过的规则；没命中返回 None。"""
    for rule in rules:
        if rule["name"] in seen_names:
            continue
        if rule["match"](cmdline, process_name):
            return rule
    return None


def _describe_service(rule, info) -> dict:
    return {
        "name": rule["name"],
        "pid": info["pid"],
        "status": "online",
        "memory_mb": round((info["memory_info"].rss or 0) / (1024 * 1024), 1),
        "cpu_percent": round(info["cpu_percent"] or 0.0, 1),
        "port": rule["port"],
    }


def _collect_ai_services(psutil) -> list:
    """遍历进程，按规则归类；每个规则只取第一个命中的（防止包装进程重复计数）。"""
    rules = _build_ai_service_rules()
    seen_names = set()
    matched = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_info"]):
        info = proc.info
        cmdline = " ".join(info["cmdline"] or []).lower()
        process_name = (info["name"] or "").lower()
        # 跳过 powershell 包装层，抓背后真正的 python/exe
        if "powershell" in process_name:
            continue
        rule = _find_matching_rule(rules, seen_names, cmdline, process_name)
        if rule is None:
            continue
        seen_names.add(rule["name"])
        matched.append(_describe_service(rule, info))
    return matched


def _build_cpu_block(cpu_sum: float, service_count: int, psutil) -> dict:
    return {
        "percent": cpu_sum,
        "cores": psutil.cpu_count(logical=True) or 16,
        "service_count": service_count,
    }


def _build_memory_block(memory_mb: float, memory_gb: float, psutil) -> dict:
    total_ram_mb = round(psutil.virtual_memory().total / (1024 * 1024), 1)
    return {
        "ai_memory_mb": memory_mb,
        "ai_memory_gb": memory_gb,
        "total_gb": round(total_ram_mb / 1024, 1),
        "percent": round(memory_mb / total_ram_mb * 100, 1) if total_ram_mb else 0.0,
    }


def _summarize_ai_totals(target: dict, services: list, psutil) -> None:
    """把服务列表汇总成总和，并把 cpu/memory 结构改写成 AI 专属口径。"""
    cpu_sum = round(sum(item["cpu_percent"] for item in services), 1)
    memory_mb = round(sum(item["memory_mb"] for item in services), 1)
    memory_gb = round(memory_mb / 1024.0, 2)

    ai = target["ai_metrics"]
    ai["cpu_percent"] = cpu_sum
    ai["memory_mb"] = memory_mb
    ai["memory_gb"] = memory_gb
    ai["service_count"] = len(services)

    target["cpu"] = _build_cpu_block(cpu_sum, len(services), psutil)
    target["memory"] = _build_memory_block(memory_mb, memory_gb, psutil)


_DEFAULT_CPU_BLOCK = {"percent": 0.0, "cores": 1, "physical_cores": 1}
_DEFAULT_MEMORY_BLOCK = {"total_gb": 0.0, "used_gb": 0.0, "percent": 0.0}
_DEFAULT_AI_METRICS = {
    "cpu_percent": 0.0,
    "memory_mb": 0.0,
    "memory_gb": 0.0,
    "service_count": 0,
    "gpu_ready": False,
    "gpu_name": "NVIDIA GeForce RTX 4070",
    "gpu_memory_used_mb": 0,
    "gpu_memory_total_mb": 12282,
    "gpu_memory_percent": 0.0,
    "gpu_temperature": None,
}


def _build_empty_resources() -> dict:
    """先把结构搭好；采集失败时前端也能拿到完整字段，不用做空判断。"""
    return {
        "timestamp": time.time(),
        "cpu": dict(_DEFAULT_CPU_BLOCK),
        "memory": dict(_DEFAULT_MEMORY_BLOCK),
        "gpu": None,
        "services": [],
        "ai_metrics": dict(_DEFAULT_AI_METRICS),
    }


def _take_cached_resources(force: bool):
    """30 秒内的缓存直接复用，避免前端轮询把机器拖慢。"""
    if force or _resources_cache is None:
        return None
    return _resources_cache if time.time() - _resources_cache_at < 30 else None


def _import_psutil():
    """没装 psutil 就返回 None，让调用方跳过进程统计而不是崩掉。"""
    try:
        import psutil
        return psutil
    except ImportError:
        return None


def _collect_resources_snapshot() -> dict:
    """真正干活：建空结构 → 填 GPU → 填进程。"""
    result = _build_empty_resources()
    gpu = _read_nvidia_gpu_metrics()
    if gpu:
        _apply_gpu_metrics(result, gpu)

    psutil = _import_psutil()
    if psutil:
        services = _collect_ai_services(psutil)
        result["services"] = services
        _summarize_ai_totals(result, services, psutil)
    return result


def get_system_resources_metrics(force: bool = False):
    """只统计 AI 相关占用。"""
    global _resources_cache, _resources_cache_at
    cached = _take_cached_resources(force)
    if cached is not None:
        return cached
    _resources_cache = _collect_resources_snapshot()
    _resources_cache_at = time.time()
    return _resources_cache
