# -*- coding: utf-8 -*-
"""Stellaris Console - 模组与拓扑

只做两件事：
1. 给仪表盘提供模块总览（拓扑树 + 每个模块的探针状态）
2. 读取模块权限（RBAC）

拓扑优先读 data/topology_config.json；文件缺失或判定是历史假数据时才重新扫盘。
"""

import json
import os
import re
import socket
import time
from urllib.parse import urlparse

from .common import (
    BASE_DIR,
    MODULES_PERMISSIONS_PATH,
    MODULES_STATE_PATH,
    TOPOLOGY_CONFIG_PATH,
    resolve_astrbot_plugins_dir,
    resolve_custom_tools_path,
)

DEFAULT_PERMISSIONS = {
    "universal_proxy": {
        "allow_recall_memory": True,
        "allow_ingest_timeline": True,
        "allowed_tools": ["*"],
        "disallowed_tools": [],
    }
}

# 命中这些 id 说明拓扑配置里是历史假数据，需要重新扫盘
_FAKE_HOST_IDS = ("nonebot", "kb_prts_mrfz", "external_api_bridge")

# 这几个插件归「知识技能」，其余算「系统核心」
_SKILL_PLUGINS = ("astrbot_plugin_mrfz", "astrbot_plugin_limbus", "astrbot_plugin_bilibili")

_ASTRBOT_HOST = {
    "id": "astrbot",
    "name": "AstrBot 核心通讯网关",
    "description": "前台即时通讯中继平台，支持 QQ/微信/Telegram 等多端适配与消息投递。",
    "author": "AstrBot Dev Team",
    "version": "4.1.3",
    "category": "系统核心",
    "type": "host",
    "endpoint": "http://127.0.0.1:6185",
    "protocol": "OpenAI Chat Completions / WebSocket",
    "tools": ["chat_relay", "user_context_forward"],
    "commands": ["/help", "/reload", "/status"],
    "needs_llm_injection": True,
    "consumes_persona": True,
}

_NAPCAT_HOST = {
    "id": "napcat",
    "name": "NapCat QQ 协议端",
    "description": "基于 OneBot v11 协议的高性能 QQ 客户端框架。",
    "author": "NapCat Community",
    "version": "2.4.0",
    "category": "自定义组件",
    "type": "custom",
    "endpoint": "http://127.0.0.1:6099",
    "console_url": "http://127.0.0.1:6099/webui",
    "protocol": "OneBot v11",
    "tools": ["send_qq_msg", "fetch_group_list"],
    "commands": ["/napcat restart"],
    "needs_llm_injection": False,
    "consumes_persona": False,
    "submods": [],
}

_BRIDGE_HOST = {
    "id": "host_pc",
    "name": "宿主机控制桥",
    "description": "通过本地 HTTP 控制 Windows 宿主机（PowerShell / 状态 / 截图 / 锁屏）。",
    "author": "Deepmind AGY",
    "version": "2.0.0",
    "category": "系统核心",
    "type": "host",
    "endpoint": "http://127.0.0.1:8765",
    "protocol": "Host Bridge HTTP",
    "tools": ["execute_terminal_task", "query_host_state"],
    "commands": ["/bridge ping", "/bridge status"],
    "needs_llm_injection": False,
    "consumes_persona": False,
    "submods": [],
}

_KB_HOST = {
    "id": "kb_local_vault",
    "name": "本地多维知识库 (Knowledge Base)",
    "description": "挂载在 AstrBot 上的本地世界观、电竞圣经与 SQLite 向量知识库。",
    "author": "AstrBot RAG",
    "version": "v1.2.0",
    "category": "知识技能",
    "type": "knowledge_base",
    "endpoint": "",
    "protocol": "Local RAG / Vector DB",
    "tools": ["search_knowledge_base", "query_lore_vectors"],
    "commands": ["/kb search", "/kb reload"],
    "needs_llm_injection": False,
    "consumes_persona": False,
}

_MCP_IMAGE_HOST = {
    "id": "mcp_image_generator",
    "name": "AI 绘图生图组件 (FastMCP)",
    "description": "基于 FastMCP 的文生图与图像微调渲染工具，运行在 astrbot/data/mcp_servers/image_generator。",
    "author": "Vision Team",
    "version": "1.2.0",
    "category": "自定义组件",
    "type": "mcp",
    "endpoint": "",
    "protocol": "Anthropic MCP (Tool)",
    "tools": ["image_generation_mcp"],
    "commands": ["/draw"],
    "needs_llm_injection": False,
    "consumes_persona": False,
    "submods": [],
}


