# -*- coding: utf-8 -*-
"""
Agent Hub Console Backend Service
中枢核心数据服务：管理记忆库、自传时间轴、人设卡、算力矩阵、统一模型池与 Token 账本
"""

import os
import re
import json
import time
import sqlite3
import urllib.request
from datetime import datetime

# 基础路径配置
CONSOLE_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.abspath(os.path.join(CONSOLE_DIR, ".."))
import sys
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_MEMORY_DB_PATH = os.path.join(DATA_DIR, "memory.db")
PERSONA_PATH = os.path.join(DATA_DIR, "anan_persona.md")
MODULES_PERMISSIONS_PATH = os.path.join(DATA_DIR, "modules_permissions.json")
MODELS_CONFIG_PATH = os.path.join(DATA_DIR, "models_config.json")
UPSTREAM_CACHE_PATH = os.path.join(DATA_DIR, "upstream_models_cache.json")
MODULES_STATE_PATH = os.path.join(DATA_DIR, "modules_state.json")
CUSTOM_TOOLS_PATH = os.path.join(DATA_DIR, "custom_tools.json")
MODULES_CUSTOM_PATH = os.path.join(DATA_DIR, "modules_custom.json")

def resolve_hub_db_path():
    """统一记忆中枢底层数据库路径，确保所有统计、自传时间轴和Token流水读写同一物理库"""
    os.makedirs(DATA_DIR, exist_ok=True)
    return DATA_MEMORY_DB_PATH

