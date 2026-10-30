# -*- coding: utf-8 -*-
"""测试端统一身份：所有测试使用假号码，不依赖本地 data/identity.json。"""

import os

os.environ.setdefault("MASTER_QQ", "1000000001")
os.environ.setdefault("MASTER_NAME", "主人")
os.environ.setdefault("MASTER_ALIASES", "master,admin,leque,1000000001")
os.environ.setdefault("BOT_ALIASES", "self,anan,bot,1000000002,3000000001")

# -*- coding: utf-8 -*-
