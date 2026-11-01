# -*- coding: utf-8 -*-
"""部署自检：端口、挂载、密钥、安全配置、审计日志。

命令行：python scripts/check_deploy.py
控制台：设置 → 运行日志 → 部署自检（GET /api/deploy_check）
重启后：后台自动跑一次，只在有失败时提醒
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DOCKER_SUBNET = "172.16.0.0/12"


@dataclass
class Check:
    name: str
    status: str  # ok / warn / fail
    detail: str

    def as_dict(self) -> Dict[str, str]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _ok(name: str, detail: str = "") -> Check:
    return Check(name, "ok", detail)


def _warn(name: str, detail: str) -> Check:
    return Check(name, "warn", detail)


def _fail(name: str, detail: str) -> Check:
    return Check(name, "fail", detail)


def check_port(host: str, port: int, timeout: float = 1.0) -> Check:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        code = sock.connect_ex((host, port))
    except OSError as exc:
        return _fail("中枢端口", f"{host}:{port} 连接异常：{exc}")
    finally:
        sock.close()
    if code == 0:
        return _ok("中枢端口", f"{host}:{port} 在监听")
    return _fail("中枢端口", f"{host}:{port} 连不上（中枢没起？）")


def docker_containers(names: List[str]) -> List[str]:
    """返回本机实际在跑的容器名（没有 docker 就返回空）。"""
    try:
        out = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    running = {line.strip() for line in (out.stdout or "").splitlines() if line.strip()}
    return [name for name in names if name in running]


def container_mounts(name: str) -> List[Tuple[str, str]]:
    """返回 [(宿主机路径, 容器内路径)]。"""
    try:
        out = subprocess.run(
            ["docker", "inspect", name, "--format", "{{json .Mounts}}"],
            capture_output=True, text=True, timeout=8,
        )
        mounts = json.loads(out.stdout or "[]")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return []
    return [
        (item.get("Source", ""), item.get("Destination", ""))
        for item in mounts
        if item.get("Type") == "bind"
    ]


def check_mounts(container: str) -> List[Check]:
    if container not in docker_containers([container]):
        return [_warn("docker 挂载", f"没找到运行中的容器 {container}（跳过了）")]
    results = []
    for source, target in container_mounts(container):
        if os.path.exists(source):
            results.append(_ok("docker 挂载", f"{target} ← {source}"))
        else:
            results.append(_fail("docker 挂载", f"{target} 的源 {source} 不存在（容器里是悬空挂载）"))
    return results or [_warn("docker 挂载", f"{container} 没有 bind 挂载")]


def docker_subnet_missing(config: Dict[str, Any], has_docker: bool) -> bool:
    """开了 local_only、有 docker、但白名单没覆盖 docker 网段 → True。"""
    if not has_docker or not config.get("local_only"):
        return False
    for item in config.get("allow_cidrs") or []:
        try:
            if ipaddress.ip_network(DOCKER_SUBNET, strict=False).subnet_of(
                ipaddress.ip_network(str(item), strict=False)
            ):
                return False
        except (ValueError, TypeError):
            continue
    return True


def check_security(has_docker: bool) -> List[Check]:
    from agent_core import api_security

    status = api_security.status()
    signature = "开" if status["signature"]["enabled"] else "关"
    results = [_ok("安全配置", f"bind={status['bind_host']} local_only={status['local_only']} 签名={signature}")]
    if status["local_only"] and not status["allow_cidrs"]:
        results.append(_fail("来源白名单", "local_only 开着但白名单为空，谁都进不来"))
    if docker_subnet_missing(status, has_docker):
        results.append(_warn(
            "来源白名单",
            f"local_only 已开但没放行 {DOCKER_SUBNET}，容器里的 AstrBot 会 403；"
            "执行 python scripts/setup_security.py --lan",
        ))
    if status["local_only"] and status["bind_host"] == "0.0.0.0":
        results.append(_warn("监听地址", "0.0.0.0 + local_only：靠应用层拦截，建议确认防火墙"))
    return results


def check_clients() -> List[Check]:
    path = ROOT / "data" / "clients.json"
    if not path.exists():
        return [_warn("客户端 Key", f"没有 {path}（默认 require_key=true，/v1 与 /hub/v1 会拒绝所有匿名请求）")]
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    clients = data.get("clients") or []
    total_keys = sum(len(item.get("keys") or []) for item in clients)
    if not data.get("require_key"):
        return [_warn("客户端 Key", "require_key=false：匿名请求按访客处理（管理接口仍要求 Key）")]
    if total_keys == 0:
        return [_fail("客户端 Key", "require_key=true 但一个 Key 都没有，谁都调不通")]
    return [_ok("客户端 Key", f"require_key=true，{len(clients)} 个客户端 / {total_keys} 个 Key")]


def check_audit() -> List[Check]:
    from agent_core import audit_log

    try:
        audit_log.record("deploy_check", dedupe=False, ip="local")
    except Exception as exc:  # noqa: BLE001
        return [_fail("审计日志", f"写不进去：{exc}")]
    info = audit_log.stats()
    size_kb = round(info["bytes"] / 1024, 1)
    return [_ok("审计日志", f"{info['dir']} · {info['files']} 个文件 · {size_kb} KB · 保留 {info['retention_days']} 天")]


def run_checks(host: str = "127.0.0.1", port: int = 18895, container: str = "astrbot") -> List[Check]:
    """跑完全部检查；供 CLI、HTTP 接口、启动自检共用。"""
    has_docker = bool(docker_containers([container]))
    checks: List[Check] = [check_port(host, port)]
    checks.extend(check_mounts(container))
    checks.extend(check_security(has_docker))
    checks.extend(check_clients())
    checks.extend(check_audit())
    return checks


def summarize(checks: List[Check]) -> Dict[str, Any]:
    fails = sum(1 for item in checks if item.status == "fail")
    warns = sum(1 for item in checks if item.status == "warn")
    return {
        "total": len(checks),
        "ok": len(checks) - fails - warns,
        "warn": warns,
        "fail": fails,
        "failed": fails > 0,
    }


def format_lines(checks: List[Check]) -> List[str]:
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    return [f"{icons[item.status]} {item.name:10s} {item.detail}" for item in checks]
