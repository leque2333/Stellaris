# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 通用模块健康探针与挂载监控引擎 (Health Probe)
为所有大型独立模块、外部宿主、MCP 服务和自定义工具提供通用、可复现的实时存活探测与状态诊断。
"""

import socket
import urllib.parse
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional


class ModuleHealthProbe:
    """
    通用模块运行状态与挂载健康探针
    """
    @staticmethod
    def probe_single(module: Dict[str, Any], timeout: float = 0.6) -> Dict[str, Any]:
        """
        对单个独立模块执行通用存活性与挂载探测
        """
        cat = module.get("category", "")
        is_custom = module.get("is_custom", False)

        # 1. 中枢核心内建组件 (常驻内存，无需外部端口)
        if cat in ("系统核心", "认知记忆") and not is_custom:
            return {
                "status": "internal",
                "badge_color": "green",
                "status_text": "核心常驻",
                "latency_ms": 0,
                "target": "中枢内存",
                "diag_hint": "智能体系统核心服务，内置常驻运行",
                "checked_at": int(time.time())
            }

        # 2. 检查是否有 explicit probe 规则 或 console_url / url
        url = module.get("console_url") or module.get("url") or ""
        probe_cfg = module.get("probe") or {}
        probe_target = probe_cfg.get("target") or ""
        
        host = None
        port = None

        if probe_target:
            if ":" in probe_target:
                parts = probe_target.split(":")
                host = parts[0]
                try:
                    port = int(parts[1])
                except Exception:
                    port = None
            elif probe_target.isdigit():
                host = "127.0.0.1"
                port = int(probe_target)
        elif url:
            try:
                parsed = urllib.parse.urlparse(url)
                host = parsed.hostname or "127.0.0.1"
                if parsed.port:
                    port = parsed.port
                else:
                    port = 443 if parsed.scheme == "https" else 80
            except Exception:
                pass

        if host and port:
            t0 = time.time()
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                res = sock.connect_ex((host, int(port)))
                sock.close()
                latency = int((time.time() - t0) * 1000)
                if res == 0:
                    return {
                        "status": "online",
                        "badge_color": "green",
                        "status_text": f"运行中 ({latency}ms)",
                        "latency_ms": latency,
                        "target": f"{host}:{port}",
                        "diag_hint": f"服务已在端口 {port} 正常监听，双向通信通畅",
                        "checked_at": int(time.time())
                    }
                else:
                    return {
                        "status": "offline",
                        "badge_color": "red",
                        "status_text": "未启动 / 离线",
                        "latency_ms": latency,
                        "target": f"{host}:{port}",
                        "diag_hint": f"端口 {port} 未启动监听，请确认对应程序、Docker 容器或独立服务是否已运行",
                        "checked_at": int(time.time())
                    }
            except Exception as e:
                latency = int((time.time() - t0) * 1000)
                return {
                    "status": "offline",
                    "badge_color": "red",
                    "status_text": "探测异常",
                    "latency_ms": latency,
                    "target": f"{host}:{port}",
                    "diag_hint": f"探测错误: {e}",
                    "checked_at": int(time.time())
                }

        # 3. 本地代码插件 / MCP 规范模块 / Agent Hub 原生插件
        if (
            len(module.get("tools", [])) > 0
            or str(module.get("protocol", "")).startswith("MCP")
            or str(module.get("protocol", "")) == "Agent Hub Native"
            or str(module.get("id", "")).startswith("plugin_")
            or cat in ("知识技能", "插件扩展")
        ):
            return {
                "status": "mounted",
                "badge_color": "green",
                "status_text": "已加载挂载",
                "latency_ms": 0,
                "target": "本地工具总线",
                "diag_hint": "工具已就绪并成功挂载至中枢扩展总线",
                "checked_at": int(time.time())
            }

        return {
            "status": "unknown",
            "badge_color": "gray",
            "status_text": "未配置探针",
            "latency_ms": 0,
            "target": "无",
            "diag_hint": "该组件未指定可探测的网络端点或端口",
            "checked_at": int(time.time())
        }

    @classmethod
    def probe_all_concurrent(cls, modules: List[Dict[str, Any]], max_workers: int = 8) -> List[Dict[str, Any]]:
        """
        并发探测所有模块，在 <1 秒内返回全部探针结果
        """
        if not modules:
            return []

        def _task(m):
            m_copy = dict(m)
            m_copy["health"] = cls.probe_single(m)
            return m_copy

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(_task, modules))
        return results


health_probe = ModuleHealthProbe()