# ----------------------------------------------------------------------
# 权限与状态
# ----------------------------------------------------------------------
def get_module_permissions():
    """模块级权限：文件不存在就用默认值。"""
    if os.path.exists(MODULES_PERMISSIONS_PATH):
        try:
            with open(MODULES_PERMISSIONS_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return DEFAULT_PERMISSIONS


def get_module_states():
    if os.path.exists(MODULES_STATE_PATH):
        try:
            with open(MODULES_STATE_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return {}


def get_custom_module_configs():
    path = resolve_custom_tools_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                return {"custom_modules": data, "categories": {}}
            return data
        except Exception:
            pass
    return {"custom_modules": [], "categories": {}}


# ----------------------------------------------------------------------
# 拓扑：优先读缓存，必要时扫盘
# ----------------------------------------------------------------------
def get_topology_overview(force_rescan=False):
    if not force_rescan and os.path.exists(TOPOLOGY_CONFIG_PATH):
        try:
            with open(TOPOLOGY_CONFIG_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            hosts = data.get("hosts", [])
            has_fake = any(host.get("id") in _FAKE_HOST_IDS for host in hosts)
            if not has_fake and hosts:
                return data
        except Exception:
            pass
    topology = scan_real_system_topology()
    save_topology_overview(topology)
    return topology


def save_topology_overview(topo_data):
    os.makedirs(os.path.dirname(TOPOLOGY_CONFIG_PATH), exist_ok=True)
    with open(TOPOLOGY_CONFIG_PATH, "w", encoding="utf-8") as handle:
        json.dump(topo_data, handle, ensure_ascii=False, indent=2)
    return {"success": True, "message": "拓扑结构配置已持久化保存"}


def scan_real_system_topology():
    """扫盘拼出真实拓扑：AstrBot 插件、NapCat、桥接、知识库、MCP。"""
    plugins_dir, astrbot_data_dir, runtime_root = _runtime_paths()
    hosts = [dict(_ASTRBOT_HOST, submods=_scan_astrbot_plugins(plugins_dir))]
    for directory, payload in (
        (os.path.join(runtime_root, "napcat"), _NAPCAT_HOST),
        (os.path.join(runtime_root, "host_bridge"), _BRIDGE_HOST),
    ):
        if os.path.exists(directory):
            hosts.append(dict(payload))
    for extra in (_knowledge_host(astrbot_data_dir), _mcp_host(astrbot_data_dir)):
        if extra:
            hosts.append(extra)
    return {
        "hub": {"id": "anan_hub_core", "name": "Stellaris 全局神经中枢", "status": "online"},
        "hosts": hosts,
    }


def _runtime_paths():
    """从 AstrBot 插件目录反推 data 目录与运行时根目录。"""
    plugins_dir = resolve_astrbot_plugins_dir()
    data_dir = os.path.dirname(plugins_dir) if plugins_dir else ""
    astrbot_dir = os.path.dirname(data_dir) if data_dir else ""
    runtime_root = os.path.dirname(astrbot_dir) if astrbot_dir else BASE_DIR
    return plugins_dir, data_dir, runtime_root


def _scan_astrbot_plugins(plugins_dir):
    """遍历插件目录，每个插件一条 submod。"""
    if not plugins_dir or not os.path.exists(plugins_dir):
        return []
    submods = []
    for item in sorted(os.listdir(plugins_dir)):
        path = os.path.join(plugins_dir, item)
        if os.path.isdir(path):
            submods.append(_plugin_entry(item, path))
    return submods


def _plugin_entry(item, path):
    """单个插件：读 metadata.yaml 与 main.py，抽出展示字段。"""
    meta = _read_metadata(path)
    commands, tools = _plugin_capabilities(path)
    entry = _plugin_fields(item, meta)
    entry["tools"] = sorted(set(tools))
    entry["commands"] = sorted(set(commands or _commands_from_help(meta)))
    return entry


def _plugin_fields(item, meta):
    return {
        "id": item,
        "name": meta.get("display_name") or meta.get("name") or item,
        "description": meta.get("desc") or meta.get("short_desc") or "",
        "author": meta.get("author", "社区"),
        "version": meta.get("version", "v1.0.0"),
        "category": "知识技能" if item in _SKILL_PLUGINS else "系统核心",
        "type": "plugin",
        "endpoint": "",
        "protocol": "AstrBot Plugin",
        "needs_llm_injection": False,
        "consumes_persona": False,
    }


def _commands_from_help(meta):
    """main.py 里没抓到命令时，从 metadata 的 help 文本兜底。"""
    return re.findall(r"(/[a-zA-Z0-9_]+)", meta.get("help") or "")


def _read_metadata(path):
    meta_file = os.path.join(path, "metadata.yaml")
    if not os.path.exists(meta_file):
        return {}
    try:
        import yaml

        with open(meta_file, "r", encoding="utf-8") as handle:
            return yaml.safe_load(handle) or {}
    except Exception:
        return {}


def _plugin_capabilities(path):
    """从 main.py 里抓 @filter.command / @filter.llm_tool 声明的命令与工具。"""
    main_py = os.path.join(path, "main.py")
    if not os.path.exists(main_py):
        return [], []
    try:
        with open(main_py, "r", encoding="utf-8", errors="ignore") as handle:
            code = handle.read()
    except Exception:
        return [], []
    commands = re.findall(r'@filter\.command\([\'"]([^\'"]+)[\'"]\)', code)
    named = re.findall(r'@filter\.llm_tool\(name=[\'"]([^\'"]+)[\'"]\)', code)
    functions = re.findall(
        r"@filter\.llm_tool[^\n]*\n(?:[ \t]*@[^\n]*\n)*[ \t]*(?:async )?def ([a-zA-Z0-9_]+)\(",
        code,
    )
    return commands, named + functions


def _knowledge_host(astrbot_data_dir):
    """AstrBot 的 knowledge_base 目录 → 一条知识库宿主。"""
    if not astrbot_data_dir:
        return None
    kb_dir = os.path.join(astrbot_data_dir, "knowledge_base")
    if not os.path.exists(kb_dir):
        return None
    docs = [
        _kb_doc(name, os.path.join(kb_dir, name))
        for name in sorted(os.listdir(kb_dir))
        if not name.endswith(("-shm", "-wal"))
    ]
    return dict(_KB_HOST, submods=[doc for doc in docs if doc])


def _kb_doc(name, path):
    if not os.path.isfile(path):
        return None
    size_kb = round(os.path.getsize(path) / 1024, 1)
    is_db = name.endswith(".db")
    return {
        "id": f"kb_doc_{name.replace('.', '_')}",
        "name": name.replace(".md", "").replace(".db", " 向量库"),
        "description": f"本地知识条目文件：{name} (大小: {size_kb} KB)",
        "author": "本地知识库",
        "version": "v1.0",
        "category": "知识技能",
        "type": "knowledge_doc",
        "endpoint": "",
        "protocol": "SQLite Vector" if is_db else "Markdown RAG",
        "tools": [f"query_{name[:10]}"],
        "commands": [],
        "needs_llm_injection": False,
        "consumes_persona": False,
    }


def _mcp_host(astrbot_data_dir):
    if not astrbot_data_dir:
        return None
    mcp_dir = os.path.join(astrbot_data_dir, "mcp_servers", "image_generator")
    return dict(_MCP_IMAGE_HOST) if os.path.exists(mcp_dir) else None


# ----------------------------------------------------------------------
# 探针
# ----------------------------------------------------------------------
def execute_module_probe(module: dict, timeout: float = 0.6) -> dict:
    """探针：能连端口就探端口，本地组件按文件/类型直接给结论。"""
    target = _probe_target(module)
    if target:
        return _tcp_probe(target[0], target[1], timeout)
    module_id = str(module.get("id", ""))
    if module_id.startswith("astrbot_plugin_"):
        return _probe_plugin_dir(module_id)
    if module_id.startswith("kb_doc_") or module_id == "kb_local_vault":
        return _status_mounted(
            "知识库就绪", "本地知识库总线", "本地 Markdown 知识百科与 SQLite 向量库已就绪"
        )
    if module_id == "mcp_image_generator":
        return _status_mounted(
            "FastMCP 就绪", "FastMCP 服务端",
            "文生图服务代码位于 astrbot/data/mcp_servers/image_generator",
        )
    return _status_mounted("已装载", "中枢内存", "核心系统生命周期常驻组件")


def _probe_target(module):
    """从 endpoint / console_url / url 解析出 (host, port)；解析不出返回 None。"""
    endpoint = module.get("endpoint") or module.get("console_url") or module.get("url") or ""
    if not endpoint:
        return None
    try:
        if "://" in endpoint:
            parsed = urlparse(endpoint)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return parsed.hostname or "127.0.0.1", port
        if ":" in endpoint:
            host, port = endpoint.split(":")[:2]
            return host or "127.0.0.1", int(port)
        if endpoint.isdigit():
            return "127.0.0.1", int(endpoint)
    except Exception:
        return None
    return None


def _tcp_probe(host, port, timeout):
    """TCP 探活：连上=online，连不上/异常都算 offline。"""
    started = time.time()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, int(port)))
        sock.close()
    except Exception as exc:
        return _probe_failed(host, port, started, exc)
    latency = int((time.time() - started) * 1000)
    if result == 0:
        return _probe_online(host, port, latency)
    return _probe_offline(host, port, latency)


def _probe_failed(host, port, started, exc):
    return _probe_result(
        "offline", "red", "探测异常", int((time.time() - started) * 1000),
        f"{host}:{port}", f"网络探测异常: {exc}",
    )


def _probe_online(host, port, latency):
    return _probe_result(
        "online", "green", f"服务正常 ({latency}ms)", latency,
        f"{host}:{port}", f"服务已在端口 {port} 正常监听，双向通信通畅",
    )


def _probe_offline(host, port, latency):
    return _probe_result(
        "offline", "red", "未运行 / 离线", latency,
        f"{host}:{port}", f"端口 {port} 未在监听，请确认对应程序服务是否已启动",
    )


def _probe_result(status, color, text, latency, target, hint):
    return {
        "status": status,
        "badge_color": color,
        "status_text": text,
        "latency_ms": latency,
        "target": target,
        "diag_hint": hint,
        "checked_at": int(time.time()),
    }


def _status_mounted(text, target, hint):
    return _probe_result("mounted", "green", text, 0, target, hint)


def _probe_plugin_dir(module_id):
    plugins_dir = resolve_astrbot_plugins_dir()
    found = bool(plugins_dir and os.path.isdir(os.path.join(plugins_dir, module_id)))
    if found:
        return _status_mounted(
            "插件已就绪", "AstrBot 插件管线", "插件代码与 metadata.yaml 均位于本地目录，已挂载"
        )
    return _probe_result(
        "offline", "red", "文件缺失", 0, "本地总线",
        f"未在 astrbot/data/plugins 中找到插件目录 {module_id}",
    )


# ----------------------------------------------------------------------
# 仪表盘总览
# ----------------------------------------------------------------------
def get_modules_overview():
    """仪表盘用：拓扑树 + 每个模块的探针状态。"""
    topology = get_topology_overview()
    states = get_module_states()
    categories = get_custom_module_configs().get("categories", {})
    modules = []
    for host in topology.get("hosts", []):
        modules.append(_module_entry(host, states, categories, "anan_hub_core", True))
        for sub in host.get("submods", []):
            modules.append(_module_entry(sub, states, categories, host.get("id"), False))
    return {
        "tree": topology,
        "modules": modules,
        "total": len(modules),
        "active_count": sum(1 for module in modules if module["is_active"]),
    }


def _module_entry(module, states, categories, parent_id, is_host):
    """一个模块的展示字段：身份 + 能力 + 探针状态。"""
    module_id = module.get("id")
    category = categories.get(module_id) or module.get("category", "自定义组件")
    entry = {
        **_module_identity(module, module_id, category, states, parent_id, is_host),
        **_module_capabilities(module, module_id, category),
    }
    entry["health"] = execute_module_probe(module)
    return entry


def _module_identity(module, module_id, category, states, parent_id, is_host):
    """身份字段：是谁、属于谁、什么类型。"""
    return {
        "id": module_id,
        "name": module.get("name", module_id),
        "description": module.get("description", ""),
        "desc": module.get("description", ""),
        "author": module.get("author", "官方内置"),
        "version": module.get("version", "1.0.0"),
        "category": category,
        "type": module.get("type", "plugin"),
        "is_active": states.get(module_id, True),
        "is_host": is_host,
        "parent_id": parent_id,
    }


def _module_capabilities(module, module_id, category):
    """能力字段：端点、工具、命令、注入策略。"""
    return {
        "endpoint": module.get("endpoint", ""),
        "console_url": module.get("console_url") or module.get("endpoint", ""),
        "protocol": module.get("protocol", "标准协议"),
        "tools": module.get("tools", []),
        "commands": module.get("commands", []),
        "config_template": module.get("config_template", {}),
        "is_ai_direct": module.get("is_ai_direct", (category != "自定义组件")),
        "is_custom": (category == "自定义组件" or module.get("is_custom", False)),
        "needs_llm_injection": module.get("needs_llm_injection", (module_id == "astrbot")),
        "consumes_persona": module.get("consumes_persona", (module_id == "astrbot")),
    }
