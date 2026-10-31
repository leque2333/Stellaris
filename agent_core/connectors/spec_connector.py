# -*- coding: utf-8 -*-
"""按 spec 读取插件上下文：不认识具体插件，只认识 sqlite/json 规则"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .base import Connector, ConnectorReport, DiscoveredContext

SPEC_FILE = Path(__file__).resolve().parents[2] / "data" / "plugin_specs.json"


def _resolve_candidates(candidates: Iterable[str], override: Optional[Path] = None) -> Optional[Path]:
    if override is not None:
        path = Path(override).resolve()
        return path if path.exists() else None
    for raw in candidates:
        text = str(raw)
        if text.startswith("{env:"):
            key = text[5:-1]
            value = os.getenv(key)
            if not value:
                continue
            text = value
        elif text.startswith("{cwd}"):
            text = str(Path.cwd()) + text[5:]
        path = Path(text)
        if path.exists():
            return path.resolve()
    return None


def _get_pointer(data: Any, pointer: str) -> Any:
    cur = data
    for part in str(pointer or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except Exception:
                return None
        else:
            return None
    return cur


def _template(template: str, values: Dict[str, Any]) -> str:
    out = str(template or "")
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value if value is not None else ""))
    return out


def _parse_umo(umo: str):
    parts = str(umo or "").split(":")
    if len(parts) < 3:
        return "qq", "group", parts[-1] if parts else ""
    kind, ident = parts[1], parts[2]
    if kind == "GroupMessage":
        return "qq", "group", ident
    if kind == "FriendMessage":
        return "qq", "friend", ident
    return "qq", kind.lower(), ident


class SpecConnector(Connector):
    name = "spec"

    def __init__(self, data_dir: Optional[Path] = None, specs_path: Optional[Path] = None):
        self.data_dir = Path(data_dir) if data_dir else None
        self.specs_path = Path(specs_path) if specs_path else SPEC_FILE
        self.specs = self._load_specs()

    def _load_specs(self) -> List[Dict[str, Any]]:
        if not self.specs_path.exists():
            return []
        try:
            data = json.loads(self.specs_path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data.get("specs") or []

    def _read_source(self, source: Dict[str, Any], base: Path) -> Any:
        kind = source.get("type")
        path = base / str(source.get("path") or "")
        if kind == "sqlite":
            if not path.exists():
                return None
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(str(source.get("query") or "")).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        if kind == "json":
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            value = _get_pointer(data, str(source.get("pointer") or ""))
            if source.get("keys") and isinstance(value, dict):
                return list(value.keys())
            return value
        return None

    def _iter_values(self, source: Any) -> Iterable[Any]:
        if source is None:
            return []
        if isinstance(source, list):
            return source
        if isinstance(source, tuple):
            return list(source)
        return [source]

    def discover(self) -> ConnectorReport:
        if not self.specs:
            return ConnectorReport(
                connector=self.name, ok=False, source_path=str(self.specs_path),
                error="没有可读取的插件 spec",
            )

        contexts: Dict[tuple, DiscoveredContext] = {}
        loaded = 0
        for spec in self.specs:
            base = _resolve_candidates(spec.get("base_dir_candidates") or [], self.data_dir)
            if not base:
                continue
            values: Dict[str, Any] = {}
            for source in spec.get("sources") or []:
                try:
                    values[source.get("as")] = self._read_source(source, base)
                except Exception:
                    values[source.get("as")] = None
            loaded += 1

            for rule in spec.get("contexts") or []:
                raw = values.get(rule.get("from"))
                exclude = set(
                    str(x) for x in self._iter_values(values.get(rule.get("exclude_if_in")))
                ) if rule.get("exclude_if_in") else set()
                for item in self._iter_values(raw):
                    if isinstance(item, str) and not item.strip():
                        continue
                    if isinstance(item, dict):
                        template_values = dict(item)
                        template_values.setdefault("value", next(iter(item.values()), ""))
                    else:
                        template_values = {"value": item, "key": item}
                    if str(template_values.get("value", "")) in exclude:
                        continue
                    external_id = _template(rule.get("external_id") or "", template_values).strip()
                    if not external_id:
                        continue
                    platform = str(rule.get("platform") or "")
                    external_type = str(rule.get("type") or "")
                    if external_type == "umo":
                        platform, external_type, ident = _parse_umo(external_id)
                        template_values.setdefault("value", ident)
                    display = _template(rule.get("display") or "", template_values).strip()
                    if external_type == "group" and not display:
                        display = f"QQ群 {external_id.rsplit(':', 1)[-1]}"
                    key = (platform or "qq", external_id)
                    ctx = contexts.get(key)
                    if ctx is None:
                        ctx = DiscoveredContext(
                            platform=platform or "qq",
                            external_id=external_id,
                            external_type=external_type,
                            display_name=display,
                            source=str(spec.get("name") or self.name),
                            evidence=[],
                            metadata={"spec": spec.get("name")},
                        )
                        contexts[key] = ctx
                    elif display and (ctx.display_name.startswith("QQ") or not ctx.display_name):
                        ctx.display_name = display
                    evidence = str(rule.get("evidence") or "")
                    if evidence and evidence not in ctx.evidence:
                        ctx.evidence.append(evidence)

        return ConnectorReport(
            connector=self.name,
            ok=loaded > 0,
            source_path=str(self.specs_path),
            contexts=sorted(contexts.values(), key=lambda c: (c.external_type, c.external_id)),
            error="" if loaded else "没有匹配到可用的数据目录",
        )


def spec_data_dirs() -> List[Path]:
    connector = SpecConnector()
    dirs: List[Path] = []
    for spec in connector.specs:
        base = _resolve_candidates(spec.get("base_dir_candidates") or [])
        if base and base not in dirs:
            dirs.append(base)
    return dirs
