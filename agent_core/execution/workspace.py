# -*- coding: utf-8 -*-
"""工作区工具：只允许在工作区内读写，命令走白名单"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from ..connectors.ai_matcher import _ReadOnlyTools

DEFAULT_ALLOWED = {"python", "python3", "pytest", "node", "npm", "git"}
GIT_SUBCOMMANDS = {"status", "diff", "log", "show", "branch", "rev-parse", "describe"}
NPM_SUBCOMMANDS = {"test", "run", "start", "--version"}
PROTECTED_FILES = {
    "data/memory.db",
    "data/clients.json",
    "data/models_config.json",
    "data/compute_providers.json",
    "data/auth_config.json",
    "data/execution_policy.json",
}
PROTECTED_DIRS = {".git", "backups", "__pycache__", ".env"}
MAX_WRITE_BYTES = 500 * 1024


class WorkspaceTools(_ReadOnlyTools):
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        super().__init__([self.workspace])

    def _resolve(self, raw: str) -> Optional[Path]:
        try:
            path = Path(raw)
            if not path.is_absolute():
                path = self.workspace / path
            path = path.resolve()
            path.relative_to(self.workspace)
            return path
        except Exception:
            return None

    def _protected(self, path: Path) -> bool:
        try:
            rel = path.relative_to(self.workspace).as_posix()
        except ValueError:
            return True
        if rel in PROTECTED_FILES:
            return True
        return any(rel == d or rel.startswith(d + "/") for d in PROTECTED_DIRS)

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve(path)
        if not target:
            return "路径不在工作区"
        if self._protected(target):
            return "受保护文件/目录，拒绝写入"
        data = str(content or "").encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            return "文件过大，超过 500KB"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backup_dir = self.workspace / "backups" / "execution"
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / f"{int(time.time())}_{target.name}"
                shutil.copy2(target, backup)
            target.write_bytes(data)
            return f"已写入 {target} ({len(data)} bytes)"
        except Exception as exc:
            return f"写入失败: {exc}"

    def run_command(self, command: str, timeout: int = 60) -> str:
        raw = str(command or "").strip()
        if not raw:
            return "命令为空"
        if any(ch in raw for ch in (";", "|", "&", ">", "<", "`", "$(")):
            return "命令包含不允许的 shell 操作符"
        try:
            argv = shlex.split(raw, posix=False)
        except Exception as exc:
            return f"命令解析失败: {exc}"
        if not argv:
            return "命令为空"
        executable = Path(argv[0]).name.lower().removesuffix(".exe")
        allowed = {
            x.strip().lower() for x in os.getenv("HUB_EXEC_ALLOW", "").split(",") if x.strip()
        } or DEFAULT_ALLOWED
        if executable not in allowed:
            return f"命令 {executable} 不在白名单"
        if executable == "git":
            sub = argv[1].lower() if len(argv) > 1 else ""
            if sub not in GIT_SUBCOMMANDS:
                return f"git {sub or '(空)'} 不在白名单"
        if executable == "npm":
            sub = argv[1].lower() if len(argv) > 1 else ""
            if sub not in NPM_SUBCOMMANDS:
                return f"npm {sub or '(空)'} 不在白名单"
        timeout = max(1, min(int(timeout or 60), 120))
        try:
            proc = subprocess.run(
                argv,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            return f"exit={proc.returncode}\n{output[-4000:]}"
        except subprocess.TimeoutExpired:
            return f"命令超时（{timeout}s）"
        except Exception as exc:
            return f"执行失败: {exc}"
