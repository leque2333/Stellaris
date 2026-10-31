# -*- coding: utf-8 -*-
"""部署自检命令行入口（逻辑在 agent_core/deploy_check.py）。

用法：
    python scripts/check_deploy.py
    python scripts/check_deploy.py --port 18895 --container astrbot

退出码：0 全过 / 1 有失败项（警告不算失败）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core import deploy_check  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Stellaris 部署自检")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18895)
    parser.add_argument("--container", default="astrbot")
    args = parser.parse_args()

    checks = deploy_check.run_checks(args.host, args.port, args.container)
    for line in deploy_check.format_lines(checks):
        print(line)
    summary = deploy_check.summarize(checks)
    print(f"\n{summary['total']} 项检查：{summary['ok']} 通过 / "
          f"{summary['warn']} 警告 / {summary['fail']} 失败")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
