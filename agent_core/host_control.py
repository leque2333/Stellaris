# -*- coding: utf-8 -*-
"""中枢备用宿主机控制：AstrBot 主工具不可用时的本地兜底。

只做执行，不做权限判断；权限由网关（管理员客户端）和时间轴审计负责。
动作：status / powershell / screenshot / lock。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

TOOL_NAME = "hub_host_control"
MAX_OUTPUT = 12000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 180


def tool_spec() -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "备用宿主机控制。主控制工具不可用时使用；"
                "支持查看状态、执行 PowerShell、截图、锁屏。仅管理员可用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["status", "powershell", "screenshot", "lock"],
                    },
                    "command": {
                        "type": "string",
                        "description": "action=powershell 时要执行的 PowerShell 命令",
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_TIMEOUT,
                    },
                },
                "required": ["action"],
            },
        },
    }


def _temp_dir() -> Path:
    raw = os.getenv("HUB_HOST_TEMP", "")
    path = Path(raw) if raw else Path(__file__).resolve().parents[1] / "data" / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run(command: str, timeout: int) -> str:
    try:
        proc = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", command,
            ],
            capture_output=True,
            text=True,
            timeout=max(1, min(int(timeout or DEFAULT_TIMEOUT), MAX_TIMEOUT)),
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return "命令超时，已中止"
    except Exception as exc:
        return f"执行失败: {exc}"
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    output = output or "[无输出]"
    suffix = "" if proc.returncode == 0 else f"\nexit={proc.returncode}"
    return (output + suffix)[-MAX_OUTPUT:]


def _status() -> str:
    try:
        import psutil

        mem = psutil.virtual_memory()
        parts = [
            f"CPU {psutil.cpu_percent(interval=0.3):.1f}%",
            f"内存 {mem.used / 1024**3:.1f}G/{mem.total / 1024**3:.1f}G ({mem.percent:.0f}%)",
            f"开机 {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(psutil.boot_time()))}",
        ]
        procs = sorted(
            psutil.process_iter(["name", "memory_percent"]),
            key=lambda p: p.info.get("memory_percent") or 0,
            reverse=True,
        )[:5]
        top = [f"{p.info['name']}({p.info['memory_percent']:.1f}%)" for p in procs]
        return "\n".join(parts + (["高内存: " + " / ".join(top)] if top else []))
    except Exception:
        return _run(
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object LastBootUpTime,FreePhysicalMemory,TotalVisibleMemorySize | Format-List",
            DEFAULT_TIMEOUT,
        )


def _screenshot() -> str:
    target = _temp_dir() / f"host_control_{int(time.time())}.jpg"
    quoted = str(target).replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        f"$bmp.Save('{quoted}',[System.Drawing.Imaging.ImageFormat]::Jpeg);"
        "$g.Dispose();$bmp.Dispose()"
    )
    result = _run(script, 20)
    if target.exists() and target.stat().st_size > 0:
        return f"截图已保存: {target}"
    return f"截图失败: {result}"


def execute(action: str, command: str = "", timeout: int = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    action = str(action or "").strip().lower()
    if action == "status":
        return {"ok": True, "action": action, "text": _status()}
    if action == "screenshot":
        text = _screenshot()
        return {"ok": text.startswith("截图已保存"), "action": action, "text": text}
    if action == "lock":
        try:
            subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"], timeout=10, shell=False)
            return {"ok": True, "action": action, "text": "已锁定屏幕"}
        except Exception as exc:
            return {"ok": False, "action": action, "text": f"锁屏失败: {exc}"}
    if action == "powershell":
        if not str(command or "").strip():
            return {"ok": False, "action": action, "text": "command 不能为空"}
        text = _run(command, timeout)
        return {"ok": not text.startswith("执行失败") and "命令超时" not in text, "action": action, "text": text}
    return {"ok": False, "action": action, "text": f"未知 action: {action}"}


__all__ = ["TOOL_NAME", "tool_spec", "execute", "MAX_OUTPUT"]
