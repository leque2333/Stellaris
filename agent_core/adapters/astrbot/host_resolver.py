# -*- coding: utf-8 -*-
"""宿主识别：一次拿到内部 UID、名称、平台和它下面的二级插件

脚本优先读宿主的 cmd_config.json / config.json；认不出来时，才让执行 AI
做一次只读调查，同时补宿主身份和二级插件，避免两次 AI 调用。
识别结果会同时写 hosts 和 platform_contexts（host_id 关联），
后续可在控制台把宿主上下文绑定到人设档案。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .component_scanner import ComponentScanner, default_plugin_roots
from ...storage import Store


class HostResolver:
    def __init__(self, store: Store, llm: Optional[Any] = None):
        self.store = store
        self.llm = llm
        self.scanner = ComponentScanner(store)

    async def resolve(
        self,
        host_root: Optional[str] = None,
        hints: Optional[Dict[str, Any]] = None,
        use_ai: bool = True,
        archive_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        hints = hints or {}
        root = Path(host_root).expanduser() if host_root else self._default_root()
        if not root or not root.exists():
            raise ValueError("宿主目录不存在")

        host = self._detect(root, hints)
        existing = {h["host_id"]: h for h in self.store.list_hosts()}
        if host["host_id"] in existing:
            old = existing[host["host_id"]]
            manifest = dict(host.get("manifest") or {})
            sources = dict(manifest.get("identity_source") or {})
            for field in ("name", "platform", "internal_uid"):
                current = str(host.get(field) or "").strip()
                stored = str(old.get(field) or "").strip()
                if not current and stored:
                    host[field] = stored
                    sources[field] = "stored"
                elif field == "name" and sources.get("name") == "dir" and stored:
                    host["name"] = stored
                    sources["name"] = "stored"
            manifest = {**(old.get("manifest") or {}), **manifest}
            manifest["identity_source"] = sources
            host["manifest"] = manifest
            host["source"] = host.get("source") or old.get("source") or "auto"

        # 脚本扫描：先拿确定的组件和需要 AI 复核的候选
        changed = self.scanner.scan(str(root), host["host_id"])
        review = [c for c in changed if c.get("status") == "needs_review"]

        ai_info: Dict[str, Any] = {"used": False, "identity": False, "components": 0, "error": ""}
        if use_ai and (not self._identity_complete(host) or review):
            payload, error = await self._investigate(root, host, review)
            ai_info["error"] = error
            if payload:
                ai_info["used"] = True
                host, identity_changed = self._merge_identity(host, payload.get("host") or {}, hints)
                ai_info["identity"] = identity_changed
                ai_components = self.scanner.apply_ai_components(
                    payload, str(root), host["host_id"], review or None
                )
                ai_info["components"] = len(ai_components)
                merged = {c["component_id"]: c for c in changed}
                for component in ai_components:
                    merged[component["component_id"]] = component
                changed = list(merged.values())

        context_id = self._record_context(host, root)
        manifest = dict(host.get("manifest") or {})
        if context_id:
            manifest["context_id"] = context_id
        manifest["identity_source"] = dict(manifest.get("identity_source") or {})
        manifest["ai"] = {
            "used": ai_info["used"],
            "identity": ai_info["identity"],
            "components": ai_info["components"],
        }
        host["manifest"] = manifest

        self.store.upsert_host(
            host["host_id"], host["name"],
            platform=host.get("platform", ""),
            internal_uid=host.get("internal_uid", ""),
            root=str(root),
            source=host.get("source", "auto"),
            manifest=manifest,
        )

        binding_id = self._bind_archive(host, hints, archive_id)
        if binding_id:
            host["binding_id"] = binding_id

        return {
            "ok": True,
            "host": host,
            "components": changed,
            "count": len(changed),
            "context_id": context_id or "",
            "ai": ai_info,
        }

    @staticmethod
    def _default_root() -> Optional[Path]:
        roots = default_plugin_roots()
        if not roots:
            return None
        root = roots[0]
        # default_plugin_roots 返回的是 .../astrbot/data/plugins；
        # HostResolver 需要宿主 data 根目录来读 cmd_config.json / data_v4.db。
        return root.parent if root.name.lower() == "plugins" else root

    def _detect(self, root: Path, hints: Dict[str, Any]) -> Dict[str, Any]:
        config = self._read_json(root / "cmd_config.json") or self._read_json(root / "config.json")
        host_id = str(hints.get("host_id") or "").strip()
        if not host_id:
            base = root.name.lower()
            host_id = "astrbot" if base in {"data", "astrbot", "plugins"} else base

        name = platform = internal_uid = ""
        sources = {"name": "dir", "platform": "", "internal_uid": ""}
        if isinstance(config, dict):
            name = str(
                config.get("bot_name")
                or config.get("name")
                or (config.get("dashboard") or {}).get("username")
                or ""
            ).strip()
            if name:
                sources["name"] = "config"
            platforms = config.get("platform") if isinstance(config.get("platform"), list) else []
            if platforms and isinstance(platforms[0], dict):
                platform = "qq"
                sources["platform"] = "config"
                internal_uid = str(
                    platforms[0].get("id") or platforms[0].get("type") or ""
                ).strip()
                if internal_uid:
                    sources["internal_uid"] = "config"

        # 显式 hints 永远优先于配置文件
        for field in ("name", "platform", "internal_uid"):
            value = str(hints.get(field) or "").strip()
            if value:
                if field == "name":
                    name = value
                elif field == "platform":
                    platform = value
                else:
                    internal_uid = value
                sources[field] = "hints"

        if not name:
            name = host_id
            sources["name"] = "dir"

        return {
            "host_id": host_id,
            "name": name,
            "platform": platform,
            "internal_uid": internal_uid,
            "root": str(root),
            "source": "auto",
            "manifest": {
                "config_found": bool(config),
                "identity_source": sources,
            },
        }

    @staticmethod
    def _identity_complete(host: Dict[str, Any]) -> bool:
        sources = (host.get("manifest") or {}).get("identity_source") or {}
        if sources.get("name") in (None, "", "dir"):
            return False
        return bool(host.get("platform")) and bool(host.get("internal_uid"))

    async def _investigate(
        self,
        root: Path,
        host: Dict[str, Any],
        candidates: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[Dict[str, Any], str]:
        """一次只读调查：宿主身份 + 二级插件，返回 (payload, error)"""
        from ...execution.agent import ExecutionAgent

        review = [c for c in (candidates or []) if c.get("status") == "needs_review"]
        review_hint = ""
        if review:
            review_hint = (
                "待确认的二级插件目录（脚本缺标准文件）："
                + ", ".join(str(c.get("path") or c.get("component_id") or "") for c in review)
                + "。\n"
            )
        goal = (
            f"宿主目录：{root}\n"
            f"脚本已识别：host_id={host['host_id']}，"
            f"name={host.get('name') or '未知'}，"
            f"platform={host.get('platform') or '未知'}，"
            f"internal_uid={host.get('internal_uid') or '未知'}\n"
            f"{review_hint}"
            "请用只读工具读取该目录下的 cmd_config.json / config.json / config.yaml / "
            "plugins/*/metadata.yaml / main.py，完成两件事：\n"
            "1. 识别宿主自身身份：平台适配器 UID、显示名称、平台；\n"
            "2. 列出宿主内部的二级插件清单。\n"
            "只输出 JSON："
            '{"host":{"host_id":"","name":"","platform":"","internal_uid":"","evidence":[]},'
            '"components":[{"component_id":"","name":"","version":"","entry":"",'
            '"config_schema":"","capabilities":[]}]}\n'
            "platform 取 qq / bilibili / telegram / other 之一；internal_uid 找不到就留空，"
            "禁止编造；没有二级插件就输出空数组。"
        )
        agent = ExecutionAgent(roots=[root], llm=self.llm, store=self.store)
        result = await agent.run(goal, mode="investigate", return_format="json", max_turns=4)
        if not result.ok or not result.text:
            return {}, result.error or "执行 AI 没有返回结果"
        payload = ComponentScanner._parse_json(result.text)
        if not payload:
            return {}, "执行 AI 返回的不是合法 JSON"
        return payload, ""

    @staticmethod
    def _merge_identity(
        host: Dict[str, Any],
        ai_host: Dict[str, Any],
        hints: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], bool]:
        """只让 AI 补脚本/人工都没给出的字段，不覆盖 hints 和配置里的值"""
        if not isinstance(ai_host, dict) or not ai_host:
            return host, False
        manifest = dict(host.get("manifest") or {})
        sources = dict(manifest.get("identity_source") or {})
        changed = False
        for field in ("name", "platform", "internal_uid"):
            if hints.get(field):
                continue
            if sources.get(field) in ("hints", "config"):
                continue
            value = str(ai_host.get(field) or "").strip()
            if not value or value == str(host.get(field) or "").strip():
                continue
            host[field] = value
            sources[field] = "ai"
            changed = True
        if changed:
            manifest["identity_source"] = sources
            host["manifest"] = manifest
            host["source"] = "ai"
        return host, changed

    def _record_context(self, host: Dict[str, Any], root: Path) -> Optional[str]:
        """宿主身份进外部上下文台账，用 host_id 关联 hosts 表"""
        platform = str(host.get("platform") or "").strip()
        external_id = str(host.get("internal_uid") or "").strip()
        if not platform or not external_id:
            return None
        try:
            return self.store.upsert_context(
                platform,
                external_id,
                external_type="host",
                display_name=str(host.get("name") or host["host_id"]),
                source="host_resolver",
                evidence=[
                    f"hosts.host_id={host['host_id']}",
                    f"host_root={root}",
                ],
                metadata={
                    "host_id": host["host_id"],
                    "host_root": str(root),
                    "host_source": host.get("source", "auto"),
                },
                host_id=host["host_id"],
            )
        except Exception:
            return None

    def _bind_archive(
        self,
        host: Dict[str, Any],
        hints: Dict[str, Any],
        archive_id: Optional[str],
    ) -> Optional[str]:
        target = str(archive_id or hints.get("archive_id") or "").strip()
        platform = str(host.get("platform") or "").strip()
        external_id = str(host.get("internal_uid") or "").strip()
        if not target or not platform or not external_id:
            return None
        if not self.store.get_archive(target):
            return None
        try:
            return self.store.bind_context(
                platform, external_id, target,
                confidence=0.9,
                verified_by="host_resolver",
            )
        except Exception:
            return None

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None
