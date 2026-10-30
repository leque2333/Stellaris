# -*- coding: utf-8 -*-
"""
Stellaris Core - 记忆库 (Memory Library)

这里只放 **L3 详情**：完整消息、工具入参/出参、模型请求/响应、附件引用。
事实、画像、日记属于 `agent_core.profile`（画像），不在这里。

三层分工：
- 时间轴 Timeline       : L1 调用索引 + L2 事件摘要
- 记忆库 MemoryLibrary  : L3 详情（本文件）
- 画像 Profile          : 事实 / 档案 / 好感 / 日记
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..storage import Store


class MemoryLibrary:
    """L3 详情库：只负责存原文与按需读取"""

    def __init__(self, store: Store):
        self.store = store

    def save_detail(
        self,
        kind: str,
        content: Any,
        *,
        invocation_id: Optional[str] = None,
        event_id: Optional[str] = None,
        mime: str = "application/json",
        expires_at: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self.store.save_detail(
            kind, content,
            invocation_id=invocation_id, event_id=event_id, mime=mime,
            expires_at=expires_at, metadata=metadata,
        )

    def get_detail(self, detail_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get_detail(detail_id)

    def list_details(
        self,
        *,
        event_id: Optional[str] = None,
        invocation_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        return self.store.list_details(
            event_id=event_id, invocation_id=invocation_id, limit=limit
        )
