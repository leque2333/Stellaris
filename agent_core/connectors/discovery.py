# -*- coding: utf-8 -*-
"""自动嗅探服务：跑连接器 → 登记上下文 → 标记失效 → 绑定管理员"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..identity import IdentityResolver
from ..storage import Store
from .base import Connector, ConnectorReport
from .spec_connector import SpecConnector


class DiscoveryService:
    def __init__(self, store: Store, connectors: Optional[List[Connector]] = None):
        self.store = store
        self.connectors = connectors or [SpecConnector()]
        self.last_result: Dict[str, Any] = {}

    def scan(self) -> Dict[str, Any]:
        now = time.time()
        reports = []
        seen = set()

        for connector in self.connectors:
            try:
                report = connector.discover()
            except Exception as exc:
                report = ConnectorReport(
                    connector=connector.name, ok=False, source_path="", error=str(exc)
                )
            reports.append(report)
            if not getattr(report, "ok", False):
                continue
            for ctx in report.contexts:
                seen.add((ctx.platform, ctx.external_id))
                self.store.upsert_context(
                    ctx.platform,
                    ctx.external_id,
                    external_type=ctx.external_type,
                    display_name=ctx.display_name,
                    source=ctx.source,
                    evidence=ctx.evidence,
                    metadata=ctx.metadata,
                    seen_at=now,
                )

        stale = 0
        if all(getattr(r, "ok", False) for r in reports):
            for ctx in self.store.list_contexts(limit=2000):
                source = str(ctx.get("source") or "")
                if "execution_ai" in source:
                    continue
                if (ctx["platform"], ctx["external_id"]) not in seen and ctx["status"] == "active":
                    self.store.set_context_status(ctx["context_id"], "stale")
                    stale += 1

        auto_bound = False
        admin_id = IdentityResolver.get_master_qq()
        admin_context = next(
            (
                c for c in self.store.list_contexts(platform="qq", limit=2000)
                if c["external_id"] == f"onebot_napcat:FriendMessage:{admin_id}"
            ),
            None,
        )
        if admin_context:
            self.store.bind_context(
                "qq", admin_context["external_id"], admin_id,
                confidence=0.9, verified_by="auto_admin",
            )
            auto_bound = True

        result = {
            "ok": all(getattr(r, "ok", False) for r in reports),
            "scanned_at": now,
            "reports": [r.to_dict() if hasattr(r, "to_dict") else r for r in reports],
            "contexts": len(seen),
            "stale": stale,
            "auto_bound_admin": auto_bound,
        }
        self.last_result = result
        return result

    def status(self) -> Dict[str, Any]:
        if not self.last_result:
            return {"scanned_at": None, "contexts": 0, "reports": []}
        return self.last_result


def run_forever(store: Store, interval: float = 60.0) -> None:
    service = DiscoveryService(store)
    while True:
        try:
            service.scan()
        except Exception:
            pass
        time.sleep(interval)
