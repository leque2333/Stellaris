# -*- coding: utf-8 -*-
"""宿主内部二级插件扫描：脚本读标准格式，副手 AI 补陌生目录"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...storage import Store

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def default_plugin_roots() -> List[Path]:
    roots: List[Path] = []
    env = os.getenv("HUB_COMPONENT_ROOTS", "")
    for part in env.split(os.pathsep):
        if part.strip():
            roots.append(Path(part).expanduser())
    data_env = os.getenv("ASTRBOT_DATA_DIR", "").strip()
    if data_env:
        roots.append(Path(data_env).expanduser() / "plugins")
    for base in (Path.cwd(), Path(__file__).resolve().parents[1]):
        for rel in ("astrbot/data/plugins", "AstrBot/data/plugins", "astrbot/plugins"):
            candidate = base / rel
            if candidate.is_dir():
                roots.append(candidate)
    result = []
    seen = set()
    for root in roots:
        try:
            key = str(root.resolve())
        except OSError:
            continue
        if key not in seen and root.is_dir():
            seen.add(key)
            result.append(root)
    return result


class ComponentScanner:
    def __init__(self, store: Store):
        self.store = store

    def scan(self, host_root: Optional[str] = None, host_id: str = "astrbot") -> List[Dict[str, Any]]:
        roots = [Path(host_root).expanduser()] if host_root else default_plugin_roots()
        existing = {
            str(c.get("component_id")): c
            for c in self.store.list_components(host_id)
        }
        changed: List[Dict[str, Any]] = []
        for root in roots:
            plugins_dir = root / "plugins" if (root / "plugins").is_dir() else root
            if not plugins_dir.is_dir():
                continue
            seen_ids = set()
            for item in sorted(plugins_dir.iterdir()):
                if not item.is_dir() or item.name.startswith("."):
                    continue
                component = self._scan_one(item, host_id)
                seen_ids.add(component["component_id"])
                old = existing.get(component["component_id"])
                if old and old.get("fingerprint") == component["fingerprint"]:
                    continue
                self._save(component)
                changed.append(component)
            for old in existing.values():
                if old.get("host_id") == host_id and old.get("component_id") not in seen_ids:
                    if old.get("status") != "missing":
                        self.store.upsert_component(
                            component_id=old["component_id"], host_id=host_id,
                            name=old["name"], version=old.get("version", ""),
                            kind=old.get("kind", "secondary"), path=old.get("path", ""),
                            entry=old.get("entry", ""), config_schema=old.get("config_schema", ""),
                            category=old.get("category", "二级插件"),
                            capabilities=old.get("capabilities", []), status="missing",
                            source=old.get("source", "auto"), manifest=old.get("manifest", {}),
                            fingerprint=old.get("fingerprint", ""),
                        )
        return changed

    async def scan_all(self, host_root: Optional[str] = None, host_id: str = "astrbot") -> List[Dict[str, Any]]:
        """一次扫描：脚本找变化，副手 AI 只补本次新增/变更里缺标准信息的组件。"""
        changed = self.scan(host_root, host_id)
        candidates = [c for c in changed if c.get("status") == "needs_review"]
        if candidates:
            root = Path(host_root).expanduser() if host_root else (default_plugin_roots()[0] if default_plugin_roots() else None)
            if root:
                await self.scan_with_ai(str(root), host_id, candidates)
        return changed

    async def scan_with_ai(self, host_root: str, host_id: str = "astrbot", candidates: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        from ...execution.agent import ExecutionAgent

        root = Path(host_root).expanduser()
        plugins_dir = root / "plugins" if (root / "plugins").is_dir() else root
        agent = ExecutionAgent(roots=[plugins_dir], store=self.store)
        target_hint = ""
        if candidates:
            target_hint = "只检查这些目录：" + ", ".join(str(c.get("path") or "") for c in candidates) + "。"
        goal = (
            "检查宿主插件目录下的二级插件。" + target_hint +
            "对每个插件输出 component_id、name、version、entry、config_schema、capabilities。只输出 JSON："
            '{"components":[{"component_id":"...","name":"...","version":"",'
            '"entry":"main.py","config_schema":"","capabilities":[]}]}。'
            "不要输出解释，不要读写文件。"
        )
        result = await agent.run(goal, mode="investigate", return_format="json", max_turns=4)
        if not result.ok or not result.text:
            return []
        return self.apply_ai_components(
            self._parse_json(result.text), host_root, host_id, candidates
        )

    def apply_ai_components(
        self,
        payload: Dict[str, Any],
        host_root: str,
        host_id: str = "astrbot",
        candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """把 AI 返回的组件清单落库；有候选时只收候选，避免重复/幻觉。"""
        root = Path(host_root).expanduser()
        plugins_dir = root / "plugins" if (root / "plugins").is_dir() else root
        candidate_by_id = {str(c.get("component_id")): c for c in (candidates or [])}
        existing_ids = {
            str(c.get("component_id")) for c in self.store.list_components(host_id)
        }
        components: List[Dict[str, Any]] = []
        seen: set = set()
        for item in (payload.get("components") or []):
            if not isinstance(item, dict) or not item.get("component_id"):
                continue
            component_id = str(item["component_id"]).strip()
            if not component_id or component_id in seen:
                continue
            if candidate_by_id:
                if component_id not in candidate_by_id:
                    continue
            elif component_id in existing_ids:
                # 没有候选时只补全新目录，不覆盖脚本已识别的组件
                continue
            seen.add(component_id)
            base = candidate_by_id.get(component_id, {})
            component = {
                "component_id": component_id,
                "host_id": host_id,
                "name": str(item.get("name") or base.get("name") or component_id).strip(),
                "version": str(item.get("version") or base.get("version") or "").strip(),
                "kind": "secondary",
                "path": str(base.get("path") or (plugins_dir / component_id)),
                "entry": str(item.get("entry") or base.get("entry") or "").strip(),
                "config_schema": str(item.get("config_schema") or base.get("config_schema") or "").strip(),
                "category": "二级插件",
                "capabilities": [str(c) for c in (item.get("capabilities") or base.get("capabilities") or [])][:20],
                "status": "active",
                "source": "ai",
                "manifest": {"ai": True},
                "fingerprint": base.get("fingerprint", ""),
            }
            self._save(component)
            components.append(component)
        return components

    def _scan_one(self, path: Path, host_id: str) -> Dict[str, Any]:
        meta: Dict[str, Any] = {}
        metadata_file = path / "metadata.yaml"
        if metadata_file.is_file() and yaml is not None:
            try:
                loaded = yaml.safe_load(metadata_file.read_text(encoding="utf-8", errors="replace"))
                if isinstance(loaded, dict):
                    meta = loaded
            except Exception:
                meta = {}
        component_id = str(meta.get("name") or path.name).strip()
        name = str(meta.get("display_name") or meta.get("name") or path.name).strip()
        entry = path / "main.py"
        schema = path / "_conf_schema.json"
        capabilities = meta.get("capabilities") if isinstance(meta.get("capabilities"), list) else []
        if not capabilities and entry.is_file():
            capabilities = self._infer_capabilities(entry)
        has_metadata = bool(meta) or schema.is_file()
        return {
            "component_id": component_id,
            "host_id": host_id,
            "name": name,
            "version": str(meta.get("version") or "").strip(),
            "kind": "secondary",
            "path": str(path),
            "entry": str(entry) if entry.is_file() else "",
            "config_schema": str(schema) if schema.is_file() else "",
            "category": str(meta.get("category") or "二级插件"),
            "capabilities": capabilities[:20],
            "status": "active" if has_metadata else "needs_review",
            "source": "auto",
            "manifest": meta,
            "fingerprint": self._fingerprint(path),
        }

    @staticmethod
    def _fingerprint(path: Path) -> str:
        parts = []
        for name in ("metadata.yaml", "_conf_schema.json", "main.py"):
            file = path / name
            if file.is_file():
                try:
                    stat = file.stat()
                    parts.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
                except OSError:
                    parts.append(f"{name}:?")
        return "|".join(parts)

    @staticmethod
    def _infer_capabilities(entry: Path) -> List[str]:
        try:
            text = entry.read_text(encoding="utf-8", errors="replace")[:200000]
        except OSError:
            return []
        found = []
        for match in re.findall(r"@filter\.(command|llm_tool|event_message_type)\((.*?)\)", text, re.S):
            kind, args = match
            name_match = re.search(r"[\"']([^\"']+)[\"']", args)
            found.append(f"{kind}:{name_match.group(1) if name_match else ''}".strip(":"))
        return list(dict.fromkeys(found))[:20]

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start:end + 1])
                    return data if isinstance(data, dict) else {}
                except Exception:
                    return {}
        return {}

    def _save(self, component: Dict[str, Any]) -> None:
        self.store.upsert_component(
            component_id=component["component_id"],
            host_id=component["host_id"],
            name=component["name"],
            version=component.get("version", ""),
            kind=component.get("kind", "secondary"),
            path=component.get("path", ""),
            entry=component.get("entry", ""),
            config_schema=component.get("config_schema", ""),
            category=component.get("category", "二级插件"),
            capabilities=component.get("capabilities", []),
            status=component.get("status", "active"),
            source=component.get("source", "auto"),
            manifest=component.get("manifest", {}),
            fingerprint=component.get("fingerprint", ""),
        )
