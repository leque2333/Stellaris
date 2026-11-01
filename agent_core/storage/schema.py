# -*- coding: utf-8 -*-
"""
Stellaris Core - 数据库结构迁移
原则：只做加法，不删列、不重写历史数据；每次迁移前自动快照备份。
版本记录在 schema_migrations 表中，可重复执行。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

REQUIRED_TABLES = ("agent_timeline", "user_profiles", "user_facts", "memory_digests", "token_usage")

TIMELINE_COLUMNS = {
    "plugin_id": "TEXT",
    "tag_primary": "TEXT",
    "tag_secondary": "TEXT",
    "tag_permission": "TEXT DEFAULT ''",
    "segment_id": "TEXT",
    "started_at": "REAL",
    "ended_at": "REAL",
    "visibility": "TEXT DEFAULT 'private'",
    "detail_id": "TEXT DEFAULT ''",
    "person_id": "TEXT DEFAULT ''",
}

LEDGER_RECHARGE_SOURCES = ("人工充值", "账户充值", "系统注资")


def _table_type(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT type FROM sqlite_master WHERE name = ?", (name,)).fetchone()
    return row[0] if row else None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at REAL NOT NULL,
            checksum TEXT NOT NULL
        )
        """
    )


def _checksum(version: int, name: str) -> str:
    return hashlib.sha256(f"{version}:{name}".encode("utf-8")).hexdigest()[:16]


