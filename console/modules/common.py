# -*- coding: utf-8 -*-
"""
Stellaris Console - Common Paths and Utilities
中枢控制台基础路径与通用工具

宿主路径通过可选适配器解析；中枢核心不依赖任何宿主目录。
"""

import os
import re
import shutil
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "data")
HUB_DB_PATH = os.path.join(DATA_DIR, "memory.db")
MODULES_STATE_PATH = os.path.join(DATA_DIR, "modules_state.json")
CUSTOM_TOOLS_PATH = os.path.join(DATA_DIR, "custom_tools.json")
TOPOLOGY_CONFIG_PATH = os.path.join(DATA_DIR, "topology_config.json")
PERSONA_PATH = os.path.join(DATA_DIR, "persona.md")
LEGACY_PERSONA_PATH = os.path.join(DATA_DIR, "anan_persona.md")
PERSONA_RULES_PATH = os.path.join(DATA_DIR, "persona_rules.json")
MODELS_CONFIG_PATH = os.path.join(DATA_DIR, "models_config.json")
UPSTREAM_CACHE_PATH = os.path.join(DATA_DIR, "upstream_models_cache.json")
MODULES_PERMISSIONS_PATH = os.path.join(DATA_DIR, "modules_permissions.json")
COMPUTE_PROVIDERS_PATH = os.path.join(DATA_DIR, "compute_providers.json")


def ensure_persona_file():
    """把旧版 anan_persona.md 平滑迁移到通用 persona.md；人格内容本身不变。"""
    if not os.path.exists(PERSONA_PATH) and os.path.exists(LEGACY_PERSONA_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        shutil.copy2(LEGACY_PERSONA_PATH, PERSONA_PATH)
    return PERSONA_PATH


def _host_adapter():
    from console.adapters import AstrBotAdapter

    return AstrBotAdapter()


def resolve_hub_db_path():
    env = os.getenv("MEMORY_HUB_DB_PATH", "").strip()
    if env:
        return env
    return HUB_DB_PATH


def resolve_custom_tools_path():
    env = os.getenv("HUB_CUSTOM_TOOLS_PATH", "").strip()
    if env:
        return env
    return CUSTOM_TOOLS_PATH


def resolve_astrbot_plugins_dir():
    return _host_adapter().plugins_dir()


def resolve_astrbot_mcp_path():
    return _host_adapter().mcp_path()


def resolve_astrbot_cmd_config_path():
    return _host_adapter().cmd_config_path()


def resolve_astrbot_v4_db_path():
    return _host_adapter().v4_db_path()


def get_db_connection():
    """只读/分析连接：路径统一由存储层决定，避免多库候选漂移"""
    from agent_core.storage import get_store

    db_path = str(get_store().path)
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def sanitize_clean_text(val):
    if val is None:
        return ""
    text = str(val)
    # 彻底剥离任何残留的 ANSI 转义序列、特殊不可见控制字符
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return text.strip()
