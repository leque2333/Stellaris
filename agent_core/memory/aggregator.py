# -*- coding: utf-8 -*-
"""
Stellaris Core - L1→L2 会话聚合器

把一串 L1 调用（channel/model/tool/plugin）按“同一通道 + 同一对象 + 空闲窗口”
合并成一个会话段；会话结束/静默后，结算成 **一条** L2 时间轴摘要。

- L1: invocations / plugin_activities
- L2: agent_timeline（一条摘要）
- L3: memory_details（不动，原文仍按需读取）

默认：静默 10 分钟切段，单段最长 2 小时。
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional

from ..identity import IdentityResolver
from ..storage import Store, classify_source

logger = logging.getLogger("SegmentAggregator")


class SegmentAggregator:
    def __init__(
        self,
        store: Store,
        idle_seconds: float = 600.0,
        max_duration_seconds: float = 7200.0,
    ):
        self.store = store
        self.idle_seconds = float(idle_seconds)
        self.max_duration_seconds = float(max_duration_seconds)

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def tick(self, now: Optional[float] = None) -> Dict[str, int]:
        now = float(now if now is not None else time.time())
        closed = self._close_stale(now)
        inv = self._ingest_invocations(now)
        act = self._ingest_activities(now)
        return {"closed": closed, "invocations": inv, "activities": act}

    def _close_stale(self, now: float) -> int:
        closed = 0
        # last_at <= now 的所有 open 段；再按“静默”或“超长”筛选
        for seg in self.store.list_stale_segments(now, 0):
            idle = now - float(seg["last_at"])
            duration = now - float(seg["started_at"])
            if idle >= self.idle_seconds or duration >= self.max_duration_seconds:
                if self._close_segment(seg, ended_at=float(seg["last_at"])):
                    closed += 1
        return closed

    # ------------------------------------------------------------------
    # 归段
    # ------------------------------------------------------------------
    @staticmethod
    def _key_from_invocation(inv: Dict[str, Any]):
        channel = inv.get("channel_id") or inv.get("module_id") or "unknown"
        context_kind = "channel" if inv.get("channel_id") else "module"
        context_id = str(inv.get("actor_id") or channel)
        context_name = str(inv.get("actor_id") or channel)
        return channel, context_kind, context_id, context_name

    def _segment_for(
        self,
        plugin_id: str,
        context_kind: str,
        context_id: str,
        context_name: str,
        started_at: float,
        now: float,
    ) -> Dict[str, Any]:
        seg = self.store.get_open_segment(plugin_id, context_kind, context_id)
        if seg:
            gap = float(started_at) - float(seg["last_at"])
            idle = now - float(seg["last_at"])
            if (gap >= self.idle_seconds
                    or idle >= self.idle_seconds
                    or now - float(seg["started_at"]) >= self.max_duration_seconds):
                self._close_segment(seg, ended_at=float(seg["last_at"]))
                seg = None
        if not seg:
            seg = self.store.create_segment(
                plugin_id, context_kind, context_id, context_name, started_at
            )
        return seg

    def _ingest_invocations(self, now: float) -> int:
        count = 0
        for inv in self.store.list_unassigned_invocations(limit=1000):
            plugin_id, kind, cid, cname = self._key_from_invocation(inv)
            started = float(inv.get("started_at") or now)
            ended = float(inv.get("ended_at") or started)
            seg = self._segment_for(plugin_id, kind, cid, cname, started, now)
            self.store.assign_invocation(inv["invocation_id"], seg["segment_id"])
            self.store.touch_segment(
                seg["segment_id"],
                last_at=max(ended, float(seg.get("last_at") or started)),
                add_fragments=1,
                participants=[inv.get("actor_id")],
                stats={"invocations": 1},
            )
            count += 1
        return count

    def _ingest_activities(self, now: float) -> int:
        count = 0
        for act in self.store.list_unassigned_activities(limit=1000):
            plugin_id = act.get("plugin_id") or "unknown"
            kind = act.get("context_kind") or "activity"
            cid = str(act.get("context_id") or plugin_id)
            cname = act.get("context_name") or cid
            occurred = float(act.get("occurred_at") or now)
            seg = self._segment_for(plugin_id, kind, cid, cname, occurred, now)
            self.store.assign_activity_segment(act["activity_id"], seg["segment_id"])
            self.store.touch_segment(
                seg["segment_id"],
                last_at=max(occurred, float(seg.get("last_at") or occurred)),
                add_fragments=1,
                participants=[act.get("actor_name") or act.get("actor_id")],
                stats={"activities": 1},
            )
            count += 1
        return count

    # ------------------------------------------------------------------
    # 结算
    # ------------------------------------------------------------------
    def _close_segment(self, seg: Dict[str, Any], ended_at: float) -> Optional[str]:
        seg_id = seg["segment_id"]
        invs = self.store.list_invocations(segment_id=seg_id, limit=1000)
        acts = self.store.activities_for_segment(seg_id)
        if not invs and not acts:
            self.store.close_segment(seg_id, ended_at)
            return None

        event = self._compose_event(seg, invs, acts, ended_at)
        saved = self.store.append_event(event)
        for inv in invs:
            self.store.set_invocation_event(inv["invocation_id"], saved.event_id)
        self.store.set_segment_event(seg_id, saved.event_id)
        self.store.close_segment(seg_id, ended_at)
        logger.info(
            "[Aggregator] 会话 %s 结算为事件 %s（%s 调用 / %s 活动）",
            seg_id, saved.event_id, len(invs), len(acts),
        )
        return saved.event_id

    def _compose_event(self, seg, invs, acts, ended_at):
        from ..models import TimelineEvent

        n = len(invs) + len(acts)
        start = float(seg["started_at"])
        end = max(float(ended_at), float(seg["last_at"]), start)

        modules = sorted({str(i.get("module_id")) for i in invs if i.get("module_id")})
        tools = sorted({str(i.get("tool_name")) for i in invs if i.get("tool_name")})
        verbs = sorted({str(a.get("verb")) for a in acts if a.get("verb")})
        highlights = []
        for a in acts:
            obj = str(a.get("object") or "").strip()
            outcome = str(a.get("outcome") or "").strip()
            if obj or outcome:
                highlights.append(f"{obj}（{outcome}）" if obj and outcome else (obj or outcome))

        context = seg.get("context_name") or seg.get("context_id") or seg["plugin_id"]
        parts = [f"{context}"]
        if modules:
            parts.append("模块：" + "、".join(modules[:4]))
        if tools:
            parts.append("工具：" + "、".join(tools[:4]))
        if verbs:
            parts.append("动作：" + "、".join(verbs[:4]))
        narrative = "；".join(parts) + f"（共 {n} 个片段）"
        if highlights:
            narrative += "。要点：" + "；".join(highlights[:3])

        importance = min(5.0, 1.0 + math.log2(max(1, n)) * 0.5)
        emotional = max(-3, min(3, sum(int(a.get("emotional_impact") or 0) for a in acts)))

        participants = []
        for item in invs:
            name = item.get("actor_id")
            if name and name not in participants:
                participants.append(name)
        for item in acts:
            name = item.get("actor_name") or item.get("actor_id")
            if name and name not in participants:
                participants.append(name)

        plugin_id = seg["plugin_id"]
        _, tag_primary, _ = classify_source(plugin_id, {})
        tag_secondary = str(seg.get("context_name") or seg.get("context_id") or "")

        return TimelineEvent(
            event_id="",
            timestamp=end,
            source_module=plugin_id,
            actor_id=participants[0] if len(participants) == 1 else "self",
            actor_name=participants[0] if len(participants) == 1 else IdentityResolver.get_bot_name(),
            narrative=narrative,
            emotional_impact=emotional,
            importance=importance,
            metadata={
                "segment_id": seg["segment_id"],
                "invocation_ids": [i["invocation_id"] for i in invs][:100],
                "activity_ids": [a["activity_id"] for a in acts][:100],
                "participants": participants,
            },
            plugin_id=plugin_id,
            tag_primary=tag_primary,
            tag_secondary=tag_secondary,
            segment_id=seg["segment_id"],
            started_at=start,
            ended_at=end,
        )


def run_forever(store: Store, interval: float = 30.0, **kwargs) -> None:
    aggregator = SegmentAggregator(store, **kwargs)
    while True:
        try:
            aggregator.tick()
        except Exception as exc:  # 不让聚合器拖垮主服务
            logger.warning("[Aggregator] tick 失败: %s", exc)
        time.sleep(interval)


if __name__ == "__main__":
    from ..storage import get_store

    result = SegmentAggregator(get_store()).tick()
    print(json.dumps(result, ensure_ascii=False))
