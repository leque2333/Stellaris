# -*- coding: utf-8 -*-
"""
Stellaris Core - 模组拓扑与父子级层级管理器 (Topology & Mod Manager)
提供类似游戏模组管理器 (Mod Organizer) 的树状/脑图模组吸附、协议兼容性语法检测与实时握手验证。
"""

import os
import json
import time
from typing import Dict, Any, List, Optional, Tuple

class TopologyManager:
    """
    智能体模组拓扑管理器：支持文件夹树状分级与思维导图/脑图吸附模式
    """
    CONFIG_FILE = "data/topology_config.json"

    # 协议能力对照矩阵
    PROTOCOL_RULES = {
        "hub": {
            "name": "Stellaris Core",
            "accepts": ["OpenAI /v1", "Model Context Protocol (MCP)", "Stellaris Native", "Local IPC / Agent Bridge", "OpenAI Function Calling", "Native"]
        },
        "astrbot": {
            "name": "AstrBot 宿主服务",
            "accepts": ["OneBot v11", "OneBot v11 WebUI", "AstrBot Plugin", "Stellaris Core Lifecycle", "OpenAI Function Calling", "Model Context Protocol (MCP)", "Native"]
        },
        "napcat": {
            "name": "NapCat QQ 协议端",
            "accepts": ["OneBot v11", "OneBot v11 WebSocket", "OneBot v11 HTTP", "Native"]
        },
        "nonebot": {
            "name": "NoneBot 宿主服务",
            "accepts": ["OneBot v11", "NoneBot Plugin", "OpenAI Function Calling", "Native"]
        },
        "control_host_pc": {
            "name": "宿主机 Antigravity 控制桥梁",
            "accepts": ["Local IPC / Agent Bridge", "OpenAI Function Calling", "Native"]
        },
        "host_pc": {
            "name": "宿主机 Antigravity 控制桥梁",
            "accepts": ["Local IPC / Agent Bridge", "OpenAI Function Calling", "Native"]
        }
    }

    # 默认层级归属映射 (开箱即用)
    DEFAULT_RELATIONS = {
        "astrbot": {"parent_id": "hub", "type": "host", "protocol": "OpenAI /v1"},
        "napcat": {"parent_id": "astrbot", "type": "host", "protocol": "OneBot v11"},
        "nonebot": {"parent_id": "hub", "type": "host", "protocol": "OpenAI /v1"},
        "control_host_pc": {"parent_id": "hub", "type": "host", "protocol": "Local IPC / Agent Bridge"},
        "host_pc": {"parent_id": "hub", "type": "host", "protocol": "Local IPC / Agent Bridge"},
        
        # 专属子模组 / 管道中间件 (归属大模块宿主，不污染主大模型上下文)
        "astrbot_plugin_turnflow": {"parent_id": "astrbot", "type": "submod", "protocol": "Stellaris Core Lifecycle"},
        "astrbot_plugin_guard": {"parent_id": "astrbot", "type": "submod", "protocol": "AstrBot Plugin"},
        "astrbot_plugin_human_patrol": {"parent_id": "astrbot", "type": "submod", "protocol": "AstrBot Plugin"},
        "astrbot_plugin_anan_bridge": {"parent_id": "astrbot", "type": "submod", "protocol": "AstrBot Plugin"},
        "astrbot_plugin_system_probe": {"parent_id": "astrbot", "type": "submod", "protocol": "AstrBot Plugin"},
        
        # 全局 AI 技能与核心记忆 (归属中枢，供大模型直接推理与调用)
        "astrbot_plugin_memory": {"parent_id": "hub", "type": "skill", "protocol": "OpenAI Function Calling"},
        "astrbot_plugin_bilibili": {"parent_id": "hub", "type": "skill", "protocol": "Stellaris Native"},
        "astrbot_plugin_mrfz": {"parent_id": "hub", "type": "skill", "protocol": "OpenAI Function Calling"},
        "astrbot_plugin_limbus": {"parent_id": "hub", "type": "skill", "protocol": "OpenAI Function Calling"},
        "mcp_image_generator": {"parent_id": "hub", "type": "skill", "protocol": "Model Context Protocol (MCP)"},
        "mcp_prts_voice": {"parent_id": "hub", "type": "skill", "protocol": "Model Context Protocol (MCP)"},
        "mcp_limbus_db": {"parent_id": "hub", "type": "skill", "protocol": "Model Context Protocol (MCP)"},
        "mcp_bilibili_summary": {"parent_id": "hub", "type": "skill", "protocol": "Model Context Protocol (MCP)"},
        "tool_system_monitor": {"parent_id": "hub", "type": "skill", "protocol": "Stellaris Native"},
        "tool_memory_archive": {"parent_id": "hub", "type": "skill", "protocol": "Stellaris Native"}
    }

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.config_path = os.path.join(base_dir, self.CONFIG_FILE)

    @staticmethod
    def _normalize_nodes(raw: Any) -> Dict[str, Any]:
        """将无论为 list 还是 dict 的 nodes 数据结构安全归一化为 { id: item } 的字典"""
        if isinstance(raw, list):
            res = {}
            for item in raw:
                if isinstance(item, dict) and "id" in item:
                    res[item["id"]] = item
            return res
        elif isinstance(raw, dict):
            return dict(raw)
        return {}

    def load_topology(self) -> Dict[str, Any]:
        """读取用户保存的拓扑图连接关系"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
        return {"nodes": {}, "positions": {}}

    def save_topology(self, nodes_data: Any, positions: Dict[str, Any] = None) -> bool:
        """保存用户的拓扑图吸附关系与坐标"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        try:
            current = self.load_topology()
            current["nodes"] = self._normalize_nodes(nodes_data)
            if positions:
                current["positions"] = positions
            current["updated_at"] = int(time.time())
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def get_effective_relation(self, module_id: str) -> Dict[str, Any]:
        """获取单个模块的实际父级与协议类型"""
        topo = self._normalize_nodes(self.load_topology().get("nodes", {}))
        saved = topo.get(module_id)
        if saved and "parent_id" in saved:
            return saved
        return self.DEFAULT_RELATIONS.get(module_id, {
            "parent_id": "hub",
            "type": "skill",
            "protocol": "OpenAI Function Calling"
        })

    def build_tree_structure(self, modules: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        构建多层级文件夹树状结构
        - Hub (中枢核心)
          - Hosts (宿主网关/大模块) -> 下挂 Submods (特化子模组/中间件)
          - Global Skills (全局 AI 技能/工具)
          - Independent Tools (未归类/独立组件)
        """
        mod_map = {m["id"]: dict(m) for m in modules}
        topo = self._normalize_nodes(self.load_topology().get("nodes", {}))

        # 为每个模块注入层级元数据
        for mid, m in mod_map.items():
            rel = topo.get(mid) or self.DEFAULT_RELATIONS.get(mid, {
                "parent_id": "hub" if m.get("category") in ("系统核心", "认知记忆", "知识技能") else "unassigned",
                "type": "host" if m.get("category") == "外部宿主" else "skill",
                "protocol": m.get("protocol", "OpenAI Function Calling")
            })
            m["parent_id"] = rel.get("parent_id", "hub")
            m["mod_type"] = rel.get("type", "skill")
            m["submods"] = []

        hub_node = {
            "id": "hub",
            "name": "Stellaris Core",
            "type": "hub",
            "category": "系统核心",
            "protocol": "Core Matrix Bus",
            "health": {"status": "online", "status_text": "中枢运行中", "latency_ms": 0},
            "desc": "核心智能大脑，承载双池算力调度、3D记忆引擎、人设过滤与全局工具总线。"
        }

        hosts = []
        global_skills = []
        unassigned = []

        for mid, m in mod_map.items():
            pid = m.get("parent_id")
            m_type = m.get("mod_type")

            if pid == "hub" and m_type == "host":
                hosts.append(m)
            elif pid in mod_map:
                # 挂载在某个宿主模块下
                mod_map[pid].setdefault("submods", []).append(m)
            elif m_type == "skill" or pid == "hub":
                global_skills.append(m)
            else:
                unassigned.append(m)

        for h in hosts:
            if "submods" not in h:
                h["submods"] = []

        return {
            "hub": hub_node,
            "core_hub": hub_node,
            "hosts": hosts,
            "global_skills": global_skills,
            "unassigned": unassigned,
            "total_modules": len(modules)
        }

    def validate_handshake(self, modules: List[Dict[str, Any]], custom_relations: Any = None) -> Dict[str, Any]:
        """
        语法检测与握手排错：
        校验模块之间的吸附拓扑关系、协议握手兼容性以及端点连通状态。
        """
        mod_map = {m["id"]: m for m in modules}
        relations = self._normalize_nodes(custom_relations) if custom_relations is not None else self._normalize_nodes(self.load_topology().get("nodes", {}))

        # 补齐默认关系
        effective = {}
        for mid, m in mod_map.items():
            effective[mid] = relations.get(mid) or self.DEFAULT_RELATIONS.get(mid, {
                "parent_id": "hub",
                "type": "skill",
                "protocol": m.get("protocol", "OpenAI Function Calling")
            })

        issues = []
        connections = []
        compatible_links = 0
        total_links = 0

        for mid, m in mod_map.items():
            rel = effective.get(mid, {})
            pid = rel.get("parent_id", "hub")
            child_proto = rel.get("protocol") or m.get("protocol", "")
            child_name = m.get("name", mid)

            if pid == "unassigned":
                issues.append({
                    "severity": "warning",
                    "module_id": mid,
                    "module_name": child_name,
                    "title": "未挂载宿主 (Unassigned)",
                    "detail": f"组件「{child_name}」当前处于离散游离状态，未吸附至任何宿主或中枢。",
                    "suggestion": "可拖动该模块吸附到 AstrBot 或中枢以激活其生命周期。"
                })
                continue

            total_links += 1

            # 校验父级是否存在
            parent_name = "中央中枢"
            if pid != "hub":
                if pid not in mod_map:
                    issues.append({
                        "severity": "error",
                        "module_id": mid,
                        "module_name": child_name,
                        "title": "父级宿主缺失 (Missing Parent)",
                        "detail": f"组件「{child_name}」吸附的宿主 [{pid}] 在系统中不存在或已被移除。",
                        "suggestion": f"请重新将「{child_name}」吸附到现有的宿主大模块。"
                    })
                    connections.append({
                        "source": mid,
                        "target": pid,
                        "status": "parent_missing",
                        "protocol": child_proto
                    })
                    continue
                parent_name = mod_map[pid].get("name", pid)

            # 协议匹配语法检测
            accepted_protos = self.PROTOCOL_RULES.get(pid, {}).get("accepts", [])
            # 兼容性匹配：子协议是否被父节点协议池覆盖
            is_compatible = False
            for accepted in accepted_protos:
                if accepted.lower() in child_proto.lower() or child_proto.lower() in accepted.lower():
                    is_compatible = True
                    break

            if not is_compatible:
                issues.append({
                    "severity": "error",
                    "module_id": mid,
                    "module_name": child_name,
                    "title": "协议不匹配 (Protocol Mismatch)",
                    "detail": f"组件「{child_name}」采用协议 [{child_proto}]，但宿主「{parent_name}」仅支持 [{', '.join(accepted_protos)}]。",
                    "suggestion": f"请检查组件接口或将其挂载到支持 {child_proto} 的宿主模块上。"
                })
                connections.append({
                    "source": mid,
                    "target": pid,
                    "status": "protocol_mismatch",
                    "protocol": child_proto
                })
            else:
                compatible_links += 1
                # 检查双方运行健康状态 (存活握手)
                child_health = m.get("health", {}).get("status", "unknown")
                parent_health = "online" if pid == "hub" else mod_map[pid].get("health", {}).get("status", "unknown")
                
                handshake_status = "handshake_ok"
                if child_health == "offline" or parent_health == "offline":
                    handshake_status = "peer_offline"
                    issues.append({
                        "severity": "warning",
                        "module_id": mid,
                        "module_name": child_name,
                        "title": "握手端点离线",
                        "detail": f"组件「{child_name}」与宿主「{parent_name}」协议兼容，但其中一方处于未启动状态。",
                        "suggestion": "请启动对应容器或本地后台服务以完成网络握手。"
                    })

                connections.append({
                    "source": mid,
                    "target": pid,
                    "status": handshake_status,
                    "protocol": child_proto
                })

        score = int((compatible_links / max(1, total_links)) * 100) if total_links > 0 else 100

        return {
            "is_valid": len([i for i in issues if i["severity"] == "error"]) == 0,
            "health_score": score,
            "total_links": total_links,
            "compatible_links": compatible_links,
            "issues": issues,
            "connections": connections,
            "checked_at": int(time.time())
        }
