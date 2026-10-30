# -*- coding: utf-8 -*-
"""
Stellaris Core - 人格身份解析器 (Persona Identity Resolver)

人格名称属于可替换的数据，不属于项目代码：
- 优先读 `data/persona.md`
- 兼容旧文件 `data/anan_persona.md`
- 最后回退到通用 `agent_core/persona_template.md`
- 环境变量 `BOT_NAME` 可强制覆盖
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

PERSONA_CANDIDATES = [
    DATA_DIR / "persona.md",
    DATA_DIR / "anan_persona.md",
    BASE_DIR / "agent_core" / "persona_template.md",
]

DEFAULT_PERSONA_NAME = "助手"

_lock = threading.Lock()
_cache: dict[str, object] = {"path": "", "mtime": None, "name": DEFAULT_PERSONA_NAME}


def get_persona_path() -> Optional[Path]:
    """返回当前生效的人设卡路径；没有文件时返回 None。"""
    for path in PERSONA_CANDIDATES:
        if path.exists() and path.is_file():
            return path
    return None


def _clean_name(value: str) -> str:
    name = re.sub(r"[\s\"'“”‘’]+", "", value or "")
    name = name.strip("：:，,。.、-—|")
    return name


def parse_persona_name(text: str) -> str:
    """从人设卡正文里解析角色名；解析不到就返回通用默认名。"""
    if not text:
        return DEFAULT_PERSONA_NAME

    # YAML front matter: name: 星野
    m = re.search(r"(?im)^\s*name\s*[:：]\s*[\"']?([^\"'\n]+)", text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name

    # "# 人设：星野" / "# 角色：星野"
    m = re.search(r"(?m)^#\s*(?:人设|角色|persona)\s*[:：]\s*([^\n【】]+)", text, re.I)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name

    # "# 【角色名：核心系统设定集...】"
    m = re.search(r"(?m)^#\s*【\s*([^】\n：:（(]+)", text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name

    # "# 星野" / "# 星野·设定"
    m = re.search(r"(?m)^#\s*([^\n【】：:（(]+)", text)
    if m:
        name = _clean_name(m.group(1))
        if name and name not in {"人设", "角色", "persona"}:
            return name

    # "- **身份与称谓**：星野（..."
    m = re.search(r"(?m)^\s*[-*]\s*\*\*身份与称谓\*\*\s*[:：]\s*([^\s（(，,。]+)", text)
    if m:
        name = _clean_name(m.group(1))
        if name:
            return name

    return DEFAULT_PERSONA_NAME


def get_persona_name(default: str = DEFAULT_PERSONA_NAME) -> str:
    """读取当前人格名称；人设文件被替换后自动随 mtime 失效缓存。"""
    env_name = (os.getenv("BOT_NAME") or "").strip()
    if env_name:
        return env_name

    path = get_persona_path()
    if path is None:
        return default

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return default

    with _lock:
        if _cache.get("path") == str(path) and _cache.get("mtime") == mtime:
            return str(_cache.get("name") or default)

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default

    name = parse_persona_name(text) or default

    with _lock:
        _cache.update({"path": str(path), "mtime": mtime, "name": name})
    return name


def clear_persona_cache() -> None:
    """测试/热替换人设卡后手动清缓存。"""
    with _lock:
        _cache.update({"path": "", "mtime": None, "name": DEFAULT_PERSONA_NAME})


# 兼容旧调用
_parse_persona_name = parse_persona_name

