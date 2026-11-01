# -*- coding: utf-8 -*-
"""控制台的「模块级权限」读取。

连接中心 / 模块市场 / 模块探针已经在 2026-10-30 下线（见 docs/CLEANUP_REPORT.md），
这里只保留 /v1 热路径仍在用的模块级默认权限；拓扑扫描、TCP 探针、模块总览
这些只服务于已下线页面的实现已删除。
"""

import json
import os

from .common import MODULES_PERMISSIONS_PATH

DEFAULT_PERMISSIONS = {
    "universal_proxy": {
        "allow_recall_memory": True,
        "allow_ingest_timeline": True,
        "allowed_tools": ["*"],
        "disallowed_tools": [],
    }
}


def get_module_permissions():
    """模块级权限：文件不存在就用默认值。"""
    if os.path.exists(MODULES_PERMISSIONS_PATH):
        try:
            with open(MODULES_PERMISSIONS_PATH, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            pass
    return DEFAULT_PERMISSIONS
