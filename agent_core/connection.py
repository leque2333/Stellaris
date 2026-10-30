# -*- coding: utf-8 -*-
"""连接中心树：模块 / MCP / 服务

映射关系：
- hosts        → 模块
- components   → 模块声明的组件
- custom_tools → 自定义组件（按 module_id 归到模块）
- services     → MCP / 工具服务

客户端与事件源已废弃，不再进入连接树。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _custom_module_id(item: Dict[str, Any], module_ids: List[str]) -> str:
    explicit = str(item.get("module_id") or "").strip()
    if explicit in module_ids:
        return explicit
    haystack = " ".join(
        str(item.get(key) or "") for key in ("id", "name", "protocol")
    ).lower()
    for module_id in module_ids:
        if module_id and module_id.lower() in haystack:
            return module_id
    return ""


def _custom_is_self(item: Dict[str, Any], module_id: str) -> bool:
    if item.get("is_self"):
        return True
    return str(item.get("id") or "").strip() == module_id


def _component_view(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "component_id": str(item.get("component_id") or ""),
        "name": str(item.get("name") or item.get("component_id") or ""),
        "version": str(item.get("version") or ""),
        "kind": str(item.get("kind") or "secondary"),
        "path": str(item.get("path") or ""),
        "entry": str(item.get("entry") or ""),
        "config_schema": str(item.get("config_schema") or ""),
        "category": str(item.get("category") or "二级插件"),
        "capabilities": list(item.get("capabilities") or []),
        "status": str(item.get("status") or "active"),
        "source": str(item.get("source") or "auto"),
    }


def _custom_view(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "category": str(item.get("category") or "自定义组件"),
        "protocol": str(item.get("protocol") or ""),
        "console_url": str(item.get("console_url") or ""),
        "desc": str(item.get("desc") or ""),
        "is_active": bool(item.get("is_active", True)),
        "module_id": str(item.get("module_id") or ""),
    }


def _mcp_view(
    service: Optional[Dict[str, Any]] = None,
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    service = service or {}
    session = session or {}
    manifest = service.get("manifest") or {}
    tools = session.get("tools") or manifest.get("tools") or []
    return {
        "service_id": str(service.get("service_id") or (f"mcp_{session.get('name')}" if session else "")),
        "name": str(service.get("name") or session.get("name") or service.get("service_id") or ""),
        "kind": str(service.get("kind") or "mcp"),
        "endpoint": str(service.get("endpoint") or ("mcp://" + str(session.get("name") or "") if session else "")),
        "status": str(service.get("status") or ("active" if session else "unknown")),
        "capabilities": list(service.get("capabilities") or []),
        "transport": str(session.get("transport") or manifest.get("transport") or ""),
        "tools": list(tools),
        "live": bool(session),
    }


def _module_contexts(
    module_id: str,
    host: Dict[str, Any],
    contexts: List[Dict[str, Any]],
    bindings: List[Dict[str, Any]],
    archive_names: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """模块内部的信息卡（联系人/群/账号），带上当前绑定档案"""
    platform = str(host.get("platform") or "")
    archive_names = archive_names or {}
    binding_map = {
        (item.get("platform"), item.get("external_id")): item for item in bindings
    }
    result: List[Dict[str, Any]] = []
    for context in contexts:
        status = str(context.get("status") or "active")
        external_type = str(context.get("external_type") or "")
        if status == "ignored":
            continue
        # 宿主自己的信息卡即使暂时 stale 也要显示；联系人/群仍只显示 active
        if status != "active" and external_type != "host":
            continue
        belongs = str(context.get("host_id") or "") == module_id
        if not belongs:
            source = str(context.get("source") or "").lower()
            belongs = bool(module_id) and module_id.lower() in source
        if not belongs and not str(context.get("host_id") or "") and platform and context.get("platform") == platform:
            belongs = True
        if not belongs:
            continue
        binding = binding_map.get((context.get("platform"), context.get("external_id")))
        display_name = str(context.get("display_name") or "")
        if binding:
            archive_name = archive_names.get(str(binding.get("archive_id") or ""))
            if archive_name and (not display_name or display_name.isdigit()):
                display_name = archive_name
        metadata = context.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        result.append({
            "context_id": str(context.get("context_id") or ""),
            "platform": str(context.get("platform") or ""),
            "external_id": str(context.get("external_id") or ""),
            "external_type": str(context.get("external_type") or ""),
            "display_name": display_name,
            "source": str(context.get("source") or ""),
            "last_seen": context.get("last_seen") or 0,
            "metadata": metadata,
            "binding": (
                {
                    "binding_id": str(binding.get("binding_id") or ""),
                    "archive_id": str(binding.get("archive_id") or ""),
                    "confidence": binding.get("confidence") or 0,
                    "verified_by": str(binding.get("verified_by") or "manual"),
                }
                if binding else None
            ),
        })
    result.sort(key=lambda item: item.get("last_seen") or 0, reverse=True)
    return result


def _ignored_contexts(
    module_id: str,
    host: Dict[str, Any],
    contexts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """已忽略的信息卡单独列出，供手误恢复。"""
    platform = str(host.get("platform") or "")
    result = []
    for context in contexts:
        if str(context.get("status") or "") != "ignored":
            continue
        belongs = str(context.get("host_id") or "") == module_id
        if not belongs and not str(context.get("host_id") or "") and platform and context.get("platform") == platform:
            belongs = True
        if not belongs:
            continue
        result.append({
            "context_id": str(context.get("context_id") or ""),
            "platform": str(context.get("platform") or ""),
            "external_id": str(context.get("external_id") or ""),
            "display_name": str(context.get("display_name") or ""),
            "source": str(context.get("source") or ""),
            "last_seen": context.get("last_seen") or 0,
        })
    result.sort(key=lambda item: item.get("last_seen") or 0, reverse=True)
    return result


def _identity_view(
    module_id: str,
    host: Dict[str, Any],
    contexts: List[Dict[str, Any]],
    bindings: List[Dict[str, Any]],
) -> Dict[str, Any]:
    platform = str(host.get("platform") or "")
    external_id = str(host.get("internal_uid") or "")
    context = next(
        (
            item for item in contexts
            if str(item.get("host_id") or "") == module_id
            and str(item.get("external_type") or "") == "host"
        ),
        None,
    )
    if context is None and external_id:
        context = next(
            (
                item for item in contexts
                if str(item.get("platform") or "") == platform
                and str(item.get("external_id") or "") == external_id
            ),
            None,
        )
    binding = None
    if context:
        binding = next(
            (
                item for item in bindings
                if item.get("platform") == context.get("platform")
                and item.get("external_id") == context.get("external_id")
            ),
            None,
        )
    return {
        "context_id": str((context or {}).get("context_id") or ""),
        "platform": str((context or {}).get("platform") or platform),
        "external_id": str((context or {}).get("external_id") or external_id),
        "display_name": str((context or {}).get("display_name") or ""),
        "binding": (
            {
                "binding_id": str(binding.get("binding_id") or ""),
                "archive_id": str(binding.get("archive_id") or ""),
                "confidence": binding.get("confidence") or 0,
                "verified_by": str(binding.get("verified_by") or "manual"),
            }
            if binding else None
        ),
    }


def build_connection_tree(
    store: Any,
    customs: Optional[List[Dict[str, Any]]] = None,
    mcp_sessions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    hosts = [
        h for h in store.list_hosts()
        if str(h.get("source") or "") != "client_v1"
        and str((h.get("manifest") or {}).get("kind") or "") != "client"
    ]
    components = list(store.list_components())
    services = list(store.list_services())
    contexts = list(store.list_contexts())
    bindings = list(store.list_bindings())
    archive_names = {
        str(item.get("archive_id") or ""): str(item.get("display_name") or "")
        for item in store.list_archives()
    }
    customs = [c for c in (customs or []) if isinstance(c, dict)]
    sessions = [s for s in (mcp_sessions or []) if isinstance(s, dict)]
    module_ids = [str(h.get("host_id") or "") for h in hosts]

    modules: List[Dict[str, Any]] = []
    for host in hosts:
        module_id = str(host.get("host_id") or "")
        all_mod_components = [
            _component_view(c) for c in components if str(c.get("host_id") or "") == module_id
        ]
        # 已缺失的二级插件不再展示，只保留计数提示
        mod_components = [c for c in all_mod_components if c.get("status") != "missing"]
        missing_components = len(all_mod_components) - len(mod_components)
        mod_customs = []
        for item in customs:
            owner = _custom_module_id(item, module_ids)
            if owner == module_id and not _custom_is_self(item, module_id):
                mod_customs.append(_custom_view(item))

        manifest = host.get("manifest") or {}
        modules.append({
            "module_id": module_id,
            "name": str(host.get("name") or module_id),
            "platform": str(host.get("platform") or ""),
            "internal_uid": str(host.get("internal_uid") or ""),
            "root": str(host.get("root") or ""),
            "source": str(host.get("source") or "auto"),
            "status": "registered" if host.get("platform") and host.get("internal_uid") else "partial",
            "manifest": manifest,
            "components": mod_components,
            "custom_components": mod_customs,
            "identity": _identity_view(module_id, host, contexts, bindings),
            "contexts": _module_contexts(module_id, host, contexts, bindings, archive_names),
            "ignored_contexts": _ignored_contexts(module_id, host, contexts),
            "counts": {
                "components": len(mod_components),
                "missing_components": missing_components,
                "custom_components": len(mod_customs),
            },
        })

    unassigned_customs = [
        _custom_view(item)
        for item in customs
        if _custom_module_id(item, module_ids) not in module_ids
    ]

    mcp_list: List[Dict[str, Any]] = []
    service_list: List[Dict[str, Any]] = []
    used_sessions = set()
    session_by_name = {str(s.get("name") or ""): s for s in sessions}
    for service in services:
        manifest = service.get("manifest") or {}
        session = session_by_name.get(str(manifest.get("mcp_name") or "")) or session_by_name.get(
            str(service.get("name") or "")
        )
        if session is not None:
            used_sessions.add(id(session))
        entry = _mcp_view(service, session)
        if entry["kind"] == "mcp":
            mcp_list.append(entry)
        else:
            service_list.append(entry)
    for session in sessions:
        if id(session) in used_sessions:
            continue
        mcp_list.append(_mcp_view(None, session))

    return {
        "ok": True,
        "object": "connection_tree",
        "modules": modules,
        "mcp": mcp_list,
        "services": service_list,
        "unassigned": {
            "custom_components": unassigned_customs,
        },
        "summary": {
            "modules": len(modules),
            "mcp": len(mcp_list),
            "services": len(service_list),
            "components": len(components),
            "custom_components": len(customs),
        },
    }
