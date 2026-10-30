# -*- coding: utf-8 -*-
"""
Stellaris Core - 闲时反思与周期固化引擎 (Reflection Engine)
借鉴：斯坦福 Generative Agents (Park et al.) 反思机制
职责：
1. 监控高频经历流水 (agent_timeline)，防止上下文爆破与流水账无限膨胀
2. 闲时对历史事件切片聚类、提炼与摘要，生成结构化《经历日记》(memory_digests)
3. 打通跨越 LIMIT 200 硬限制的长程记忆检索视界

存储细节已下沉到 agent_core.storage。
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..storage import Store

logger = logging.getLogger("ReflectionEngine")


class ReflectionEngine:
    """记忆反思与日记固化引擎"""

    def __init__(self, store: Store):
        self.store = store
        self.dal = store  # 兼容旧属性名

    def consolidate_episodes(
        self, min_events: int = 5, older_than_hours: float = 12.0
    ) -> Optional[Dict[str, Any]]:
        """对已发生一定时间的高频琐碎经历做反思聚类，固化为日记节点"""
        now_ts = time.time()
        cutoff_ts = now_ts - (older_than_hours * 3600.0)

        undigested = self.store.list_undigested_before(cutoff_ts, limit=100)
        if len(undigested) < min_events:
            return None

        first_ts = float(undigested[0].timestamp)
        date_str = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d")

        narratives = [
            f"[{e.time_str[-8:]}] ({e.source_module}) {e.narrative}" for e in undigested
        ]
        summary = f"【{date_str} 经历总结】共沉淀 {len(undigested)} 起交互事件。涵盖：" + "；".join(
            [n[:60] for n in narratives[:6]]
        )
        if len(narratives) > 6:
            summary += f"...等共计 {len(narratives)} 项经历。"

        digest_id = f"digest_{int(first_ts)}_{uuid.uuid4().hex[:6]}"
        self.store.insert_digest(
            digest_id=digest_id,
            date_str=date_str,
            timestamp=first_ts,
            narrative=summary,
            key_facts=[],
            events_covered_count=len(undigested),
            created_at=time.time(),
        )
        self.store.mark_digested([e.event_id for e in undigested], digest_id)

        logger.info(
            "[ReflectionEngine] 成功将 %s 条原始事件压缩归档为日记节点: %s",
            len(undigested), digest_id,
        )
        return {
            "digest_id": digest_id,
            "date_str": date_str,
            "events_count": len(undigested),
            "summary": summary,
        }

    def recall_digests(self, query: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]:
        """长程检索：跨越日/周/月视界检索经历日记"""
        return self.store.list_digests(query=query, limit=limit)
