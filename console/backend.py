# -*- coding: utf-8 -*-
"""
Stellaris Console Backend (Modular Facade)
控制台后端服务聚合模块（门面模式）
各个专业领域的底层逻辑已彻底解耦拆分至 console/modules/：
  - common.py: 共享路径、通用环境判定、SQLite 连接工厂
  - system_service.py: 系统概览统计、自传时间轴事件写入与 Token 记录
  - module_manager.py: 树状拓扑、自定义组件管理、网络探针与 RBAC 权限
  - persona_service.py: 人设 Prompt 注入、多级好感度阶梯与正则过滤引擎
  - model_service.py: 凭证池、大模型调度、服务商智能嗅探、上游模型同步
  - compute_service.py: 算力矩阵管理与网络连通性测试
本文件统一聚合导出所有函数，保持与历史路由及上游调用的 100% 兼容。
"""

import os
import sys

# 动态确保当前 console 目录及项目根目录在 sys.path 中
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
for _p in (PROJECT_ROOT, CURRENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from modules.system_service import (
        ingest_timeline_event,
        record_token_usage_event,
        get_timeline_events,
        get_system_summary,
        get_system_resources_metrics
    )
    from modules.common import (
        resolve_hub_db_path,
        resolve_custom_tools_path,
        resolve_astrbot_plugins_dir,
        resolve_astrbot_mcp_path,
        resolve_astrbot_cmd_config_path,
        resolve_astrbot_v4_db_path,
        get_db_connection,
        sanitize_clean_text
    )
    from modules.module_manager import (
        get_module_permissions,
        save_module_permissions,
        get_module_states,
        toggle_module_active,
        get_custom_module_configs,
        update_module_custom_category,
        import_custom_module,
        delete_custom_module,
        probe_single_module,
        get_topology_overview,
        save_topology_overview,
        validate_topology_overview,
        probe_all_modules,
        get_modules_overview
    )
    from modules.persona_service import (
        clean_persona_reply,
        get_current_persona,
        save_persona,
        save_persona_profile,
        get_preset_personas,
        get_persona_rules,
        save_persona_rules
    )
    from modules.model_service import (
        sniff_provider_from_key,
        handle_sniff_key,
        get_model_reasoning_options,
        check_model_reasoning_support,
        build_upstream_network_opener,
        sync_models_from_upstream,
        get_preset_models,
        get_models_config,
        save_models_config,
        save_credits,
        get_model_pricing_map,
        get_token_analytics,
        get_universal_models_list
    )
    from modules.compute_service import (
        get_compute_config,
        save_compute_config,
        test_compute_provider
    )
except ImportError:
    from console.modules.system_service import (
        ingest_timeline_event,
        record_token_usage_event,
        get_timeline_events,
        get_system_summary,
        get_system_resources_metrics
    )
    from console.modules.common import (
        resolve_hub_db_path,
        resolve_custom_tools_path,
        resolve_astrbot_plugins_dir,
        resolve_astrbot_mcp_path,
        resolve_astrbot_cmd_config_path,
        resolve_astrbot_v4_db_path,
        get_db_connection,
        sanitize_clean_text
    )
    from console.modules.module_manager import (
        get_module_permissions,
        save_module_permissions,
        get_module_states,
        toggle_module_active,
        get_custom_module_configs,
        update_module_custom_category,
        import_custom_module,
        delete_custom_module,
        probe_single_module,
        get_topology_overview,
        save_topology_overview,
        validate_topology_overview,
        probe_all_modules,
        get_modules_overview
    )
    from console.modules.persona_service import (
        clean_persona_reply,
        get_current_persona,
        save_persona,
        save_persona_profile,
        get_preset_personas,
        get_persona_rules,
        save_persona_rules
    )
    from console.modules.model_service import (
        sniff_provider_from_key,
        handle_sniff_key,
        get_model_reasoning_options,
        check_model_reasoning_support,
        build_upstream_network_opener,
        sync_models_from_upstream,
        get_preset_models,
        get_models_config,
        save_models_config,
        save_credits,
        get_model_pricing_map,
        get_token_analytics,
        get_universal_models_list
    )
    from console.modules.compute_service import (
        get_compute_config,
        save_compute_config,
        test_compute_provider
    )

