# -*- coding: utf-8 -*-
"""测试端统一身份：所有测试使用假号码，不依赖本地 data/identity.json。"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MASTER_QQ", "1000000001")
os.environ.setdefault("MASTER_NAME", "主人")
# 不加 admin / leque：生产默认已经没有这些通用词别名，
# 测试里再加回来会掩盖“通用词认主”的回归（复核报告 §4.8）
os.environ.setdefault("MASTER_ALIASES", "master,1000000001")
os.environ.setdefault("BOT_ALIASES", "self,anan,bot,1000000002,3000000001")

# 记忆调参文件写临时目录：测试里的环境变量断言不能被本机保存过的值干扰。
os.environ.setdefault(
    "HUB_MEMORY_SETTINGS_PATH",
    str(Path(tempfile.mkdtemp(prefix="hub_memory_settings_")) / "memory_settings.json"),
)

# 鉴权配置也指到临时目录：测试会 `import console.server`，
# 绝不能让测试读写开发机上的真实口令文件。
os.environ.setdefault(
    "HUB_AUTH_CONFIG_PATH",
    str(Path(tempfile.mkdtemp(prefix="hub_auth_cfg_")) / "auth_config.json"),
)

# -*- coding: utf-8 -*-
