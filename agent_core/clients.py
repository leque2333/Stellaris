# -*- coding: utf-8 -*-
"""
Stellaris Core - 外部客户端密钥与通道画像 (Client Registry)

解决三个问题：
1. 谁在调用中枢（每个软件一个 Key，而不是所有客户端共用一个口子）
2. 调用者默认是谁（缺省不再一律当成 master）
3. 该客户端能读多少记忆、能写多少时间轴、每分钟多少请求

配置文件默认 data/clients.json；不存在时保持旧行为（兼容）。
可用 HUB_CLIENTS_PATH 指定路径。
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ClientProfile:
    id: str = "legacy"
    name: str = "未登记客户端"
    channel: Optional[str] = None
    default_actor: str = "guest"           # master | guest（默认 guest：无配置不等于主人）
    allow_recall_memory: bool = True
    allow_ingest_timeline: bool = True
    rate_limit_per_minute: int = 0         # 0 = 不限制
    keys: List[str] = field(default_factory=list)
    tool_permissions: Dict[str, Any] = field(default_factory=dict)  # role/allow/deny


@dataclass
class AuthResult:
    profile: ClientProfile
    error: Optional[str] = None            # missing_key / invalid_key / rate_limited


class ClientRegistry:
    """线程安全的客户端注册表，支持文件变更热加载"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path or os.getenv("HUB_CLIENTS_PATH") or (Path(__file__).resolve().parent.parent / "data" / "clients.json"))
        self.require_key = True  # 安全默认：没配置也要 Key
        self._clients: List[ClientProfile] = []
        self._mtime: Optional[float] = None
        self._configured = False
        self._lock = threading.Lock()
        self._hits: Dict[str, deque] = defaultdict(deque)
        self._reload(force=True)

    # ---------------- 加载 ----------------
    def _reload(self, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
        except OSError:
            mtime = None
        if not force and mtime == self._mtime:
            return
        with self._lock:
            self._mtime = mtime
            if mtime is None:
                # 没有配置文件：没有任何合法 Key，按“必须带 Key”处理
                self.require_key = True
                self._clients = []
                self._configured = False
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                # 文件坏了和不存是一个下场：宁可拒绝，也不要悄悄放行
                self.require_key = True
                self._clients = []
                self._configured = False
                return

            self._configured = True
            # 默认必须带 Key：只有显式写 "require_key": false 才允许匿名
            self.require_key = bool(data.get("require_key", True))
            clients: List[ClientProfile] = []
            for item in data.get("clients", []):
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id") or "").strip()
                if not cid:
                    continue
                keys = item.get("keys") or ([item["key"]] if item.get("key") else [])
                perms = item.get("tool_permissions")
                clients.append(ClientProfile(
                    id=cid,
                    name=str(item.get("name") or cid),
                    channel=item.get("channel"),
                    # 没写 default_actor 的客户端按 guest 处理，不再默认成主人
                    default_actor=str(item.get("default_actor") or "guest"),
                    allow_recall_memory=bool(item.get("allow_recall_memory", True)),
                    allow_ingest_timeline=bool(item.get("allow_ingest_timeline", True)),
                    rate_limit_per_minute=int(item.get("rate_limit_per_minute", 0) or 0),
                    keys=[str(k) for k in keys if k],
                    tool_permissions=dict(perms) if isinstance(perms, dict) else {},
                ))
            self._clients = clients

    # ---------------- 解析 ----------------
    def resolve(self, token: Optional[str]) -> AuthResult:
        """把请求里的 Key 解析成客户端画像。

        安全默认（对应复核报告 §3）：**没有 clients.json 就没有主人**。
        配置缺失时一律按 `missing_key` 拒绝，绝不返回一个隐含 master 的画像；
        显式写了 `require_key=false` 才允许无 Key 访问，且此时画像仍是 guest。
        """
        self._reload()
        token = (token or "").strip()

        if not self._configured:
            # 连配置都没有：不存在任何合法 Key，拒绝而不是放行
            return AuthResult(ClientProfile(id="unconfigured"), error="missing_key")

        if token:
            for client in self._clients:
                for key in client.keys:
                    if key and hmac.compare_digest(token, key):
                        return AuthResult(client)
            return AuthResult(ClientProfile(id="anonymous"), error="invalid_key")

        if self.require_key:
            return AuthResult(ClientProfile(id="anonymous"), error="missing_key")

        # 显式关闭 require_key：允许匿名，但画像固定是 guest，不能借此成为主人
        return AuthResult(ClientProfile(id="legacy", default_actor="guest"))

    def allow_request(self, client_id: str, limit: int) -> bool:
        if not limit or limit <= 0:
            return True
        now = time.time()
        with self._lock:
            q = self._hits[client_id]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def list_clients(self) -> List[Dict[str, Any]]:
        self._reload()
        return [{
            "id": c.id,
            "name": c.name,
            "channel": c.channel,
            "default_actor": c.default_actor,
            "allow_recall_memory": c.allow_recall_memory,
            "allow_ingest_timeline": c.allow_ingest_timeline,
            "rate_limit_per_minute": c.rate_limit_per_minute,
            "key_count": len(c.keys),
            "tool_permissions": dict(c.tool_permissions or {}),
        } for c in self._clients]


# 全局单例
client_registry = ClientRegistry()
