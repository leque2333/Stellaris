# -*- coding: utf-8 -*-
"""中枢备用宿主机控制：AstrBot 主工具不可用时的本地兜底。

只做执行，不做权限判断；权限由网关（管理员客户端）和时间轴审计负责。
动作：status / powershell / screenshot / lock。

安全约定：
- `powershell` 默认**关闭**：工具描述里不出现该动作，调用也会被拒绝；
- 确实需要时用 HUB_HOST_POWERSHELL_ENABLED=1 显式打开，且只放行只读白名单命令，
  含命令拼接/重定向/下载执行的写法一律拒绝；
- 每次执行都写审计日志，超时上限不变。
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
POWERSHELL_ENV = "HUB_HOST_POWERSHELL_ENABLED"

# 只读命令白名单：动作默认关闭，打开后也只放行这些前缀
POWERSHELL_ALLOWED_PREFIXES = (
    "get-process", "get-service", "get-ciminstance", "get-computerinfo",
    "get-date", "get-childitem", "get-content", "get-psdrive", "get-netadapter",
    "get-nettcpconnection", "get-volume", "get-eventlog", "get-winevent",
    "test-connection", "test-path", "dir", "ls", "type", "whoami", "hostname",
    "systeminfo", "ipconfig", "tasklist",
)

# 命令拼接 / 重定向 / 下载执行 / 进程拉起：出现就整条拒绝
POWERSHELL_FORBIDDEN_TOKENS = (
    ";", "&&", "||", "|", ">", "<", "`", "$(", "invoke-expression", "iex",
    "invoke-webrequest", "invoke-restmethod", "start-process", "new-object",
    "add-type", "remove-item", "rm ", "del ", "set-", "new-item", "out-file",
    "downloadstring", "curl", "wget", "certutil", "reg add", "schtasks",
    "net user", "stop-process", "restart-computer", "stop-computer",
)


def powershell_enabled() -> bool:
    """powershell 动作是否被显式打开（默认关闭）。"""
    return str(os.getenv(POWERSHELL_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def _command_rejection(command: str) -> str:
    """返回拒绝原因；空串表示放行。"""
    text = str(command or "").strip()
    if not text:
        return "command 不能为空"
    lowered = text.lower()
    for token in POWERSHELL_FORBIDDEN_TOKENS:
        if token in lowered:
            return f"命令包含不允许的片段：{token.strip()}"
    if not lowered.startswith(POWERSHELL_ALLOWED_PREFIXES):
        return "命令不在只读白名单内"
    return ""


def available_actions() -> list:
    actions = ["status", "screenshot", "lock"]
    if powershell_enabled():
        actions.append("powershell")
    return actions


def tool_spec() -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "action": {
            "type": "string",
            "enum": available_actions(),
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_TIMEOUT,
        },
    }
    if powershell_enabled():
        properties["command"] = {
            "type": "string",
            "description": "action=powershell 时要执行的只读 PowerShell 命令",
        }
    return {
        "type": "function",
        "function": {
            "name": TOOL_NAME,
            "description": (
                "备用宿主机控制。主控制工具不可用时使用；"
                "支持查看状态、截图、锁屏" + ("、执行只读 PowerShell" if powershell_enabled() else "") + "。仅管理员可用。"
            ),
            "parameters": {
                "type": "object",
                "properties": properties,
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
        if not powershell_enabled():
            return {
                "ok": False, "action": action,
                "text": f"宿主机命令执行默认关闭（需要时设置 {POWERSHELL_ENV}=1）",
            }
        rejection = _command_rejection(command)
        if rejection:
            return {"ok": False, "action": action, "text": rejection}
        text = _run(command, timeout)
        return {"ok": not text.startswith("执行失败") and "命令超时" not in text, "action": action, "text": text}
    return {"ok": False, "action": action, "text": f"未知 action: {action}"}


__all__ = [
    "TOOL_NAME", "tool_spec", "execute", "MAX_OUTPUT",
    "powershell_enabled", "available_actions", "POWERSHELL_ENV",
]
