# -*- coding: utf-8 -*-
"""模块公开清单：只写身份/能力，不写 Cookie。中枢扫描优先读它。"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


def build_manifest(config: Dict[str, Any]) -> Dict[str, Any]:
    hub = config.get("hub") or {}
    bcfg = config.get("bilibili") or {}
    acfg = config.get("actions") or {}
    uid = str(bcfg.get("bot_uid") or bcfg.get("dedeuserid") or "").strip()
    account_name = str(bcfg.get("master_name") or "").strip()
    actions = []
    if acfg.get("enabled", True):
        host = str(acfg.get("host") or "127.0.0.1")
        port = int(acfg.get("port") or 18901)
        base = f"http://{host}:{port}"
        actions = [
            {
                "name": "recommend",
                "description": "从 B站找几个当前可能合适的视频或话题，返回标题、链接和推荐理由。只读，不评论、不发布。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "根据当前对话提炼的检索意图"},
                        "limit": {"type": "integer", "description": "最多返回几个"},
                    },
                },
                "endpoint": f"{base}/actions/recommend",
                "method": "POST",
                "risk": "read",
                "timeout": 30,
            },
            {
                "name": "patrol",
                "description": "执行一轮 B站刷视频 / 评论巡检，返回本轮结果。是否真正发布由模块配置和权限决定。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "dry_run": {"type": "boolean", "description": "只评估不发布"},
                    },
                },
                "endpoint": f"{base}/actions/patrol",
                "method": "POST",
                "risk": "write",
                "timeout": 180,
            },
            {
                "name": "status",
                "description": "读取 B站模块的登录、时段、评论配额和追踪状态。",
                "input_schema": {"type": "object", "properties": {}},
                "endpoint": f"{base}/actions/status",
                "method": "GET",
                "risk": "read",
                "timeout": 20,
            },
        ]
    return {
        "id": str(hub.get("host_id") or "bilibili"),
        "name": str(hub.get("host_name") or "B站"),
        "platform": "bilibili",
        "version": "0.1",
        "account": {
            "uid": uid,
            "name": account_name,
        },
        "capabilities": ["patrol", "comment", "reply"],
        "events": ["video.comment", "video.reply"],
        "actions": actions,
        "action_token": str(acfg.get("token") or ""),
        "updated_at": time.time(),
    }


def manifest_path() -> Path:
    return Path(__file__).parent / "identity" / "module.json"


def write_manifest(config: Dict[str, Any]) -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(build_manifest(config), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".bili_manifest_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path
