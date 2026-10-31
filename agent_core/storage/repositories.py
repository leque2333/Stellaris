# -*- coding: utf-8 -*-
"""
Stellaris Core - 存储仓库接口 (Repository Contract)
业务层只依赖这里的抽象，不感知 SQLite / PostgreSQL 的差异。
新增后端时实现同一套方法即可，业务代码零改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..models import TimelineEvent


class Store(ABC):
    """记忆中枢存储门面：时间轴 / 事实 / 日记 / 账本 / 活动 / 会话段"""

    path: Path

    # ---------- 生命周期 ----------
    @abstractmethod
    def close(self) -> None:
        """释放连接资源（当前实现为按需连接，保留接口）"""

    @abstractmethod
    def schema_version(self) -> int:
        """返回当前已应用的 schema 版本"""

    # ---------- 时间轴 ----------
    @abstractmethod
    def append_event(
        self,
        event: TimelineEvent,
        *,
        session_window_seconds: int = 0,
        prev_window_seconds: int = 1800,
    ) -> TimelineEvent:
        """追加一条经历；session_window_seconds>0 时对同源高频事件做增量折叠"""

    @abstractmethod
    def get_event(self, event_id: str) -> Optional[TimelineEvent]: ...

    @abstractmethod
    def list_events(
        self,
        *,
        limit: int = 10,
        since_ts: Optional[float] = None,
        source_module: Optional[str] = None,
        actor_ids: Optional[Iterable[str]] = None,
        order: str = "desc",
        tag_primary: Optional[str] = None,
        tag_secondary: Optional[str] = None,
        include_embedding: bool = False,
    ) -> List[TimelineEvent]: ...

    @abstractmethod
    def list_candidates(self, *, actor_ids: Optional[Iterable[str]] = None, limit: int = 200) -> List[TimelineEvent]:
        """召回候选：带 embedding，按时间倒序"""

    @abstractmethod
    def surrounding(self, event_id: str, before_n: int = 2, after_n: int = 2) -> List[TimelineEvent]: ...

    @abstractmethod
    def update_event_metadata(self, event_id: str, metadata: Dict[str, Any]) -> None: ...

    @abstractmethod
    def timeline_count(self) -> int: ...

    @abstractmethod
    def import_events(self, rows: Iterable[Dict[str, Any]]) -> int:
        """跨库导入（AstrBot 旧库同步等），按 event_id 幂等"""

    # ---------- 用户画像与好感 ----------
    @abstractmethod
    def get_profile(self, user_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def insert_profile(
        self,
        user_id: str,
        nickname: str,
        role_type: str,
        facts: List[str],
        affection: int,
        affection_history: List[Dict[str, Any]],
    ) -> None: ...

    @abstractmethod
    def set_profile_facts(self, user_id: str, facts: List[str]) -> None: ...

    @abstractmethod
    def get_affection(self, user_id: str) -> Optional[int]: ...

    @abstractmethod
    def update_affection(self, user_id: str, delta: int, reason: str = "", default: int = 50) -> int: ...

    # ---------- 语义事实 ----------
    @abstractmethod
    def add_fact(self, user_id: str, content: str, category: str = "preference", supersedes_id: Optional[str] = None) -> str: ...

    @abstractmethod
    def supersede_fact(self, old_fact_id: str, new_content: str, category: Optional[str] = None) -> str: ...

    @abstractmethod
    def get_active_facts(self, user_id: str, category: Optional[str] = None, limit: int = 15) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_facts_for_prompt(self, user_id: str, limit: int = 8) -> List[str]: ...

    # ---------- 周期反思 ----------
    @abstractmethod
    def list_undigested_before(self, cutoff_ts: float, limit: int = 100) -> List[TimelineEvent]: ...

    @abstractmethod
    def insert_digest(
        self,
        digest_id: str,
        date_str: str,
        timestamp: float,
        narrative: str,
        key_facts: List[str],
        events_covered_count: int,
        created_at: float,
    ) -> None: ...

    @abstractmethod
    def mark_digested(self, event_ids: Iterable[str], digest_id: str) -> None: ...

    @abstractmethod
    def list_digests(self, query: Optional[str] = None, limit: int = 3) -> List[Dict[str, Any]]: ...

    # ---------- 三层记忆：L1 调用索引 / L3 详情 ----------
    @abstractmethod
    def record_invocation(
        self,
        trace_id: str,
        layer: str,
        module_id: str,
        *,
        tool_name: Optional[str] = None,
        channel_id: Optional[str] = None,
        actor_id: Optional[str] = None,
        started_at: Optional[float] = None,
        parent_id: Optional[str] = None,
        summary: Optional[str] = None,
        detail_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str: ...

    @abstractmethod
    def finish_invocation(
        self,
        invocation_id: str,
        *,
        status: str = "ok",
        ended_at: Optional[float] = None,
        summary: Optional[str] = None,
        detail_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> None: ...

    @abstractmethod
    def list_invocations(
        self,
        *,
        trace_id: Optional[str] = None,
        event_id: Optional[str] = None,
        module_id: Optional[str] = None,
        segment_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def list_unassigned_invocations(self, limit: int = 500) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def assign_invocation(self, invocation_id: str, segment_id: str) -> None: ...

    @abstractmethod
    def set_invocation_event(self, invocation_id: str, event_id: str) -> None: ...

    @abstractmethod
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
    ) -> str: ...

    @abstractmethod
    def get_detail(self, detail_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def list_details(
        self,
        *,
        event_id: Optional[str] = None,
        invocation_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]: ...

    # ---------- 档案层（身份映射） ----------
    @abstractmethod
    def ensure_archive(
        self,
        archive_id: str,
        role: str = "user",
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    @abstractmethod
    def get_archive(self, archive_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def list_archives(self, role: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def link_identity(
        self,
        archive_id: str,
        platform: str,
        platform_user_id: str,
        *,
        display_name: Optional[str] = None,
        is_primary: bool = False,
        confidence: float = 1.0,
    ) -> str: ...

    @abstractmethod
    def find_archive_by_identity(self, platform: str, platform_user_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def list_identities(self, archive_id: str) -> List[Dict[str, Any]]: ...

    # ---------- 平台发现：外部上下文 + 人设绑定 ----------
    @abstractmethod
    def upsert_context(
        self,
        platform: str,
        external_id: str,
        *,
        external_type: str = "",
        display_name: str = "",
        source: str = "",
        evidence: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        seen_at: Optional[float] = None,
        host_id: str = "",
    ) -> str: ...

    @abstractmethod
    def list_contexts(
        self,
        *,
        platform: Optional[str] = None,
        status: Optional[str] = None,
        host_id: Optional[str] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def mark_contexts_stale(
        self, source: str, seen_external_ids: List[str], now: Optional[float] = None
    ) -> int: ...

    @abstractmethod
    def set_context_status(self, context_id: str, status: str) -> None: ...

    @abstractmethod
    def bind_context(
        self,
        platform: str,
        external_id: str,
        archive_id: str,
        *,
        confidence: float = 0.0,
        verified_by: str = "manual",
    ) -> str: ...

    @abstractmethod
    def unbind_context(self, platform: str, external_id: str) -> bool: ...

    @abstractmethod
    def list_bindings(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def resolve_binding(self, platform: str, external_id: str) -> Optional[str]: ...

    # ---------- 插件注册表（插件是数据，不是代码） ----------
    @abstractmethod
    def upsert_plugin(
        self,
        plugin_id: str,
        *,
        name: str = "",
        root_path: str = "",
        protocol: str = "",
        spec: Optional[Dict[str, Any]] = None,
        status: str = "active",
        source: str = "",
        last_error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str: ...

    @abstractmethod
    def list_plugins(
        self,
        *,
        protocol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_plugin(self, plugin_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def delete_plugin(self, plugin_id: str) -> bool: ...

    # ---------- 桥接层：事件去重 + 游标 ----------
    @abstractmethod
    def mark_ingested(self, event_key: str, source: str = "", event_id: str = "") -> bool: ...

    @abstractmethod
    def get_bridge_cursor(self, source_id: str) -> Optional[str]: ...

    @abstractmethod
    def set_bridge_cursor(self, source_id: str, cursor: str) -> None: ...

    # ---------- 桥 spec 持久化 ----------
    @abstractmethod
    def upsert_bridge_spec(
        self, spec: Dict[str, Any], *, auto_start: bool = False, status: str = "stopped"
    ) -> str: ...

    @abstractmethod
    def list_bridge_specs(self, *, auto_start: Optional[bool] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_bridge_spec(self, source_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def delete_bridge_spec(self, source_id: str) -> bool: ...

    @abstractmethod
    def set_bridge_spec_status(
        self, source_id: str, status: str, *, auto_start: Optional[bool] = None
    ) -> None: ...

    # ---------- 直连服务注册表 ----------
    @abstractmethod
    def upsert_service(
        self,
        service_id: str,
        *,
        name: str = "",
        kind: str = "",
        endpoint: str = "",
        auth_token: str = "",
        capabilities: Optional[List[str]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        status: str = "active",
    ) -> str: ...

    @abstractmethod
    def list_services(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def get_service(self, service_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def delete_service(self, service_id: str) -> bool: ...

    @abstractmethod
    def touch_service(self, service_id: str) -> None: ...

    # ---------- 统一知识库 ----------
    @abstractmethod
    def add_knowledge_source(
        self, source_id: str, title: str, source: str = "", tags: Optional[List[str]] = None
    ) -> None: ...

    @abstractmethod
    def add_knowledge_chunks(self, chunks: List[Dict[str, Any]]) -> None: ...

    @abstractmethod
    def list_knowledge_sources(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def search_knowledge(self, query: str, limit: int = 20) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def delete_knowledge_source(self, source_id: str) -> bool: ...

    # ---------- 宿主内部二级插件 ----------
    @abstractmethod
    def upsert_component(
        self,
        component_id: str,
        host_id: str,
        name: str,
        *,
        version: str = "",
        kind: str = "secondary",
        path: str = "",
        entry: str = "",
        config_schema: str = "",
        category: str = "二级插件",
        capabilities: Optional[List[str]] = None,
        status: str = "active",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
        fingerprint: str = "",
    ) -> None: ...

    @abstractmethod
    def list_components(self, host_id: Optional[str] = None) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def delete_component(self, component_id: str) -> bool: ...

    # ---------- 宿主身份 ----------
    @abstractmethod
    def upsert_host(
        self,
        host_id: str,
        name: str,
        *,
        platform: str = "",
        internal_uid: str = "",
        root: str = "",
        source: str = "auto",
        manifest: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    @abstractmethod
    def list_hosts(self) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def update_archive(
        self,
        archive_id: str,
        *,
        display_name: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None: ...

    # ---------- Token 账本 ----------
    @abstractmethod
    def record_usage(
        self,
        session_type: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
        cached_tokens: int = 0,
        call_duration_ms: int = 0,
        cost_usd: float = 0.0,
        source: str = "universal_proxy",
        credential_type: str = "",
        currency: str = "USD",
    ) -> int: ...

    @abstractmethod
    def query_usage(self, limit: int = 50, session_type: Optional[str] = None) -> List[Dict[str, Any]]: ...

    # ---------- 插件活动（暂存区，不进时间轴） ----------
    @abstractmethod
    def insert_activities(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """返回逐条状态: accepted / duplicate / rejected"""

    @abstractmethod
    def activities_for_segment(self, segment_id: str) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def list_unassigned_activities(self, limit: int = 500) -> List[Dict[str, Any]]: ...

    @abstractmethod
    def assign_activity_segment(self, activity_id: str, segment_id: str) -> None: ...

    @abstractmethod
    def activity_count(self) -> int: ...

    # ---------- 会话段（聚合中的复杂事件） ----------
    @abstractmethod
    def get_open_segment(self, plugin_id: str, context_kind: str, context_id: str) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    def create_segment(
        self,
        plugin_id: str,
        context_kind: str,
        context_id: str,
        context_name: str,
        started_at: float,
    ) -> Dict[str, Any]: ...

    @abstractmethod
    def touch_segment(
        self,
        segment_id: str,
        last_at: float,
        add_fragments: int = 1,
        participants: Optional[List[str]] = None,
        stats: Optional[Dict[str, Any]] = None,
    ) -> None: ...

    @abstractmethod
    def close_segment(self, segment_id: str, ended_at: float) -> None: ...

    @abstractmethod
    def set_segment_event(self, segment_id: str, event_id: str) -> None: ...

    @abstractmethod
    def list_stale_segments(self, now_ts: float, idle_seconds: float) -> List[Dict[str, Any]]: ...
