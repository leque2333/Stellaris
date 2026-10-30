# -*- coding: utf-8 -*-
"""AstrBot 宿主适配器：只在控制台里做可选发现，不进入中枢核心"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


class AstrBotAdapter:
    """可选宿主适配：没有 AstrBot 时所有路径返回空，控制台照常运行。"""

    def __init__(self) -> None:
        self.data_dir = self._resolve_data_dir()

    @staticmethod
    def _resolve_data_dir() -> Optional[Path]:
        env = os.getenv("ASTRBOT_DATA_DIR", "").strip()
        if env:
            path = Path(env).expanduser()
            return path if path.is_dir() else None

        roots = [
            Path(__file__).resolve().parents[2],
            Path.cwd(),
        ]
        for root in roots:
            for rel in ("astrbot/data", "AstrBot/data", "astrbot"):
                candidate = root / rel
                if candidate.is_dir() and (
                    (candidate / "cmd_config.json").exists()
                    or (candidate / "plugins").is_dir()
                ):
                    return candidate
        return None

    def path(self, name: str) -> str:
        if not self.data_dir:
            return ""
        candidate = self.data_dir / name
        return str(candidate) if candidate.exists() else ""

    @property
    def available(self) -> bool:
        return self.data_dir is not None

    def cmd_config_path(self) -> str:
        return self.path("cmd_config.json")

    def plugins_dir(self) -> str:
        path = self.data_dir / "plugins" if self.data_dir else None
        return str(path) if path and path.is_dir() else ""

    def mcp_path(self) -> str:
        return self.path("mcp.json")

    def v4_db_path(self) -> str:
        return self.path("astrbot_v4.db")