def get_db_connection(db_path=None):
    target = db_path or resolve_hub_db_path()
    conn = sqlite3.connect(target, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------- 模组与工具生态管理 -----------------

_MODULES_CACHE = None
_MODULES_CACHE_TIME = 0

def sanitize_clean_text(text):
    if not text:
        return ""
    return str(text).strip()

def get_module_states():
    if os.path.exists(MODULES_STATE_PATH):
        try:
            with open(MODULES_STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def toggle_module_active(module_id, is_active):
    global _MODULES_CACHE
    _MODULES_CACHE = None
    states = get_module_states()
    val = bool(is_active)
    states[module_id] = val
    os.makedirs(os.path.dirname(MODULES_STATE_PATH), exist_ok=True)
    with open(MODULES_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(states, f, ensure_ascii=False, indent=2)
    return {"success": True, "module_id": module_id, "is_active": val}

def get_custom_module_configs():
    if os.path.exists(MODULES_CUSTOM_PATH):
        try:
            with open(MODULES_CUSTOM_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def update_module_custom_category(module_id, new_category):
    global _MODULES_CACHE
    _MODULES_CACHE = None
    custom = get_custom_module_configs()
    custom.setdefault(module_id, {})
    custom[module_id]["category"] = str(new_category).strip()
    os.makedirs(os.path.dirname(MODULES_CUSTOM_PATH), exist_ok=True)
    with open(MODULES_CUSTOM_PATH, "w", encoding="utf-8") as f:
        json.dump(custom, f, ensure_ascii=False, indent=2)
    return {"success": True, "module_id": module_id, "category": new_category}

def import_custom_module(module_data):
    global _MODULES_CACHE
    _MODULES_CACHE = None
    os.makedirs(os.path.dirname(CUSTOM_TOOLS_PATH), exist_ok=True)
    custom_list = []
    if os.path.exists(CUSTOM_TOOLS_PATH):
        try:
            with open(CUSTOM_TOOLS_PATH, 'r', encoding='utf-8') as f:
                custom_list = json.load(f)
        except Exception:
            pass
    
    mod_id = module_data.get('id') or f"custom_tool_{int(time.time())}"
    module_data['id'] = mod_id
    module_data['is_active'] = True
    module_data.setdefault('category', '自定义组件')
    module_data.setdefault('protocol', 'OpenAI Function Calling')
    module_data.setdefault('is_ai_direct', True)
    module_data.setdefault('version', '1.0.0')
    module_data.setdefault('author', 'Custom Tool')
    module_data['desc'] = sanitize_clean_text(module_data.get('desc', ''))
    module_data.setdefault('is_custom', True)
    
    existing_idx = next((i for i, m in enumerate(custom_list) if m.get('id') == mod_id), -1)
    if existing_idx >= 0:
        custom_list[existing_idx] = module_data
    else:
        custom_list.append(module_data)

    with open(CUSTOM_TOOLS_PATH, 'w', encoding='utf-8') as f:
        json.dump(custom_list, f, ensure_ascii=False, indent=2)
    
    return {"success": True, "module": module_data}

def delete_custom_module(module_id):
    global _MODULES_CACHE
    _MODULES_CACHE = None
    if os.path.exists(CUSTOM_TOOLS_PATH):
        try:
            with open(CUSTOM_TOOLS_PATH, 'r', encoding='utf-8') as f:
                custom_list = json.load(f)
            custom_list = [m for m in custom_list if m.get('id') != module_id]
            with open(CUSTOM_TOOLS_PATH, 'w', encoding='utf-8') as f:
                json.dump(custom_list, f, ensure_ascii=False, indent=2)
            return {"success": True, "module_id": module_id}
        except Exception as e:
            return {"success": False, "error": str(e)}
    return {"success": False, "message": "custom_tools.json not found"}

def get_modules_overview(force_refresh=False):
    """动态扫描项目自有工具与挂载组件生态"""
    global _MODULES_CACHE, _MODULES_CACHE_TIME
    now_t = time.time()
    if not force_refresh and _MODULES_CACHE and (now_t - _MODULES_CACHE_TIME < 10.0):
        return _MODULES_CACHE

    states = get_module_states()
    modules = []

    # 1. 挂载 Agent Core 内置工具
    try:
        from agent_core.tools import tool_registry
        core_tools = []
        for t in tool_registry.list_tools():
            core_tools.append({
                "name": t.name,
                "desc": t.description,
                "type": "llm_tool",
                "protocol": "OpenAI Function Calling"
            })
        if core_tools:
            modules.append({
                "id": "agent_core_builtins",
                "name": "中枢内置器官工具 (Core Tools)",
                "category": "系统核心",
                "desc": "智能体内核自带的深度回忆与好感度心理微调器官",
                "version": "1.0.0",
                "author": "Agent Core Team",
                "protocol": "OpenAI Function Calling",
                "repo": "",
                "tools": core_tools,
                "commands": [],
                "console_url": "",
                "is_active": True,
                "is_ai_direct": True
            })
    except Exception:
        pass

    # 2. 读取用户自定义导入的工具 (data/custom_tools.json)
    if os.path.exists(CUSTOM_TOOLS_PATH):
        try:
            with open(CUSTOM_TOOLS_PATH, 'r', encoding='utf-8') as f:
                custom_list = json.load(f)
                for c in custom_list:
                    c['is_active'] = states.get(c.get('id'), c.get('is_active', True))
                    c['desc'] = sanitize_clean_text(c.get('desc', ''))
                    c['is_custom'] = True
                    c.setdefault('category', '自定义组件')
                    c['is_ai_direct'] = bool(c.get('tools'))
                    modules.append(c)
        except Exception:
            pass

    # 动态并发挂载健康探针与连通性检测
    try:
        from agent_core.health_probe import ModuleHealthProbe
        modules = ModuleHealthProbe.probe_all_concurrent(modules)
    except Exception:
        for m in modules:
            m['health'] = {
                "status": "internal",
                "badge_color": "green",
                "status_text": "核心常驻",
                "latency_ms": 0,
                "target": "中枢内存",
                "diag_hint": "智能体系统服务正常运行"
            }

    category_order = {"系统核心": 1, "认知记忆": 2, "知识技能": 3, "协议服务": 4, "自定义组件": 5}
    modules.sort(key=lambda m: (category_order.get(m.get("category", ""), 99), m.get("category", ""), m.get("name", "")))

    ai_modules_count = sum(1 for m in modules if m.get('is_ai_direct', True))
    custom_modules_count = sum(1 for m in modules if not m.get('is_ai_direct', True))
    total_tools = sum(len(m.get('tools', [])) for m in modules)
    active_modules_count = sum(1 for m in modules if m.get('is_active', True))
    active_tools_count = sum(len(m.get('tools', [])) for m in modules if m.get('is_active', True))
    online_modules_count = sum(1 for m in modules if m.get('health', {}).get('status') in ('online', 'internal', 'mounted'))
    offline_modules_count = sum(1 for m in modules if m.get('health', {}).get('status') == 'offline')
    protocols = list(set(m.get('protocol', 'Other') for m in modules))

    # 3. 模组管理器树形结构构建与拓扑验证
    tree_data = {}
    try:
        from agent_core.topology import TopologyManager
        tm = TopologyManager(BASE_DIR)
        tree_data = tm.build_tree_structure(modules)
    except Exception as e:
        tree_data = {'error': str(e), 'hosts': [], 'global_skills': [], 'unassigned': []}

    result_payload = {
        'summary': {
            'total_modules': len(modules),
            'ai_modules': ai_modules_count,
            'custom_modules': custom_modules_count,
            'active_modules': active_modules_count,
            'online_modules': online_modules_count,
            'offline_modules': offline_modules_count,
            'total_tools': total_tools,
            'active_tools': active_tools_count,
            'protocols': protocols
        },
        'modules': modules,
        'tree': tree_data,
        'adapter_spec': {
            'protocol_version': '1.0.0',
            'standard': 'Open Agent Module Specification (OAMP)'
        }
    }
    _MODULES_CACHE = result_payload
    _MODULES_CACHE_TIME = now_t
    return result_payload

def get_topology_overview():
    """获取完整的拓扑图数据：包含节点、坐标、连接树与握手健康诊断报告"""
    try:
        from agent_core.topology import TopologyManager
        overview = get_modules_overview()
        modules = overview.get('modules', [])
        tm = TopologyManager(BASE_DIR)
        topo_cfg = tm.load_topology()
        validation = tm.validate_handshake(modules)
        tree = tm.build_tree_structure(modules)
        return {
            'success': True,
            'tree': tree,
            'modules': modules,
            'topology': topo_cfg,
            'validation': validation,
            'protocol_rules': tm.PROTOCOL_RULES
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def save_topology_overview(nodes_data, positions=None):
    """保存拓扑图模组吸附与坐标配置，并立即执行握手语法检测"""
    try:
        from agent_core.topology import TopologyManager
        tm = TopologyManager(BASE_DIR)
        saved = tm.save_topology(nodes_data, positions)
        overview = get_modules_overview(force_refresh=True)
        validation = tm.validate_handshake(overview.get('modules', []))
        return {
            'success': saved,
            'validation': validation,
            'tree': tm.build_tree_structure(overview.get('modules', []))
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def validate_topology_overview(custom_relations=None):
    """单独执行拓扑语法检测与握手排错"""
    try:
        from agent_core.topology import TopologyManager
        tm = TopologyManager(BASE_DIR)
        overview = get_modules_overview()
        validation = tm.validate_handshake(overview.get('modules', []), custom_relations)
        return {
            'success': True,
            'validation': validation
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}

def probe_single_module(module_id):
    overview = get_modules_overview(force_refresh=True)
    for m in overview.get('modules', []):
        if m.get('id') == module_id:
            try:
                from agent_core.health_probe import ModuleHealthProbe
                res = ModuleHealthProbe.probe_single(m)
                m['health'] = res
                return {"success": True, "health": res, "module": m}
            except Exception as e:
                return {"success": False, "error": str(e)}
    return {"success": False, "error": f"找不到模块: {module_id}"}

def probe_all_modules():
    return get_modules_overview(force_refresh=True)

# ----------------- 人格设定模块 -----------------

def get_current_persona():
    try:
        from agent_core.persona import PersonaManager
        pm = PersonaManager()
        base_prompt = pm.get_base_prompt()
        if base_prompt:
            return {"system_prompt": base_prompt, "source": "persona_kernel"}
    except Exception:
        pass

    if os.path.exists(PERSONA_PATH):
        try:
            with open(PERSONA_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return {"system_prompt": content, "source": "anan_persona_md"}
        except Exception:
            pass

    return {"system_prompt": "你是一个拥有自洽灵魂与纯口语法则的智能体。", "source": "default"}

def save_persona(prompt):
    os.makedirs(os.path.dirname(PERSONA_PATH), exist_ok=True)
    with open(PERSONA_PATH, "w", encoding="utf-8") as f:
        f.write(prompt)
    return {"success": True, "message": "人格设定已持久化保存"}

def get_preset_personas():
    """获取内置角色卡设定预设列表"""
    presets = []
    candidate_files = [
        ("核心系统设定 V6.3 (当前最新)", PERSONA_PATH),
        ("经典角色设定 V5.2", os.path.join(BASE_DIR, "hakimi_core_settings_v5_2.md")),
        ("基底模板设定", os.path.join(BASE_DIR, "agent_core", "persona_template.md")),
    ]
    for name, path in candidate_files:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                presets.append({
                    "name": name,
                    "filename": os.path.basename(path),
                    "length": len(content),
                    "content": content
                })
            except Exception:
                pass
    return presets

# ----------------- Token 账本与大模型调用监控 -----------------

def get_model_pricing_map():
    mapping = {}
    try:
        presets = get_preset_models()
        for m in presets:
            mid = (m.get("id") or "").lower().strip()
            if mid:
                mapping[mid] = {
                    "input_price": float(m.get("input_price") or 0.0),
                    "output_price": float(m.get("output_price") or 0.0),
                    "cache_price": float(m.get("cache_price") or 0.0)
                }
    except Exception:
        pass
    return mapping

def get_token_analytics():
    """计算 Token 总账本与支出数据，按真实发生的工具/通道动态聚合"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_credits = {
        "balance": 50.00,
        "total_granted": 50.00,
        "total_spent": 0.00,
        "currency": "USD"
    }

    db_file = resolve_hub_db_path()
    if not os.path.exists(db_file):
        return {
            "credits": default_credits,
            "today": {"date": today_str, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
            "overall": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
            "modules_breakdown": [],
            "recent_calls": []
        }

    conn = get_db_connection(db_file)
    try:
        cur = conn.cursor()
        tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_usage'").fetchone()
        if not tbl:
            return {
                "credits": default_credits,
                "today": {"date": today_str, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
                "overall": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
                "modules_breakdown": [],
                "recent_calls": []
            }

        pricing_map = get_model_pricing_map()

        all_rows = cur.execute(
            """
            SELECT id, timestamp, time_str, session_type, model, 
                   prompt_tokens, completion_tokens, total_tokens, 
                   cached_tokens, call_duration_ms
            FROM token_usage
            ORDER BY timestamp DESC
            """
        ).fetchall()

        total_calls = len(all_rows)
        overall_input = 0
        overall_output = 0
        overall_total = 0
        overall_cost = 0.0

        today_calls = 0
        today_input = 0
        today_output = 0
        today_cache = 0
        today_total = 0
        today_cost = 0.0

        recent_calls = []
        mod_stats = {}

        color_palette = ["#38bdf8", "#10b981", "#f59e0b", "#f43f5e", "#a855f7", "#ec4899", "#6366f1"]
        channel_names = {
            "universal_proxy": "通用 API 投射网关 (/v1)",
            "api": "API 直接调用",
            "web": "控制台在线交互",
            "cli": "命令行交互终端",
            "agent_core": "中枢认知记忆流"
        }

        for idx, r in enumerate(all_rows):
            m_raw = (r["model"] or "").strip().lower()
            m_price = pricing_map.get(m_raw, {"input_price": 0.15, "output_price": 0.60, "cache_price": 0.0375})
            
            p_in = r["prompt_tokens"] or 0
            p_out = r["completion_tokens"] or 0
            p_cache = r["cached_tokens"] or 0
            p_tot = r["total_tokens"] or (p_in + p_out)
            t_str = r["time_str"] or ""

            uncached_in = max(0, p_in - p_cache)
            c_cost = (uncached_in * m_price["input_price"] + 
                      p_cache * m_price["cache_price"] + 
                      p_out * m_price["output_price"]) / 1000000.0

            overall_input += p_in
            overall_output += p_out
            overall_total += p_tot
            overall_cost += c_cost

            raw_st = (r["session_type"] or "universal_proxy").strip()
            if raw_st not in mod_stats:
                color_idx = len(mod_stats) % len(color_palette)
                mod_stats[raw_st] = {
                    "id": raw_st,
                    "name": channel_names.get(raw_st, raw_st),
                    "calls": 0,
                    "tokens": 0,
                    "cost": 0.0,
                    "color": color_palette[color_idx]
                }
            mod_stats[raw_st]["calls"] += 1
            mod_stats[raw_st]["tokens"] += p_tot
            mod_stats[raw_st]["cost"] += c_cost

            if t_str.startswith(today_str):
                today_calls += 1
                today_input += p_in
                today_output += p_out
                today_cache += p_cache
                today_total += p_tot
                today_cost += c_cost

            if idx < 20:
                recent_calls.append({
                    "id": r["id"],
                    "timestamp": r["timestamp"],
                    "time_str": r["time_str"],
                    "session_type": r["session_type"],
                    "model": r["model"],
                    "prompt_tokens": p_in,
                    "completion_tokens": p_out,
                    "cached_tokens": p_cache,
                    "total_tokens": p_tot,
                    "duration_ms": r["call_duration_ms"] or 0,
                    "cost": round(c_cost, 6),
                    "cost_formatted": f"${c_cost:.4f}"
                })

        cfg_credits = default_credits
        if os.path.exists(MODELS_CONFIG_PATH):
            try:
                with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg_credits = json.load(f).get("credits", default_credits)
            except Exception:
                pass

        total_grant = float(cfg_credits.get("total_quota") or cfg_credits.get("total_granted") or 50.0)
        calc_balance = max(0.0, round(total_grant - overall_cost, 4))

        credits_data = {
            "balance": calc_balance,
            "total_granted": total_grant,
            "total_spent": round(overall_cost, 4),
            "currency": cfg_credits.get("currency", "USD")
        }

        modules_breakdown = []
        for cid, stat in sorted(mod_stats.items(), key=lambda x: x[1]["tokens"], reverse=True):
            pct = round((stat["tokens"] / overall_total * 100.0), 1) if overall_total > 0 else 0.0
            modules_breakdown.append({
                "id": stat["id"],
                "name": stat["name"],
                "calls": stat["calls"],
                "tokens": stat["tokens"],
                "cost": round(stat["cost"], 4),
                "cost_formatted": f"${stat['cost']:.4f}",
                "percent": pct,
                "color": stat["color"]
            })

        return {
            "credits": credits_data,
            "today": {
                "date": today_str,
                "calls": today_calls,
                "input_tokens": today_input,
                "output_tokens": today_output,
                "cached_tokens": today_cache,
                "total_tokens": today_total,
                "cost": round(today_cost, 4),
                "cost_formatted": f"${today_cost:.4f}"
            },
            "overall": {
                "calls": total_calls,
                "input_tokens": overall_input,
                "output_tokens": overall_output,
                "total_tokens": overall_total,
                "cost": round(overall_cost, 4),
                "cost_formatted": f"${overall_cost:.4f}"
            },
            "modules_breakdown": modules_breakdown,
            "recent_calls": recent_calls
        }
    except Exception:
        return {
            "credits": default_credits,
            "today": {"date": today_str, "calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
            "overall": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost": 0.0, "cost_formatted": "$0.0000"},
            "modules_breakdown": [],
            "recent_calls": []
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass

def get_timeline_events(limit=25):
    """获取自传式时间轴经历流"""
    found_db = resolve_hub_db_path()
    if not os.path.exists(found_db):
        return []
    conn = get_db_connection(found_db)
    try:
        cur = conn.cursor()
        tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_timeline'").fetchone()
        if not tbl:
            return []
        rows = cur.execute(
            """
            SELECT event_id, timestamp, time_str, source_module, actor_name, narrative, emotional_impact, importance 
            FROM agent_timeline 
            ORDER BY timestamp DESC 
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()

def get_system_summary():
    """获取系统整体简报"""
    summary = {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "persona_status": "Ready",
        "memory_count": 0,
        "timeline_count": 0,
        "master_affection": 90,
        "user_profile_count": 0
    }
    db_file = resolve_hub_db_path()
    if os.path.exists(db_file):
        try:
            m_conn = get_db_connection(db_file)
            m_cur = m_conn.cursor()
            tbl_up = m_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_profiles'").fetchone()
            if tbl_up:
                summary["user_profile_count"] = m_cur.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
                row_aff = m_cur.execute("SELECT affection FROM user_profiles WHERE user_id = 'master' OR role_type = 'admin' LIMIT 1").fetchone()
                if row_aff and row_aff[0] is not None:
                    summary["master_affection"] = int(row_aff[0])
            tbl_tl = m_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_timeline'").fetchone()
            if tbl_tl:
                summary["timeline_count"] = m_cur.execute("SELECT COUNT(*) FROM agent_timeline").fetchone()[0]
                summary["memory_count"] = summary["timeline_count"]
            m_conn.close()
        except Exception:
            pass
    return summary

# ----------------- 大模型网络数据源与思考强度同步 -----------------

def build_upstream_network_opener():
    proxies = [os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"), None]
    for p in proxies:
        try:
            if p:
                handler = urllib.request.ProxyHandler({"http": p, "https": p})
                opener = urllib.request.build_opener(handler)
            else:
                opener = urllib.request.build_opener()
            req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "Mozilla/5.0 AgentHub/1.0"})
            with opener.open(req, timeout=4) as res:
                if res.status == 200:
                    return opener
        except Exception:
            continue
    return urllib.request.build_opener()

def sync_models_from_upstream():
    """从权威公开数据源动态拉取最新模型参数、定价与思考强度并持久化"""
    try:
        opener = build_upstream_network_opener()
        req = urllib.request.Request("https://openrouter.ai/api/v1/models", headers={"User-Agent": "Mozilla/5.0 AgentHub/1.0"})
        with opener.open(req, timeout=12) as res:
            raw = json.loads(res.read().decode("utf-8"))
            data = raw.get("data", [])
            
        provider_map = {
            "google/": "Google",
            "anthropic/": "Anthropic",
            "openai/": "OpenAI",
            "deepseek/": "DeepSeek",
            "meta-llama/": "Meta",
            "mistralai/": "Mistral",
            "qwen/": "Qwen"
        }
        
        parsed = []
        for item in data:
            mid = item.get("id", "")
            lower_id = mid.lower()
            if any(lower_id.endswith(suffix) for suffix in [":batch", ":free"]):
                continue
            
            matched_p = None
            for prefix, pname in provider_map.items():
                if lower_id.startswith(prefix):
                    matched_p = pname
                    break
            if not matched_p:
                continue

            supported_params = item.get("supported_parameters", [])
            pricing = item.get("pricing", {})
            try:
                p_in = float(pricing.get("prompt", 0)) * 1000000
            except Exception:
                p_in = 0.0
            try:
                p_out = float(pricing.get("completion", 0)) * 1000000
            except Exception:
                p_out = 0.0
            try:
                p_cache = float(pricing.get("input_cache_read", 0)) * 1000000
            except Exception:
                p_cache = round(p_in * 0.25, 4)
            
            raw_name = item.get("name") or mid.split("/")[-1]
            clean_name = raw_name.split(":", 1)[1].strip() if ":" in raw_name else raw_name.strip()
            
            if "reasoning_effort" in supported_params:
                reasoning_type = "effort"
                available_efforts = ["low", "medium", "high"]
                reasoning_desc = "力度调控 (Low / Medium / High)"
            elif "reasoning" in supported_params or "include_reasoning" in supported_params:
                reasoning_type = "native"
                available_efforts = ["native"]
                reasoning_desc = "原生自主推理 (全量推演，无外部档位)"
            else:
                reasoning_type = "none"
                available_efforts = ["none"]
                reasoning_desc = "不支持思考调控"

            tier = "Medium"
            if p_in >= 1.5 or p_out >= 5.0 or "r1" in lower_id or "o1" in lower_id:
                tier = "High"
            elif p_in <= 0.15 and p_out <= 0.6:
                tier = "Low"

            parsed.append({
                "id": mid.split("/")[-1],
                "full_id": mid,
                "name": clean_name,
                "provider": matched_p,
                "tier": tier,
                "input_price": round(p_in, 4),
                "output_price": round(p_out, 4),
                "cache_price": round(p_cache, 4),
                "currency": "USD",
                "context_length": item.get("context_length", 128000),
                "supported_parameters": supported_params,
                "reasoning_type": reasoning_type,
                "available_reasoning_efforts": available_efforts,
                "reasoning_desc": reasoning_desc
            })
            
        if parsed:
            os.makedirs(os.path.dirname(UPSTREAM_CACHE_PATH), exist_ok=True)
            with open(UPSTREAM_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(parsed, f, ensure_ascii=False, indent=2)
            return {"success": True, "count": len(parsed), "source": "openrouter_network"}
    except Exception:
        pass
        
    return {"success": False, "count": 0, "source": "error"}

def get_model_reasoning_options(model_id):
    if not model_id:
        return ["none"]
    clean_id = model_id.split("/")[-1].lower().strip()

    if os.path.exists(UPSTREAM_CACHE_PATH):
        try:
            with open(UPSTREAM_CACHE_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
                for m in cached:
                    if m.get("id", "").lower() == clean_id or m.get("full_id", "").lower().endswith("/" + clean_id):
                        return m.get("available_reasoning_efforts", ["none"])
        except Exception:
            pass

    if os.path.exists(MODELS_CONFIG_PATH):
        try:
            with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                for m in cfg.get("models", []):
                    if m.get("id", "").lower() == clean_id:
                        return m.get("available_reasoning_efforts", ["none"])
        except Exception:
            pass

    return ["none"]

def check_model_reasoning_support(model_id):
    opts = get_model_reasoning_options(model_id)
    return opts != ["none"]

def get_preset_models():
    data = []
    if os.path.exists(UPSTREAM_CACHE_PATH):
        try:
            with open(UPSTREAM_CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
    if not data:
        sync_models_from_upstream()
        if os.path.exists(UPSTREAM_CACHE_PATH):
            try:
                with open(UPSTREAM_CACHE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass
    return data or []

def get_models_config():
    default_config = {
        "active_model": "gemini-3.8-flash",
        "reasoning_effort": "medium",
        "credits": {
            "total_quota": 50.0,
            "used_quota": 0.0,
            "balance": 50.0,
            "currency": "USD"
        },
        "models": [],
        "api_pool": []
    }
    config = dict(default_config)
    if os.path.exists(MODELS_CONFIG_PATH):
        try:
            with open(MODELS_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config.update(saved)
        except Exception:
            pass
            
    presets = get_preset_models()
    for m in presets:
        m["available_reasoning_efforts"] = get_model_reasoning_options(m.get("id", ""))
    for m in config.get("models", []):
        m["available_reasoning_efforts"] = get_model_reasoning_options(m.get("id", ""))
        
    config["preset_templates"] = presets
    
    if not config.get("active_model"):
        config["active_model"] = "gemini-3.8-flash"

    active_m = config.get("active_model", "")
    valid_efforts = get_model_reasoning_options(active_m)
    if config.get("reasoning_effort") not in valid_efforts:
        config["reasoning_effort"] = valid_efforts[0]
        
    return config

def save_models_config(new_config):
    downgraded = False
    active_m = new_config.get("active_model", "")
    effort = new_config.get("reasoning_effort", "none")
    
    valid_efforts = get_model_reasoning_options(active_m)
    if effort not in valid_efforts:
        new_config["reasoning_effort"] = valid_efforts[0]
        downgraded = True

    os.makedirs(os.path.dirname(MODELS_CONFIG_PATH), exist_ok=True)
    with open(MODELS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_config, f, ensure_ascii=False, indent=2)
        
    return {
        "success": True,
        "downgraded_reasoning": downgraded,
        "reasoning_effort": new_config.get("reasoning_effort", "none"),
        "message": "已自动匹配模型可用思考强度" if downgraded else "配置已保存"
    }

def save_credits(data):
    current_credits = {
        "total_quota": 50.0,
        "balance": 50.0,
        "currency": "USD"
    }
    cfg = get_models_config()
    if "credits" in cfg:
        current_credits.update(cfg["credits"])
    
    if "total_quota" in data:
        current_credits["total_quota"] = float(data["total_quota"])
    if "balance" in data:
        current_credits["balance"] = float(data["balance"])
    if "currency" in data:
        current_credits["currency"] = str(data["currency"])
        
    cfg_to_save = {
        "active_model": cfg.get("active_model", ""),
        "reasoning_effort": cfg.get("reasoning_effort", "none"),
        "credits": current_credits,
        "api_pool": cfg.get("api_pool", []),
        "models": cfg.get("models", [])
    }
    save_models_config(cfg_to_save)
    return {"success": True, "credits": current_credits}

# ----------------- 模块与工具权限管控 (RBAC) -----------------

DEFAULT_PERMISSIONS = {
    "universal_proxy": {
        "allow_recall_memory": True,
        "allow_ingest_timeline": True,
        "allowed_tools": ["*"],
        "disallowed_tools": []
    }
}

def get_module_permissions():
    if os.path.exists(MODULES_PERMISSIONS_PATH):
        try:
            with open(MODULES_PERMISSIONS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_PERMISSIONS

def save_module_permissions(perms):
    os.makedirs(os.path.dirname(MODULES_PERMISSIONS_PATH), exist_ok=True)
    with open(MODULES_PERMISSIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(perms, f, ensure_ascii=False, indent=2)
    return {"success": True, "message": "权限配置已保存"}

# ----------------- 通用 OpenAI 兼容网关 (/v1) -----------------

def get_universal_models_list():
    cfg = get_models_config()
    models_out = []
    active_m = cfg.get("active_model", "gemini-3.8-flash")
    for m in cfg.get("preset_templates", []):
        models_out.append({
            "id": m.get("id"),
            "object": "model",
            "created": 1700000000,
            "owned_by": m.get("provider", "hub")
        })
    if not any(m["id"] == active_m for m in models_out):
        models_out.insert(0, {
            "id": active_m,
            "object": "model",
            "created": 1700000000,
            "owned_by": "hub"
        })
    return {"object": "list", "data": models_out}

def clean_persona_reply(text):
    if not text:
        return ""
    raw = str(text).strip()

    code_snippets = []
    def _save_code(m):
        code_snippets.append(m.group(0))
        return f"__CODE_SNIPPET_{len(code_snippets)-1}__"

    masked = re.sub(r"```[\s\S]*?```", _save_code, raw)
    masked = re.sub(r"`[^`\n]+`", _save_code, masked)

    md_links = []
    def _save_link(m):
        md_links.append(m.group(0))
        return f"__MD_LINK_{len(md_links)-1}__"

    masked = re.sub(r"\[[^\]]+\]\([^\)]+\)", _save_link, masked)

    cleaned = re.sub(r"（[^）]*）", "", masked)
    cleaned = re.sub(r"\([^\)]*[一-龥][^\)]*\)", "", cleaned)

    cleaned = re.sub(r"。+$", "", cleaned.strip())
    if not cleaned.endswith("__"):
        cleaned = re.sub(r"(?<!\.)\.(?!\.)$", "", cleaned).rstrip()

    for i, link in enumerate(md_links):
        cleaned = cleaned.replace(f"__MD_LINK_{i}__", link)
    for i, code in enumerate(code_snippets):
        cleaned = cleaned.replace(f"__CODE_SNIPPET_{i}__", code)

    return cleaned.strip()

def record_token_usage_event(session_type, model_name, p_in, p_out, duration_ms=0):
    """向统一数据库沉淀 Token 账本记录"""
    target_db = resolve_hub_db_path()
    try:
        conn = sqlite3.connect(target_db, timeout=5.0)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                time_str TEXT,
                session_type TEXT,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                cached_tokens INTEGER DEFAULT 0,
                call_duration_ms INTEGER DEFAULT 0
            )
        """)
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO token_usage 
            (timestamp, time_str, session_type, model, prompt_tokens, completion_tokens, total_tokens, cached_tokens, call_duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (now_ts, now_str, session_type, model_name, p_in, p_out, p_in + p_out, 0, duration_ms))
        conn.commit()
        conn.close()
    except Exception:
        pass

def ingest_timeline_event(source_module, actor_name, narrative, actor_id=None, importance=1.0, emotional_impact=0):
    """统一向自传时间轴沉淀经历 (优先通过 TimelineHub 接口，保持时序因果链与好感联动)"""
    target_db = resolve_hub_db_path()
    try:
        from agent_core.identity import IdentityResolver
        resolved_actor_id = actor_id or ("master" if IdentityResolver.is_master(actor_name) or "主人" in str(actor_name) else "user")
        
        try:
            from agent_core.timeline_hub import TimelineHub
            th = TimelineHub(db_path=target_db)
            th.append_event(
                source_module=source_module,
                actor_id=resolved_actor_id,
                narrative=narrative,
                actor_name=actor_name,
                importance=importance,
                emotional_impact=emotional_impact
            )
            return
        except Exception:
            pass

        conn = sqlite3.connect(target_db, timeout=5.0)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_timeline (
                event_id TEXT PRIMARY KEY,
                timestamp REAL NOT NULL,
                time_str TEXT NOT NULL,
                source_module TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                narrative TEXT NOT NULL,
                emotional_impact INTEGER DEFAULT 0,
                importance REAL DEFAULT 1.0,
                prev_event_id TEXT,
                causal_relation TEXT,
                metadata TEXT DEFAULT '{}',
                embedding BLOB
            )
        """)
        import uuid
        ev_id = f"ev_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        now_ts = time.time()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("""
            INSERT INTO agent_timeline 
            (event_id, timestamp, time_str, source_module, actor_id, actor_name, narrative, emotional_impact, importance)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ev_id, now_ts, now_str, source_module, resolved_actor_id, actor_name, narrative, emotional_impact, importance))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ----------------- 全合规算力矩阵与双池调度 -----------------

def get_compute_config():
    try:
        from agent_core.compute_providers import compute_manager
        return compute_manager.get_config()
    except Exception as e:
        return {"error": str(e), "active_slots": {}, "providers": {}}

def save_compute_config(config_data):
    try:
        from agent_core.compute_providers import compute_manager
        old_cfg = compute_manager.load_config()
        new_providers = config_data.get("providers", {})
        for p_key, p_val in new_providers.items():
            new_k = p_val.get("api_key", "")
            if not new_k or "****" in new_k:
                old_k = old_cfg.get("providers", {}).get(p_key, {}).get("api_key", "")
                p_val["api_key"] = old_k
        ok = compute_manager.save_config(config_data)
        return {"success": ok}
    except Exception as e:
        return {"success": False, "error": str(e)}

def test_compute_provider(provider_key):
    try:
        from agent_core.compute_providers import compute_manager
        return compute_manager.test_provider_connection(provider_key)
    except Exception as e:
        return {"ok": False, "error": str(e)}
