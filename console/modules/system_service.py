# -*- coding: utf-8 -*-
"""
Stellaris Console - System Service
系统状态概览、自传时间轴与审计日志服务
"""

import os
import json
import time
import uuid
import datetime
import sqlite3
from .common import (
    get_db_connection,
    sanitize_clean_text,
    resolve_hub_db_path,
    resolve_astrbot_cmd_config_path
)
from agent_core.storage import classify_source, get_store
from agent_core.memory.embedding import embed_text_bytes
from agent_core.persona_identity import get_persona_name

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
        segment_id=kwargs.get("segment_id"),
        started_at=kwargs.get("started_at"),
        ended_at=kwargs.get("ended_at"),
    )

def _price_for_model(model_id):
    try:
        from .model_service import get_model_pricing_map, get_models_config
        pricing = get_model_pricing_map()
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
            "SELECT id, model, prompt_tokens, completion_tokens, cached_tokens "
            "FROM token_usage WHERE COALESCE(cost_usd, 0) = 0"
        ).fetchall()
        for row in rows:
            price = _price_for_model(str(row["model"] or ""))
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


def record_token_usage_event(model_id, input_tokens, output_tokens, cache_tokens=0, source="universal_proxy", cost_usd=0.0, credential_type=None, currency=None):
    """统一账本：所有通道都写入 token_usage（token_usage_ledger 为兼容视图）"""
    try:
        if not cost_usd:
            price = _price_for_model(str(model_id or ""))
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
        )
    except Exception as e:
        print(f"[TokenLedger] Record failed: {e}")

def get_timeline_events(limit=60):
    events = []
    for evt in get_store().list_events(limit=limit, order="desc"):
        payload = evt.metadata or {}
        raw_source = evt.source_module or "system"
        raw_actor = evt.actor_name or "用户"
        raw_narrative = sanitize_clean_text(evt.narrative or "")
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
            # 两级标签（供控制台分组）
            "plugin_id": evt.plugin_id,
            "tag_primary": evt.tag_primary,
            "tag_secondary": evt.tag_secondary,
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


