# -*- coding: utf-8 -*-
"""源码分析：从开源仓库提取接入证据（不按插件名写死）"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from ..connectors.ai_matcher import _redact

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", "target", "vendor", "backups", "temp", ".idea", ".vscode",
}
LANG_BY_SUFFIX = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".php": "php", ".rb": "ruby", ".cs": "csharp",
}
MANIFESTS = {
    "pyproject.toml", "requirements.txt", "setup.py", "setup.cfg",
    "package.json", "go.mod", "Cargo.toml", "pom.xml", "build.gradle",
}
FRAMEWORKS = {
    "nonebot": "NoneBot", "astrbot": "AstrBot", "koishi": "Koishi",
    "yunzai": "Yunzai", "fastapi": "FastAPI", "flask": "Flask",
    "django": "Django", "aiohttp": "aiohttp", "websockets": "websockets",
    "sqlalchemy": "SQLAlchemy", "prisma": "Prisma", "typeorm": "TypeORM",
    "express": "Express", "koa": "Koa", "socket.io": "Socket.IO",
    "discord": "Discord", "telegram": "Telegram",
}
ROUTE_PATTERNS = [
    re.compile(r"@(?:app|router|bp|blueprint)\.(get|post|put|delete|patch|websocket)\("),
    re.compile(r"\b(?:app|router|server)\.(get|post|put|delete|patch)\("),
    re.compile(r"\b(?:ctx|session|bot|client)\.on\("),
    re.compile(r"\b(?:on_message|on_notice|on_request|message_received)\b"),
    re.compile(r"\b(?:add_command|register_command|command)\("),
]
LOG_PATTERNS = [
    re.compile(r"\blogger\.(info|debug|warning|warn|error)\("),
    re.compile(r"\blogging\.(info|debug|warning|error)\("),
    re.compile(r"\bconsole\.(log|info|warn|error)\("),
    re.compile(r"\bprint\("),
]
CONFIG_NAMES = ("config", "settings", "schema", "env", "options", "conf")


class SourceAnalyzer:
    def __init__(self, max_files: int = 4000):
        self.max_files = max_files

    def analyze(self, repo_path: str) -> Dict[str, Any]:
        root = Path(repo_path).resolve()
        if not root.exists() or not root.is_dir():
            return {"ok": False, "error": "仓库路径不存在或不是目录"}

        languages: Dict[str, int] = {}
        manifests: List[Dict[str, Any]] = []
        configs: List[str] = []
        databases: List[Dict[str, Any]] = []
        routes: List[Dict[str, Any]] = []
        logs: List[Dict[str, Any]] = []
        tests: List[str] = []
        entrypoints: List[str] = []
        files_scanned = 0

        for path in root.rglob("*"):
            if files_scanned >= self.max_files:
                break
            if not path.is_file():
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            files_scanned += 1
            suffix = path.suffix.lower()
            rel = str(path.relative_to(root)).replace("\\", "/")
            if suffix in LANG_BY_SUFFIX:
                languages[LANG_BY_SUFFIX[suffix]] = languages.get(LANG_BY_SUFFIX[suffix], 0) + 1

            if path.name in MANIFESTS:
                manifests.append({
                    "path": rel,
                    "name": path.name,
                    "text": _redact(self._read(path, 30000)),
                })
                continue

            if suffix in (".db", ".sqlite", ".sqlite3"):
                databases.append(self._db_info(path, rel))
                continue

            if path.name.lower().startswith(".env") or (
                suffix in (".json", ".yaml", ".yml", ".toml", ".ini", ".conf")
                and any(x in path.name.lower() for x in CONFIG_NAMES)
            ):
                if len(configs) < 30:
                    configs.append(rel)
                continue

            if "test" in rel.lower():
                if len(tests) < 30:
                    tests.append(rel)
                continue

            if path.name.lower() in ("main.py", "app.py", "bot.py", "index.js", "index.ts", "main.go"):
                entrypoints.append(rel)

            if suffix in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs"):
                text = self._read(path, 200000)
                for lineno, line in enumerate(text.splitlines(), 1):
                    snippet = line.strip()
                    if not snippet:
                        continue
                    if any(p.search(snippet) for p in ROUTE_PATTERNS):
                        if len(routes) < 60:
                            routes.append({"path": rel, "line": lineno, "text": _redact(snippet)[:200]})
                    if any(p.search(snippet) for p in LOG_PATTERNS):
                        if len(logs) < 60:
                            logs.append({"path": rel, "line": lineno, "text": _redact(snippet)[:200]})

        frameworks = self._detect_frameworks(manifests)
        return {
            "ok": True,
            "repo": str(root),
            "files_scanned": files_scanned,
            "languages": dict(sorted(languages.items(), key=lambda x: x[1], reverse=True)),
            "frameworks": frameworks,
            "manifests": [{"path": x["path"], "name": x["name"]} for x in manifests],
            "entrypoints": sorted(set(entrypoints))[:20],
            "configs": configs,
            "databases": databases,
            "routes": routes,
            "logs": logs,
            "tests": tests,
        }

    @staticmethod
    def _read(path: Path, limit: int) -> str:
        try:
            if path.stat().st_size > limit:
                return ""
            return path.read_text(encoding="utf-8", errors="ignore")[:limit]
        except Exception:
            return ""

    @staticmethod
    def _detect_frameworks(manifests: List[Dict[str, Any]]) -> List[str]:
        text = "\n".join(x.get("text", "") for x in manifests).lower()
        found = []
        for key, name in FRAMEWORKS.items():
            if key in text and name not in found:
                found.append(name)
        return found

    @staticmethod
    def _db_info(path: Path, rel: str) -> Dict[str, Any]:
        info: Dict[str, Any] = {"path": rel, "tables": []}
        try:
            conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3)
            for (table,) in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 40"
            ):
                cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
                info["tables"].append({"table": table, "columns": cols})
            conn.close()
        except Exception as exc:
            info["error"] = str(exc)
        return info


def analyze_repo(repo_path: str) -> Dict[str, Any]:
    return SourceAnalyzer().analyze(repo_path)
