# -*- coding: utf-8 -*-
# ruff: noqa: F401  —— 本文件是聚合转导出层，名字由 server.py 通过 backend.xxx 调用
"""Stellaris Console Backend (Modular Facade)

控制台后端服务聚合模块（门面模式）。各领域逻辑在 console/modules/ 下：
  - common.py: 共享路径、通用环境判定、SQLite 连接工厂
  - system_service.py: 系统概览统计、时间轴事件写入与 Token 记录
  - module_manager.py: 模块级权限（连接中心下线后仅剩这一项）
  - persona_service.py: 人设 Prompt 注入、好感度阶梯与正则清洗
  - model_service.py: 凭证池、模型调度、账本统计
  - compute_service.py: 算力矩阵与网络连通性测试

只导出 server.py 真正用到的名字；死导出会在审计里被当成冗余单独清理。
"""

import os
import sys

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
for _p in (PROJECT_ROOT, CURRENT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from modules.common import get_db_connection  # noqa: E402
from modules.compute_service import (  # noqa: E402
    get_compute_config,
    redact_compute_config,
    save_compute_config,
    test_compute_provider,
)
from modules.model_service import (  # noqa: E402
    get_models_config,
    get_token_analytics,
    get_universal_models_list,
    redact_models_config,
    save_credits,
    save_models_config,
    sync_models_from_upstream,
)
from modules.module_manager import get_module_permissions  # noqa: E402
from modules.persona_service import (  # noqa: E402
    clean_persona_reply,
    get_current_persona,
    get_persona_rules,
    get_preset_personas,
    save_persona,
    save_persona_profile,
    save_persona_rules,
)
from modules.system_service import (  # noqa: E402
    get_system_resources_metrics,
    get_system_summary,
    get_timeline_events,
    ingest_timeline_event,
    record_token_usage_event,
)