def get_system_resources_metrics(force: bool = False):
    """仅统计与显示 AI 相关的专属资源占用(模型显存/算力、AI专属CPU、AI专属运行内存)"""
    global _resources_cache, _resources_cache_at
    if not force and _resources_cache is not None and time.time() - _resources_cache_at < 30:
        return _resources_cache
    try:
        import psutil
    except ImportError:
        psutil = None
    import subprocess

    res = {
        "timestamp": time.time(),
        "cpu": {
            "percent": 0.0,
            "cores": 1,
            "physical_cores": 1
        },
        "memory": {
            "total_gb": 0.0,
            "used_gb": 0.0,
            "percent": 0.0
        },
        "gpu": None,
        "services": [],
        "ai_metrics": {
            "cpu_percent": 0.0,
            "memory_mb": 0.0,
            "memory_gb": 0.0,
            "service_count": 0,
            "gpu_ready": False,
            "gpu_name": "NVIDIA GeForce RTX 4070",
            "gpu_memory_used_mb": 0,
            "gpu_memory_total_mb": 12282,
            "gpu_memory_percent": 0.0,
            "gpu_temperature": None
        }
    }

    # 1. 真实 GPU 监控 (NVIDIA 硬件加速)
    try:
        smi = subprocess.check_output(
            ['nvidia-smi', '--query-gpu=name,utilization.gpu,memory.total,memory.used,temperature.gpu', '--format=csv,noheader,nounits'],
            encoding='utf-8', timeout=2
        ).strip()
        if smi:
            parts = [p.strip() for p in smi.split(',')]
            tot = int(parts[2]) if parts[2].isdigit() else 1
            used = int(parts[3]) if parts[3].isdigit() else 0
            temp = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else None
            res["gpu"] = {
                "name": parts[0],
                "util_percent": float(parts[1]) if parts[1].replace('.','',1).isdigit() else 0.0,
                "memory_total_mb": tot,
                "memory_used_mb": used,
                "memory_percent": round(used / tot * 100, 1),
                "temperature": temp
            }
            res["ai_metrics"]["gpu_ready"] = True
            res["ai_metrics"]["gpu_name"] = parts[0]
            res["ai_metrics"]["gpu_memory_used_mb"] = used
            res["ai_metrics"]["gpu_memory_total_mb"] = tot
            res["ai_metrics"]["gpu_memory_percent"] = round(used / tot * 100, 1)
            res["ai_metrics"]["gpu_temperature"] = temp
    except Exception:
        pass

    # 2. 专注识别与汇聚所有 AI 核心服务进程 (杜绝无关系统占用)
    if psutil:
        ai_service_rules = [
            {
                "name": "Console Hub 控制台中枢",
                "match": lambda c, n, p: ("console" in c and "server.py" in c) or (p == 18895),
                "port": 18895
            },
            {
                "name": "Antigravity Bridge 极速推理桥",
                "match": lambda c, n, p: ("antigravity_bridge" in c) or (p == 8765),
                "port": 8765
            },
            {
                "name": "AI Language Server 推理引擎",
                "match": lambda c, n, p: "language_server" in n or "language_server" in c,
                "port": None
            },
            {
                "name": "Local Ollama 本地推理池",
                "match": lambda c, n, p: "ollama" in n or "ollama" in c or (p == 11434),
                "port": 11434
            },
            {
                "name": "AstrBot 机器人智能体核心",
                "match": lambda c, n, p: "astrbot" in c or "astrbot" in n,
                "port": None
            }
        ]

        seen_names = set()
        matched_services = []

        try:
            # 优先遍历真实执行进程 (过滤纯 powershell 包装进程)
            for p in psutil.process_iter(['pid', 'name', 'cmdline', 'cpu_percent', 'memory_info']):
                cmd = " ".join(p.info['cmdline'] or []).lower()
                pname = (p.info['name'] or '').lower()
                if "powershell" in pname:
                    # 跳过纯 wrapper powershell 进程，直接抓背后的真实 python/exe
                    continue
                
                for rule in ai_service_rules:
                    if rule["name"] not in seen_names and rule["match"](cmd, pname, None):
                        seen_names.add(rule["name"])
                        mem_mb = round((p.info['memory_info'].rss or 0) / (1024 * 1024), 1)
                        cpu = round(p.info['cpu_percent'] or 0.0, 1)
                        matched_services.append({
                            "name": rule["name"],
                            "pid": p.info['pid'],
                            "status": "online",
                            "memory_mb": mem_mb,
                            "cpu_percent": cpu,
                            "port": rule["port"]
                        })
                        break
        except Exception:
            pass

        res["services"] = matched_services

        # 计算所有 AI 核心服务的专属资源总和
        ai_cpu_sum = round(sum(s["cpu_percent"] for s in matched_services), 1)
        ai_mem_sum = round(sum(s["memory_mb"] for s in matched_services), 1)
        ai_mem_gb = round(ai_mem_sum / 1024.0, 2)

        res["ai_metrics"]["cpu_percent"] = ai_cpu_sum
        res["ai_metrics"]["memory_mb"] = ai_mem_sum
        res["ai_metrics"]["memory_gb"] = ai_mem_gb
        res["ai_metrics"]["service_count"] = len(matched_services)

        # 同时也把 cpu 和 memory 结构重定向为 AI 专属数据，彻底剔除无关整机占用
        res["cpu"] = {
            "percent": ai_cpu_sum,
            "cores": psutil.cpu_count(logical=True) or 16,
            "service_count": len(matched_services)
        }
        total_ram = psutil.virtual_memory().total if psutil else (32 * 1024 * 1024 * 1024)
        total_ram_gb = round(total_ram / (1024 * 1024 * 1024), 1)
        total_ram_mb = round(total_ram / (1024 * 1024), 1)
        ai_mem_percent = round((ai_mem_sum / total_ram_mb) * 100, 1) if total_ram_mb > 0 else 0.0

        res["memory"] = {
            "ai_memory_mb": ai_mem_sum,
            "ai_memory_gb": ai_mem_gb,
            "total_gb": total_ram_gb,
            "percent": ai_mem_percent
        }

    _resources_cache = res
    _resources_cache_at = time.time()
    return res
