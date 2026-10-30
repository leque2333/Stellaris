# -*- coding: utf-8 -*-
"""工具设置管理：直接改 tool_shelves.json。

控制台「工具」页用。两个开关：
    mode      direct（主模型直接用） / filtered（执行模型筛选）
    approval  自动调用是否需要管理员提权
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ShelfConfig


# 工具来源：中枢不提供工具，这些全部来自 AstrBot（MCP / 插件 / 内置）
_SOURCE_MAP = {
    "generate_image": "image-generator",
    "check_image_quota": "image-generator",
    "you-search": "you-search",
    "you-contents": "you-search",
    "you-discover": "you-search",
    "you-balance": "you-search",
    "you_search_clean": "hub-tools",
    "you_contents_clean": "hub-tools",
    "call_raw_tool": "hub_tool_executor",
    "future_task": "AstrBot 内置",
    "send_message_to_user": "AstrBot 内置",
    "control_host_pc_via_antigravity": "MCP（executor 隐藏）",
}


def tool_source(name: str) -> str:
    return _SOURCE_MAP.get(str(name), "AstrBot")


def _config_path(cfg: Optional[ShelfConfig] = None) -> Path:
    if cfg is not None:
        return cfg.path
    return Path(os.getenv("TOOL_SHELVES_CONFIG_PATH", "") or "data/tool_shelves.json")


def _read(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _category_of(data: Dict[str, Any], name: str) -> str:
    for cat, spec in (data.get("categories") or {}).items():
        if not isinstance(spec, dict):
            continue
        if name in [str(x) for x in (spec.get("tools") or [])]:
            return str(cat)
    return ""


def list_tools(cfg: Optional[ShelfConfig] = None) -> Dict[str, Any]:
    """所有已知工具 + 它们当前的设置。"""
    path = _config_path(cfg)
    data = _read(path)
    always = {str(x) for x in (data.get("always_available") or [])}
    approval = {str(x) for x in (data.get("approval_tools") or [])}
    categories = data.get("categories") or {}

    # 1) 配置里登记的工具
    entries: Dict[str, Dict[str, Any]] = {}
    for name in always:
        entries[name] = {
            "name": name,
            "mode": "direct",
            "category": "",
            "approval": name in approval,
            "source": "config",
            "source_group": tool_source(name),
            "description": "",
        }
    for cat, spec in categories.items():
        if not isinstance(spec, dict):
            continue
        for name in (spec.get("tools") or []):
            name = str(name)
            if name in entries:
                continue
            entries[name] = {
                "name": name,
                "mode": "filtered",
                "category": str(cat),
                "approval": name in approval,
                "source": "config",
                "description": "",
            }

    # 2) 网关见过的工具（配置没登记的）
    try:
        from . import registry

        for name, info in registry.all_tools().items():
            if name in entries:
                continue
            entries[name] = {
                "name": name,
                "mode": "unknown",
                "category": "",
                "approval": name in approval,
                "source": "observed",
                "description": str(info.get("desc") or ""),
            }
    except Exception:
        pass

    # 3) 提权列表里有、但配置和 registry 都没有的
    for name in approval:
        if name not in entries:
            entries[name] = {
                "name": name,
                "mode": "unknown",
                "category": "",
                "approval": True,
                "source": "config",
                "description": "",
            }

    for item in entries.values():
        item.setdefault("source_group", tool_source(item["name"]))
    items = sorted(entries.values(), key=lambda x: (x["mode"] != "direct", x["category"], x["name"]))
    cat_meta = [
        {
            "name": str(cat),
            "label": str((spec or {}).get("label") or cat),
            "brief": str((spec or {}).get("brief") or ""),
        }
        for cat, spec in categories.items()
        if isinstance(spec, dict)
    ]
    return {
        "ok": True,
        "config_path": str(path),
        "categories": cat_meta,
        "always_available": sorted(always),
        "approval_tools": sorted(approval),
        "tools": items,
        "count": len(items),
    }


def update_tool(
    name: str,
    *,
    mode: Optional[str] = None,
    approval: Optional[bool] = None,
    category: Optional[str] = None,
    cfg: Optional[ShelfConfig] = None,
) -> Dict[str, Any]:
    """更新一个工具的设置，写回配置并热加载。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("name 必填")
    path = _config_path(cfg)
    data = _read(path)
    if not data:
        raise ValueError(f"配置读取失败: {path}")

    always: List[str] = [str(x) for x in (data.get("always_available") or [])]
    approval_list: List[str] = [str(x) for x in (data.get("approval_tools") or [])]
    cats: Dict[str, Any] = data.get("categories") or {}

    if mode in ("direct", "filtered"):
        # 先从所有位置摘掉
        always = [x for x in always if x != name]
        for spec in cats.values():
            if isinstance(spec, dict):
                spec["tools"] = [str(x) for x in (spec.get("tools") or []) if str(x) != name]
        if mode == "direct":
            always.append(name)
        else:
            target = str(category or "").strip() or _category_of(data, name)
            if not target or not isinstance(cats.get(target), dict):
                # 没有合适类目就用第一个；一个类目都没有就建一个
                target = next((k for k, v in cats.items() if isinstance(v, dict)), "")
                if not target:
                    target = "other"
                    cats[target] = {
                        "label": "其他",
                        "brief": "未归类的工具",
                        "keywords": [],
                        "tools": [],
                    }
            cats[target].setdefault("tools", [])
            cats[target]["tools"].append(name)

    if approval is not None:
        approval_list = [x for x in approval_list if x != name]
        if approval:
            approval_list.append(name)

    data["always_available"] = always
    data["approval_tools"] = sorted(set(approval_list))
    data["categories"] = cats
    data["version"] = data.get("version") or 1
    _write(path, data)

    if cfg is not None:
        try:
            cfg.reload(force=True)
        except Exception:
            pass

    cat = _category_of(data, name)
    return {
        "ok": True,
        "tool": {
            "name": name,
            "mode": "direct" if name in always else ("filtered" if cat else "unknown"),
            "category": cat,
            "approval": name in set(approval_list),
            "source": "config",
        },
        "updated_at": time.time(),
    }


__all__ = ["list_tools", "update_tool"]