def _format_ts(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return time.strftime("%Y-%m-%d %H:%M:%S")


def _backup(conn: sqlite3.Connection, db_path: Path, backup_dir: Path | None) -> Path | None:
    """用 SQLite 在线备份 API 生成一致性快照（含 WAL 中未落盘内容）"""
    if not backup_dir:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"pre_migrate_{time.strftime('%Y%m%d_%H%M%S')}_{db_path.stem}.db"
    dst = sqlite3.connect(str(dest))
    try:
        conn.backup(dst)
        dst.commit()
    finally:
        dst.close()
    return dest


# --------------------------------------------------------------------------
# v1: 基线结构（新建库时创建；老库执行 IF NOT EXISTS 无副作用）
# --------------------------------------------------------------------------
def _v1_baseline(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_timeline (
            event_id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            time_str TEXT NOT NULL,
            source_module TEXT NOT NULL,
            actor_id TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            narrative TEXT NOT NULL,
            emotional_impact INTEGER DEFAULT 0,
            importance REAL DEFAULT 1.0,
            prev_event_id TEXT,
            causal_relation TEXT,
            metadata TEXT DEFAULT '{}',
            embedding BLOB
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_ts ON agent_timeline(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_actor ON agent_timeline(actor_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_module ON agent_timeline(source_module)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            nickname TEXT,
            role_type TEXT DEFAULT 'member',
            facts TEXT DEFAULT '[]',
            updated_at REAL,
            affection INTEGER DEFAULT 50,
            affection_history TEXT DEFAULT '[]'
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_facts (
            fact_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            content TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            supersedes_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_user ON user_facts(user_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_status ON user_facts(status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_digests (
            digest_id TEXT PRIMARY KEY,
            date_str TEXT NOT NULL,
            timestamp REAL NOT NULL,
            narrative TEXT NOT NULL,
            key_facts TEXT DEFAULT '[]',
            events_covered_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_digest_ts ON memory_digests(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_digest_date ON memory_digests(date_str)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL,
            time_str TEXT,
            session_type TEXT,
            model TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            total_tokens INTEGER,
            cached_tokens INTEGER DEFAULT 0,
            call_duration_ms INTEGER DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_ts ON token_usage(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_session ON token_usage(session_type)")


# --------------------------------------------------------------------------
# v2: 时间线标签 + 插件活动/会话段 + 账本统一
# --------------------------------------------------------------------------
def _v2_timeline_and_plugins(conn: sqlite3.Connection) -> None:
    existing = _columns(conn, "agent_timeline")
    for name, ddl in TIMELINE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE agent_timeline ADD COLUMN {name} {ddl}")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_tags ON agent_timeline(tag_primary, tag_secondary, timestamp)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_segment ON agent_timeline(segment_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plugin_activities (
            activity_id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            session_id TEXT,
            context_kind TEXT,
            context_id TEXT,
            context_name TEXT,
            occurred_at REAL NOT NULL,
            kind TEXT NOT NULL,
            verb TEXT,
            object TEXT,
            outcome TEXT,
            actor_id TEXT,
            actor_name TEXT,
            detail TEXT,
            importance REAL DEFAULT 1.0,
            emotional_impact INTEGER DEFAULT 0,
            trace_id TEXT,
            metadata TEXT DEFAULT '{}',
            segment_id TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_act_seg ON plugin_activities(segment_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_act_plugin_time ON plugin_activities(plugin_id, occurred_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_act_session ON plugin_activities(session_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline_segments (
            segment_id TEXT PRIMARY KEY,
            plugin_id TEXT NOT NULL,
            context_kind TEXT,
            context_id TEXT,
            context_name TEXT,
            started_at REAL NOT NULL,
            last_at REAL NOT NULL,
            ended_at REAL,
            status TEXT DEFAULT 'open',
            fragment_count INTEGER DEFAULT 0,
            participants TEXT DEFAULT '[]',
            stats TEXT DEFAULT '{}',
            event_id TEXT,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_open ON timeline_segments(status, last_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_seg_ctx ON timeline_segments(plugin_id, context_id, status)")

    # 账本统一：token_usage 为主表，token_usage_ledger 变为只读兼容视图
    token_cols = _columns(conn, "token_usage")
    if "cost_usd" not in token_cols:
        conn.execute("ALTER TABLE token_usage ADD COLUMN cost_usd REAL DEFAULT 0")
    if "source" not in token_cols:
        conn.execute("ALTER TABLE token_usage ADD COLUMN source TEXT DEFAULT 'universal_proxy'")

    if _table_type(conn, "token_usage_ledger") == "table":
        rows = conn.execute("SELECT * FROM token_usage_ledger").fetchall()
        for row in rows:
            source = row["source"] or "universal_proxy"
            session_type = "recharge" if source in LEDGER_RECHARGE_SOURCES else source
            conn.execute(
                """
                INSERT INTO token_usage
                (timestamp, time_str, session_type, model, prompt_tokens, completion_tokens,
                 total_tokens, cached_tokens, call_duration_ms, cost_usd, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["timestamp"],
                    _format_ts(row["timestamp"]),
                    session_type,
                    row["model_id"],
                    row["input_tokens"] or 0,
                    row["output_tokens"] or 0,
                    (row["input_tokens"] or 0) + (row["output_tokens"] or 0),
                    row["cache_tokens"] or 0,
                    0,
                    row["cost_usd"] or 0.0,
                    source,
                ),
            )
        conn.execute("DROP TABLE token_usage_ledger")

    if _table_type(conn, "token_usage_ledger") is None:
        conn.execute(
            """
            CREATE VIEW token_usage_ledger AS
            SELECT 'tu_' || id AS id,
                   timestamp,
                   model AS model_id,
                   prompt_tokens AS input_tokens,
                   completion_tokens AS output_tokens,
                   cached_tokens AS cache_tokens,
                   COALESCE(source, 'universal_proxy') AS source,
                   COALESCE(cost_usd, 0) AS cost_usd,
                   COALESCE(session_type, '') AS session_type,
                   COALESCE(credential_type, '') AS credential_type,
                   COALESCE(tools, '') AS tools
            FROM token_usage
            """
        )


# --------------------------------------------------------------------------
# v3: 老数据标签回填（按 source_module + metadata 推断）
# --------------------------------------------------------------------------
def classify_source(source_module: str, metadata: Dict[str, Any]) -> Tuple[str, str, str]:
    """返回 (plugin_id, tag_primary, tag_secondary)"""
    src = str(source_module or "").lower()
    meta = metadata or {}

    if src.startswith("astrbot") or src in ("qq_chat", "qq"):
        plugin = "qq"
        secondary = (
            meta.get("group_name")
            or meta.get("peer_name")
            or meta.get("user_name")
            or ("群聊" if "group" in src else "私聊")
        )
        return plugin, "QQ", str(secondary)

    if src.startswith("bilibili"):
        secondary = meta.get("up_name") or meta.get("video_title") or "巡检"
        return "bilibili", "B站", str(secondary)

    if src in ("pc_bridge", "host_control", "host_bridge", "antigravity_bridge"):
        return "host_control", "宿主机", str(meta.get("task") or meta.get("action") or "系统操作")

    if src.startswith("cli"):
        return "cli", "终端", "CLI"

    if src.startswith("universal_proxy") or src.startswith("api_"):
        return "universal_proxy", "通用", str(meta.get("channel") or "默认")

    return src or "system", src or "系统", str(meta.get("context_name") or "默认")


def _v3_backfill_tags(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT event_id, source_module, metadata, timestamp FROM agent_timeline WHERE tag_primary IS NULL"
    ).fetchall()
    for row in rows:
        try:
            meta = json.loads(row["metadata"] or "{}")
        except Exception:
            meta = {}
        plugin_id, primary, secondary = classify_source(row["source_module"], meta)
        conn.execute(
            """
            UPDATE agent_timeline
               SET plugin_id = ?, tag_primary = ?, tag_secondary = ?,
                   started_at = COALESCE(started_at, timestamp),
                   ended_at = COALESCE(ended_at, timestamp)
             WHERE event_id = ?
            """,
            (plugin_id, primary, secondary, row["event_id"]),
        )


def _v4_memory_layers(conn: sqlite3.Connection) -> None:
    """三层记忆底座：L1 调用索引 + L3 详情库（L2 继续复用 agent_timeline）"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS invocations (
            invocation_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_id TEXT,
            layer TEXT NOT NULL,
            module_id TEXT NOT NULL,
            tool_name TEXT,
            channel_id TEXT,
            actor_id TEXT,
            started_at REAL NOT NULL,
            ended_at REAL,
            status TEXT DEFAULT 'ok',
            summary TEXT,
            detail_id TEXT,
            event_id TEXT,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_trace ON invocations(trace_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_time ON invocations(started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_module ON invocations(module_id, started_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_event ON invocations(event_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS memory_details (
            detail_id TEXT PRIMARY KEY,
            invocation_id TEXT,
            event_id TEXT,
            kind TEXT NOT NULL,
            mime TEXT DEFAULT 'application/json',
            content TEXT,
            size_bytes INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            expires_at REAL,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_detail_event ON memory_details(event_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_detail_inv ON memory_details(invocation_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_detail_time ON memory_details(created_at)")


def _v5_archives(conn: sqlite3.Connection) -> None:
    """档案层：管理员档案（必须）+ 用户档案（可选）+ 平台身份映射"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archives (
            archive_id TEXT PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'user',
            display_name TEXT,
            status TEXT DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            metadata TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archives_role ON archives(role, status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_identities (
            identity_id TEXT PRIMARY KEY,
            archive_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            platform_user_id TEXT NOT NULL,
            display_name TEXT,
            is_primary INTEGER DEFAULT 0,
            confidence REAL DEFAULT 1.0,
            created_at REAL NOT NULL,
            UNIQUE(platform, platform_user_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ident_archive ON archive_identities(archive_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ident_platform ON archive_identities(platform, platform_user_id)")


def _v6_persona_binding(conn: sqlite3.Connection) -> None:
    """档案绑定人格：管理员档案默认 anan，用户档案默认 default"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(archives)")}
    if "persona_id" not in cols:
        conn.execute("ALTER TABLE archives ADD COLUMN persona_id TEXT DEFAULT 'anan'")
    conn.execute("UPDATE archives SET persona_id = 'anan' WHERE persona_id IS NULL AND role = 'admin'")
    conn.execute("UPDATE archives SET persona_id = 'default' WHERE persona_id IS NULL AND role != 'admin'")


def _v7_invocation_segment(conn: sqlite3.Connection) -> None:
    """L1 调用索引挂到会话段，供聚合器结算 L2"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invocations)")}
    if "segment_id" not in cols:
        conn.execute("ALTER TABLE invocations ADD COLUMN segment_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_segment ON invocations(segment_id)")


def _v8_persona_sections(conn: sqlite3.Connection) -> None:
    """档案承载自我人设：按段存储（身份/性格/语言/对白/规范）"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(archives)")}
    if "persona_sections" not in cols:
        conn.execute("ALTER TABLE archives ADD COLUMN persona_sections TEXT DEFAULT '[]'")


def _v9_platform_discovery(conn: sqlite3.Connection) -> None:
    """平台自动发现：外部上下文台账 + 人设绑定"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_contexts (
            context_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            external_id TEXT NOT NULL,
            external_type TEXT,
            display_name TEXT,
            source TEXT,
            status TEXT DEFAULT 'active',
            evidence TEXT DEFAULT '[]',
            first_seen REAL,
            last_seen REAL,
            metadata TEXT DEFAULT '{}',
            UNIQUE(platform, external_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ctx_platform ON platform_contexts(platform, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ctx_source ON platform_contexts(source)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS platform_bindings (
            binding_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            external_id TEXT NOT NULL,
            archive_id TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at REAL,
            updated_at REAL,
            UNIQUE(platform, external_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bind_archive ON platform_bindings(archive_id)")


def _v10_binding_confidence(conn: sqlite3.Connection) -> None:
    """绑定记录来源与置信度，区分“交叉验证 / 只匹配一边 / 手填”"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(platform_bindings)")}
    if "confidence" not in cols:
        conn.execute("ALTER TABLE platform_bindings ADD COLUMN confidence REAL DEFAULT 0")
    if "verified_by" not in cols:
        conn.execute("ALTER TABLE platform_bindings ADD COLUMN verified_by TEXT DEFAULT 'manual'")


def _v11_plugins(conn: sqlite3.Connection) -> None:
    """插件注册表：插件是数据，不是代码分支"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS plugins (
            plugin_id TEXT PRIMARY KEY,
            name TEXT,
            root_path TEXT,
            protocol TEXT,
            spec_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'active',
            source TEXT,
            last_error TEXT,
            metadata TEXT DEFAULT '{}',
            discovered_at REAL,
            updated_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plugins_protocol ON plugins(protocol, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plugins_path ON plugins(root_path)")


def _v12_bridge(conn: sqlite3.Connection) -> None:
    """桥接层：事件去重 + 游标"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ingested_events (
            event_key TEXT PRIMARY KEY,
            source TEXT,
            event_id TEXT,
            ingested_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_cursors (
            source_id TEXT PRIMARY KEY,
            cursor TEXT,
            updated_at REAL
        )
        """
    )


def _v13_bridge_specs(conn: sqlite3.Connection) -> None:
    """桥 spec 持久化：重启后恢复"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge_specs (
            source_id TEXT PRIMARY KEY,
            kind TEXT,
            scope TEXT,
            locator TEXT,
            options_json TEXT DEFAULT '{}',
            status TEXT DEFAULT 'stopped',
            auto_start INTEGER DEFAULT 0,
            created_at REAL,
            updated_at REAL
        )
        """
    )


def _v14_services(conn: sqlite3.Connection) -> None:
    """直连服务注册表：服务是数据，不是代码分支"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            service_id TEXT PRIMARY KEY,
            name TEXT,
            kind TEXT,
            endpoint TEXT,
            auth_token TEXT,
            capabilities TEXT DEFAULT '[]',
            manifest TEXT DEFAULT '{}',
            status TEXT DEFAULT 'active',
            last_seen REAL,
            created_at REAL,
            updated_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_services_status ON services(status, updated_at)")


def _v15_knowledge(conn: sqlite3.Connection) -> None:
    """中枢统一知识库：多个宿主共享的文档片段"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_sources (
            source_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT,
            tags TEXT DEFAULT '[]',
            created_at REAL,
            updated_at REAL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            chunk_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            created_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_source ON knowledge_chunks(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_title ON knowledge_chunks(title)")


def _v16_components(conn: sqlite3.Connection) -> None:
    """宿主内部二级插件：只展示，不注册为直连服务"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS components (
            component_id TEXT PRIMARY KEY,
            host_id TEXT NOT NULL,
            name TEXT NOT NULL,
            version TEXT,
            kind TEXT DEFAULT 'secondary',
            path TEXT,
            entry TEXT,
            config_schema TEXT,
            category TEXT,
            capabilities TEXT DEFAULT '[]',
            status TEXT DEFAULT 'active',
            source TEXT DEFAULT 'auto',
            manifest TEXT DEFAULT '{}',
            last_seen REAL,
            created_at REAL,
            updated_at REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_components_host ON components(host_id, status)")


def _v17_component_fingerprint(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(components)")}
    if "fingerprint" not in cols:
        conn.execute("ALTER TABLE components ADD COLUMN fingerprint TEXT DEFAULT ''")
    if "removed_at" not in cols:
        conn.execute("ALTER TABLE components ADD COLUMN removed_at REAL")


def _v18_hosts(conn: sqlite3.Connection) -> None:
    """宿主身份：内部 UID、名称、平台与根目录"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hosts (
            host_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            platform TEXT,
            internal_uid TEXT,
            root TEXT,
            source TEXT DEFAULT 'auto',
            manifest TEXT DEFAULT '{}',
            created_at REAL,
            updated_at REAL
        )
        """
    )


def _v19_context_host(conn: sqlite3.Connection) -> None:
    """宿主身份也进外部上下文台账：一个 context 可挂到 hosts.host_id"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(platform_contexts)")}
    if "host_id" not in cols:
        conn.execute("ALTER TABLE platform_contexts ADD COLUMN host_id TEXT DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ctx_host ON platform_contexts(host_id, status)"
    )


def _v20_timeline_detail(conn: sqlite3.Connection) -> None:
    """时间轴事件回链 L3 原文，召回时可取回完整一轮"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_timeline)")}
    if "detail_id" not in cols:
        conn.execute("ALTER TABLE agent_timeline ADD COLUMN detail_id TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_detail ON agent_timeline(detail_id)")


def _v21_timeline_person(conn: sqlite3.Connection) -> None:
    """档案即统一人物 ID：事件写 person_id，跨模块召回同一份记忆"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_timeline)")}
    if "person_id" not in cols:
        conn.execute("ALTER TABLE agent_timeline ADD COLUMN person_id TEXT DEFAULT ''")
    try:
        conn.execute(
            """
            UPDATE agent_timeline
            SET person_id = COALESCE(NULLIF(json_extract(metadata, '$.archive_id'), ''), actor_id)
            WHERE person_id IS NULL OR person_id = ''
            """
        )
    except sqlite3.OperationalError:
        conn.execute(
            "UPDATE agent_timeline SET person_id = actor_id "
            "WHERE person_id IS NULL OR person_id = ''"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timeline_person ON agent_timeline(person_id, timestamp)")


def _v22_master_person(conn: sqlite3.Connection) -> None:
    """管理人所有历史事件归一到 master QQ，避免 master / 档案两个 person_id 分家"""
    try:
        from ..identity import IdentityResolver
        master_qq = str(IdentityResolver.get_master_qq() or "").strip()
    except Exception:
        master_qq = ""
    if not master_qq:
        return
    conn.execute(
        "UPDATE agent_timeline SET person_id = ? "
        "WHERE actor_id IN ('master', ?) AND "
        "(person_id IS NULL OR person_id = '' OR person_id = 'master')",
        (master_qq, master_qq),
    )


def _v23_archive_aliases(conn: sqlite3.Connection) -> None:
    """旧档案 ID → 新独立档案 ID 的兼容映射。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_aliases (
            alias_id TEXT PRIMARY KEY,
            archive_id TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_archive_alias_target ON archive_aliases(archive_id)")


def _v24_usage_credential_meta(conn: sqlite3.Connection) -> None:
    """账本按主凭证/执行凭证及币种分桶。"""
    cols = _columns(conn, "token_usage")
    if "credential_type" not in cols:
        conn.execute("ALTER TABLE token_usage ADD COLUMN credential_type TEXT DEFAULT ''")
    if "currency" not in cols:
        conn.execute("ALTER TABLE token_usage ADD COLUMN currency TEXT DEFAULT 'USD'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_token_usage_meta ON token_usage(credential_type, currency)")


def _v26_token_usage_tools(conn: sqlite3.Connection) -> None:
    """Token 账本加来源工具列。

    一次请求可能触发多个工具（tool_calls），把工具名记下来，
    账本就能按「通道 + 工具」归因，不再只有一个 qq。
    """
    cols = _columns(conn, "token_usage")
    if "tools" not in cols:
        conn.execute("ALTER TABLE token_usage ADD COLUMN tools TEXT DEFAULT ''")


def _v25_affection_range_and_settings(conn: sqlite3.Connection) -> None:
    """档案好感度浮动范围 + 自定义设定 + 事实来源标记。

    - archives.affection_min / affection_max：每个档案的好感度只能在窗口内浮动
    - user_facts.source：区分 AI 总结 / 人工填写 / 历史数据
    - archive_settings：用户自己写的稳定设定，AI 不可改
    """
    # 1) 好感度浮动窗口
    cols = _columns(conn, "archives")
    if "affection_min" not in cols:
        conn.execute("ALTER TABLE archives ADD COLUMN affection_min INTEGER DEFAULT 0")
    if "affection_max" not in cols:
        conn.execute("ALTER TABLE archives ADD COLUMN affection_max INTEGER DEFAULT 100")

    # 2) 事实来源
    fact_cols = _columns(conn, "user_facts")
    if "source" not in fact_cols:
        conn.execute("ALTER TABLE user_facts ADD COLUMN source TEXT DEFAULT 'legacy'")

    # 3) 自定义设定
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS archive_settings (
            setting_id TEXT PRIMARY KEY,
            archive_id TEXT NOT NULL,
            label TEXT NOT NULL,
            content TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_archive_settings_archive "
        "ON archive_settings(archive_id, sort_order)"
    )


def _v27_timeline_buffer(conn: sqlite3.Connection) -> None:
    """时间轴缓冲区：被检索顶出去的近期记忆暂存这里，轮次结束后回填。

    只存 event 的完整快照（payload），不回写 agent_timeline——
    因为它已经从视图里出去了，回来时也应该走同一套曲线规则。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS timeline_buffer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT NOT NULL,
            event_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            released_at REAL NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_timeline_buffer_person "
        "ON timeline_buffer(person_id, id)"
    )


def _v28_timeline_permission(conn: sqlite3.Connection) -> None:
    """时间轴权限标签：与发起人档案标签分离。"""
    existing = _columns(conn, "agent_timeline")
    if "tag_permission" not in existing:
        conn.execute("ALTER TABLE agent_timeline ADD COLUMN tag_permission TEXT DEFAULT ''")


def _v29_fact_confidence_and_buffer_unique(conn: sqlite3.Connection) -> None:
    """事实置信度/失效时间 + 缓冲区去重索引。

    - user_facts.confidence：低置信度事实写 pending，不再一律 active
    - user_facts.superseded_at：记录旧事实退役时间，回滚时能还原
    - idx_facts_conflict：同人 / 同分类 / 同状态的事实检索更快
    - uq_buffer_event：同一事件在缓冲区里只留一份（审计 B23）
    """
    fact_cols = _columns(conn, "user_facts")
    if "confidence" not in fact_cols:
        conn.execute("ALTER TABLE user_facts ADD COLUMN confidence REAL DEFAULT 1.0")
    if "superseded_at" not in fact_cols:
        conn.execute("ALTER TABLE user_facts ADD COLUMN superseded_at REAL")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_facts_conflict "
        "ON user_facts(user_id, category, status)"
    )
    buffer_cols = _columns(conn, "timeline_buffer")
    if buffer_cols:
        conn.execute(
            "DELETE FROM timeline_buffer WHERE rowid NOT IN ("
            "  SELECT MIN(rowid) FROM timeline_buffer GROUP BY person_id, event_id"
            ")"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_buffer_event "
            "ON timeline_buffer(person_id, event_id)"
        )


MIGRATIONS: List[Tuple[int, str, Callable[[sqlite3.Connection], None]]] = [
    (1, "baseline", _v1_baseline),
    (2, "timeline_tags_plugins_ledger", _v2_timeline_and_plugins),
    (3, "backfill_tags", _v3_backfill_tags),
    (4, "memory_layers", _v4_memory_layers),
    (5, "archives", _v5_archives),
    (6, "persona_binding", _v6_persona_binding),
    (7, "invocation_segment", _v7_invocation_segment),
    (8, "persona_sections", _v8_persona_sections),
    (9, "platform_discovery", _v9_platform_discovery),
    (10, "binding_confidence", _v10_binding_confidence),
    (11, "plugins", _v11_plugins),
    (12, "bridge", _v12_bridge),
    (13, "bridge_specs", _v13_bridge_specs),
    (14, "services", _v14_services),
    (15, "knowledge", _v15_knowledge),
    (16, "components", _v16_components),
    (17, "component_fingerprint", _v17_component_fingerprint),
    (18, "hosts", _v18_hosts),
    (19, "context_host", _v19_context_host),
    (20, "timeline_detail", _v20_timeline_detail),
    (21, "timeline_person", _v21_timeline_person),
    (22, "master_person", _v22_master_person),
    (23, "archive_aliases", _v23_archive_aliases),
    (24, "usage_credential_meta", _v24_usage_credential_meta),
    (25, "affection_range_and_settings", _v25_affection_range_and_settings),
    (26, "token_usage_tools", _v26_token_usage_tools),
    (27, "timeline_buffer", _v27_timeline_buffer),
    (28, "timeline_permission", _v28_timeline_permission),
    (29, "fact_confidence_and_buffer_unique", _v29_fact_confidence_and_buffer_unique),
]


def current_version(db_path: Path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        try:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0] or 0) if row else 0
        except sqlite3.OperationalError:
            return 0


def apply_migrations(db_path: Path, backup_dir: Path | None = None) -> Dict[str, Any]:
    """应用所有未执行的迁移；返回 {from, to, applied, backup}"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    applied_names: List[str] = []

    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_migrations_table(conn)
        done = {int(r["version"]) for r in conn.execute("SELECT version FROM schema_migrations")}
        pending = [(v, n, f) for v, n, f in MIGRATIONS if v not in done]
        start_version = max(done) if done else 0
        if not pending:
            return {"from": start_version, "to": start_version, "applied": [], "backup": None}

        backup_path = _backup(conn, db_path, backup_dir or (db_path.parent.parent / "backups"))

        for version, name, fn in pending:
            conn.execute("BEGIN IMMEDIATE")
            try:
                fn(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
                    (version, name, time.time(), _checksum(version, name)),
                )
                conn.execute(f"PRAGMA user_version = {version}")
                conn.execute("COMMIT")
                applied_names.append(name)
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()

    return {
        "from": start_version,
        "to": max(v for v, _, _ in MIGRATIONS) if applied_names else start_version,
        "applied": applied_names,
        "backup": str(backup_path) if backup_path else None,
    }
