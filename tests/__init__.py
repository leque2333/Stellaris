# -*- coding: utf-8 -*-
"""测试端统一身份：所有测试使用假号码，不依赖本地 data/identity.json。"""

import os
import tempfile
from pathlib import Path

os.environ.setdefault("MASTER_QQ", "1000000001")
os.environ.setdefault("MASTER_NAME", "主人")
os.environ.setdefault("MASTER_ALIASES", "master,admin,leque,1000000001")
os.environ.setdefault("BOT_ALIASES", "self,anan,bot,1000000002,3000000001")

# 记忆调参文件写临时目录：测试里的环境变量断言不能被本机保存过的值干扰。
os.environ.setdefault(
    "HUB_MEMORY_SETTINGS_PATH",
    str(Path(tempfile.mkdtemp(prefix="hub_memory_settings_")) / "memory_settings.json"),
)

# -*- coding: utf-8 -*-
