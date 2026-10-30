# -*- coding: utf-8 -*-
"""执行 AI 生成 BridgeSpec：探测目标 → 生成 spec → 试读预览"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from ..connectors.ai_matcher import _redact
from ..llm import LLMClient
from .drivers import HttpEventDriver, JsonEventDriver, LogEventDriver, SqliteEventDriver
from .models import BridgeSpec

SYSTEM_PROMPT = """你是中枢的桥接器生成器。根据提供的目标证据，生成一份 BridgeSpec JSON。
规则：
1. 只输出 JSON，不要 markdown，不要解释。
2. 只能使用已有字段；不要编造表名/字段名。
3. kind 只能是 sqlite/json/log/http。
4. source_id 用 auto_ 开头；scope 用 plugin。
5. options 里给出 table/cursor/id/time/channel/actor/content 等字段；不存在的不要写。
6. 如果证据不足，输出 {"error":"..."}。
输出格式：
{"source_id":"auto_xxx","kind":"sqlite","scope":"plugin","locator":"...","options":{...},"confidence":0.0,"reason":"..."}"""

FIELD_HINTS = {
    "id": ["id", "message_id", "msg_id", "uuid", "event_id"],
    "time": ["created_at", "timestamp", "time", "date", "updated_at", "ts"],
    "channel": ["group_id", "channel_id", "chat_id", "session_id", "conversation_id", "room_id"],
    "actor": ["sender_id", "user_id", "uid", "qq", "from_id"],
    "actor_name": ["sender_name", "nickname", "user_name", "name", "from_name"],
    "content": ["content", "text", "message", "msg", "body", "plain_text"],
}


def _pick(columns: List[str], names: List[str]) -> Optional[str]:
    low = {c.lower(): c for c in columns}
    for name in names:
        if name in low:
            return low[name]
    return None


def _auto_id(target: Any) -> str:
    digest = hashlib.sha1(str(target).encode("utf-8")).hexdigest()[:8]
    return f"auto_{digest}"


class BridgeSpecInferer:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    async def infer(self, target: str, kind: str = "", extra_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        is_url = target.startswith(("http://", "https://"))
        path = Path(target).resolve()
        if not is_url and not path.exists():
            return {"ok": False, "error": "目标不存在", "preview": []}

        if is_url:
            evidence = await self._probe_url(target)
            heuristic = self._heuristic_url(target, evidence)
        else:
            evidence = await asyncio.to_thread(self._probe, path)
            heuristic = self._heuristic(path, evidence, kind)
        spec_data = heuristic
        source = "heuristic"

        if self.llm.api_key and not kind:
            ai_spec = await self._ask_ai(target, evidence)
            if ai_spec and ai_spec.get("kind") in ("sqlite", "json", "log", "http"):
                spec_data = ai_spec
                source = "ai"

        try:
            if extra_options:
                merged = dict(spec_data.get("options") or {})
                merged.update(extra_options)
                spec_data["options"] = merged
            spec = BridgeSpec.from_dict(spec_data)
        except Exception as exc:
            return {"ok": False, "error": f"spec 解析失败: {exc}", "preview": []}
        if not spec.locator:
            spec.locator = target
        if spec.kind not in ("sqlite", "json", "log", "http"):
            return {"ok": False, "error": f"不支持的 kind={spec.kind}", "preview": []}

        preview, error = await self._dry_run(spec)
        return {
            "ok": bool(preview),
            "spec": spec.to_dict(),
            "preview": [e.to_dict() for e in preview],
            "confidence": float(spec_data.get("confidence") or (0.8 if preview else 0.3)),
            "source": source,
            "error": error,
            "evidence": evidence,
        }

    async def _probe_url(self, url: str) -> Dict[str, Any]:
        evidence: Dict[str, Any] = {"url": url, "target": "url"}
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    evidence["status"] = resp.status
                    evidence["content_type"] = resp.headers.get("Content-Type", "")
                    text = await resp.text()
                    text = text[:200000]
        except Exception as exc:
            evidence["error"] = str(exc)
            return evidence
        try:
            data = json.loads(text)
            evidence["top_type"] = type(data).__name__
            if isinstance(data, dict):
                evidence["keys"] = list(data.keys())[:30]
                for key, value in data.items():
                    if isinstance(value, list) and value:
                        evidence["list_key"] = key
                        evidence["sample"] = [
                            {k: _redact(str(v))[:80] for k, v in item.items()}
                            for item in value[:2] if isinstance(item, dict)
                        ]
                        break
            elif isinstance(data, list):
                evidence["sample"] = [
                    {k: _redact(str(v))[:80] for k, v in item.items()}
                    for item in data[:2] if isinstance(item, dict)
                ]
        except Exception:
            evidence["lines"] = [_redact(x)[:300] for x in text.splitlines()[:10]]
        return evidence

    def _heuristic_url(self, url: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        options: Dict[str, Any] = {"platform": "other"}
        sample = (evidence.get("sample") or [{}])[0]
        keys = list(sample.keys())
        if keys:
            options["id"] = _pick(keys, FIELD_HINTS["id"]) or (keys[0] if keys else "id")
            options["time"] = _pick(keys, FIELD_HINTS["time"]) or options["id"]
            options["channel"] = _pick(keys, FIELD_HINTS["channel"]) or ""
            options["actor"] = _pick(keys, FIELD_HINTS["actor"]) or ""
            options["actor_name"] = _pick(keys, FIELD_HINTS["actor_name"]) or ""
            options["content"] = _pick(keys, FIELD_HINTS["content"]) or (keys[-1] if keys else "content")
        if evidence.get("list_key"):
            options["items_path"] = evidence["list_key"]
        return {
            "source_id": _auto_id(url), "kind": "http", "scope": "protocol",
            "locator": url, "options": options, "confidence": 0.5,
        }

    def _probe(self, path: Path) -> Dict[str, Any]:
        suffix = path.suffix.lower()
        evidence: Dict[str, Any] = {"path": str(path), "suffix": suffix, "size": path.stat().st_size}
        if suffix in (".db", ".sqlite", ".sqlite3"):
            try:
                conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
                conn.row_factory = sqlite3.Row
                tables = []
                for (table,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 30"
                ):
                    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    sample = [dict(r) for r in conn.execute(f"SELECT * FROM {table} LIMIT 2")]
                    tables.append({
                        "table": table, "columns": cols, "count": count,
                        "sample": [{k: _redact(str(v))[:120] for k, v in row.items()} for row in sample],
                    })
                conn.close()
                evidence["tables"] = tables
            except Exception as exc:
                evidence["error"] = str(exc)
            return evidence

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            evidence["error"] = str(exc)
            return evidence

        if suffix in (".jsonl", ".ndjson"):
            lines = [x for x in text.splitlines() if x.strip()][:5]
            evidence["lines"] = [_redact(x)[:300] for x in lines]
            try:
                first = json.loads(lines[0]) if lines else {}
                if isinstance(first, dict):
                    evidence["sample"] = [
                        {k: _redact(str(v))[:80] for k, v in first.items()}
                    ]
            except Exception:
                pass
            return evidence
        if suffix == ".json":
            try:
                data = json.loads(text)
                evidence["top_type"] = type(data).__name__
                if isinstance(data, dict):
                    evidence["keys"] = list(data.keys())[:30]
                    for key, value in data.items():
                        if isinstance(value, list) and value:
                            evidence["list_key"] = key
                            evidence["sample"] = [
                                {k: _redact(str(v))[:80] for k, v in item.items()}
                                for item in value[:2] if isinstance(item, dict)
                            ]
                            break
                elif isinstance(data, list):
                    evidence["sample"] = [
                        {k: _redact(str(v))[:80] for k, v in item.items()}
                        for item in data[:2] if isinstance(item, dict)
                    ]
            except Exception as exc:
                evidence["error"] = str(exc)
            return evidence

        evidence["lines"] = [_redact(x)[:300] for x in text.splitlines()[:10]]
        return evidence

    def _heuristic(self, path: Path, evidence: Dict[str, Any], kind: str = "") -> Dict[str, Any]:
        suffix = path.suffix.lower()
        source_id = _auto_id(path)
        if kind == "sqlite" or suffix in (".db", ".sqlite", ".sqlite3"):
            tables = evidence.get("tables") or []
            table = max(tables, key=lambda t: t.get("count", 0)) if tables else None
            options: Dict[str, Any] = {"platform": "qq"}
            if table:
                cols = table["columns"]
                options["table"] = table["table"]
                options["cursor"] = _pick(cols, FIELD_HINTS["id"]) or cols[0]
                options["id"] = _pick(cols, FIELD_HINTS["id"]) or cols[0]
                options["time"] = _pick(cols, FIELD_HINTS["time"]) or options["cursor"]
                options["channel"] = _pick(cols, FIELD_HINTS["channel"]) or ""
                options["actor"] = _pick(cols, FIELD_HINTS["actor"]) or ""
                options["actor_name"] = _pick(cols, FIELD_HINTS["actor_name"]) or ""
                options["content"] = _pick(cols, FIELD_HINTS["content"]) or cols[-1]
            return {
                "source_id": source_id, "kind": "sqlite", "scope": "plugin",
                "locator": str(path), "options": options, "confidence": 0.55,
            }
        if suffix in (".jsonl", ".ndjson") or suffix == ".json":
            options = {"platform": "qq"}
            sample = (evidence.get("sample") or [{}])[0]
            keys = list(sample.keys())
            options["id"] = _pick(keys, FIELD_HINTS["id"]) or (keys[0] if keys else "id")
            options["time"] = _pick(keys, FIELD_HINTS["time"]) or options["id"]
            options["channel"] = _pick(keys, FIELD_HINTS["channel"]) or ""
            options["actor"] = _pick(keys, FIELD_HINTS["actor"]) or ""
            options["actor_name"] = _pick(keys, FIELD_HINTS["actor_name"]) or ""
            options["content"] = _pick(keys, FIELD_HINTS["content"]) or (keys[-1] if keys else "content")
            if evidence.get("list_key"):
                options["items_path"] = evidence["list_key"]
            return {
                "source_id": source_id, "kind": "json", "scope": "plugin",
                "locator": str(path), "options": options, "confidence": 0.55,
            }
        return {
            "source_id": source_id, "kind": "log", "scope": "plugin",
            "locator": str(path), "options": {"platform": "qq"}, "confidence": 0.4,
        }

    async def _ask_ai(self, target: Any, evidence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            text, _ = await self.llm.generate_with_tools(
                system_prompt=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(evidence, ensure_ascii=False)[:12000]}],
                tool_registry=None,
                max_tool_turns=1,
                temperature=0.0,
            )
        except Exception:
            return None
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start:end + 1])
        except Exception:
            return None
        if not isinstance(data, dict) or data.get("error"):
            return None
        return data

    async def _dry_run(self, spec: BridgeSpec):
        try:
            if spec.kind == "sqlite":
                events = SqliteEventDriver(spec, emit=None, store=None).poll_once()
            elif spec.kind == "json":
                events = JsonEventDriver(spec, emit=None, store=None).preview(5)
            elif spec.kind == "log":
                events = LogEventDriver(spec, emit=None, store=None).preview(5)
            else:
                events = await HttpEventDriver(spec, emit=None, store=None).poll_once()
            return events[:5], ""
        except Exception as exc:
            return [], f"试读失败: {exc}"
