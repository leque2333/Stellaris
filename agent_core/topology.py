# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 模组拓扑与父子级层级管理器 (Topology & Mod Manager)
提供树状/脑图模组吸附、协议兼容性语法检测与实时握手验证。
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

    # 基础协议能力对照矩阵
    PROTOCOL_RULES = {
        "hub": {
            "name": "智能体中枢 (Agent Hub Core)",
            "accepts": ["OpenAI /v1", "Model Context Protocol (MCP)", "Agent Hub Native", "Local IPC / Agent Bridge", "OpenAI Function Calling"]
        }
    }

    # 默认层级归属映射 (开箱即用)
    DEFAULT_RELATIONS = {}

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.config_path = os.path.join(base_dir, self.CONFIG_FILE)

    def load_topology(self) -> Dict[str, Any]:
        """读取用户保存的拓扑图连接关系"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"nodes": {}, "positions": {}}

    def save_topology(self, nodes_data: Dict[str, Any], positions: Dict[str, Any] = None) -> bool:
        """保存用户的拓扑图吸附关系与坐标"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        try:
            current = self.load_topology()
            current["nodes"] = nodes_data
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
        saved = self.load_topology().get("nodes", {}).get(module_id)
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
          - Hosts (宿主网关/大模块) -> 下挂 Submods (特化子模组/插件)
          - Global Skills (全局 AI 技能/工具)
          - Independent Tools (未归类/独立组件)
        """
        mod_map = {m["id"]: dict(m) for m in modules}
        topo = self.load_topology().get("nodes", {})

        # 为每个模块注入层级元数据
        for mid, m in mod_map.items():
            rel = topo.get(mid) or self.DEFAULT_RELATIONS.get(mid, {
                "parent_id": "hub" if m.get("category") in ("系统核心", "认知记忆", "知识技能") else "unassigned",
                "type": "host" if m.get("category") == "自定义组件" else "skill",
                "protocol": m.get("protocol", "OpenAI Function Calling")
            })
            m["parent_id"] = rel.get("parent_id", "hub")
            m["mod_type"] = rel.get("type", "skill")
            m["submods"] = []

        hub_node = {
            "id": "hub",
            "name": "助手智能体中枢 (Agent Hub Core)",
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
            "hosts": hosts,
            "global_skills": global_skills,
            "unassigned": unassigned,
            "total_modules": len(modules)
        }

    def validate_handshake(self, modules: List[Dict[str, Any]], custom_relations: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        语法检测与握手排错：
        校验模块之间的吸附拓扑关系、协议握手兼容性以及端点连通状态。
        """
        mod_map = {m["id"]: m for m in modules}
        relations = custom_relations or self.load_topology().get("nodes", {})
        
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
                    "severity": "info",
                    "module_id": mid,
                    "module_name": child_name,
                    "title": "未分配挂载点",
                    "detail": f"组件「{child_name}」尚未吸附到任何宿主大模块或中枢。",
                    "suggestion": "可将其拖动吸附至「助手中枢」作为全局技能，或吸附至对应的宿主大模块。"
                })
                continue

            total_links += 1

            # 校验目标父节点是否存在
            if pid != "hub" and pid not in mod_map:
                issues.append({
                    "severity": "error",
                    "module_id": mid,
                    "module_name": child_name,
                    "title": "父级宿主缺失",
                    "detail": f"组件「{child_name}」指定吸附的宿主 [{pid}] 不存在或未安装。",
                    "suggestion": "请重新连接至已在线的宿主模块或中枢。"
                })
                connections.append({
                    "source": mid,
                    "target": pid,
                    "status": "broken",
                    "protocol": child_proto
                })
                continue

            # 获取父节点协议接纳规则
            parent_rule = self.PROTOCOL_RULES.get(pid, {
                "name": mod_map.get(pid, {}).get("name", pid),
                "accepts": ["OpenAI Function Calling", "OpenAI /v1", "Agent Hub Native"]
            })
            parent_name = "智能体中枢" if pid == "hub" else mod_map[pid].get("name", pid)
            accepted_protos = parent_rule.get("accepts", [])

            # 协议兼容性检测
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
