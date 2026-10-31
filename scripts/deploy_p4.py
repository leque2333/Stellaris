# -*- coding: utf-8 -*-
"""部署脚本：把开发目录的 P4 组件同步到生产目录与 AstrBot。

用法（在任意目录）：
    python scripts/deploy_p4.py            # 只同步，不重启
    python scripts/deploy_p4.py --restart  # 同步后重启中枢与 AstrBot

同步内容：
    1. console/server.py            → D:\\Stellaris_test\\console\\
    2. console/mcp_hub_tools.py     → 生产 console\\ 与 AstrBot 挂载卷
    3. agent_core/tool_shelves/*    → 生产 agent_core\\
    4. astrbot_plugin_hub_tool_executor/* → AstrBot 插件目录
    5. data/tool_shelves.json       → 生产 data\\

刻意不覆盖：data/clients.json、data/models_config.json、compute_providers.json（生产敏感配置）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(r"D:\Stellaris")
DST = Path(r"D:\Stellaris_test")
ASTRBOT_DATA = DST / "astrbot" / "data"

FILES = [
    ("console/server.py", "console/server.py"),
    ("console/mcp_hub_tools.py", "console/mcp_hub_tools.py"),
    ("console/mcp_hub_tools.py", "astrbot/data/mcp_hub_tools.py"),
    ("data/tool_shelves.json", "data/tool_shelves.json"),
    ("tests/test_tool_shelves.py", "tests/test_tool_shelves.py"),
    ("docs/TOOL_SHELVES.md", "docs/TOOL_SHELVES.md"),
    ("docs/HANDOFF.md", "docs/HANDOFF.md"),
]

PKG_DIRS = [
    ("agent_core/tool_shelves", "agent_core/tool_shelves"),
    ("astrbot_plugins/astrbot_plugin_hub_tool_executor",
     "astrbot/data/plugins/astrbot_plugin_hub_tool_executor"),
]


def copy_file(rel_src: str, rel_dst: str) -> None:
    src = SRC / rel_src
    dst = DST / rel_dst
    if not src.exists():
        print(f"  [跳过] 源不存在: {src}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  [OK] {rel_dst}")


def copy_dir(rel_src: str, rel_dst: str) -> None:
    src = SRC / rel_src
    dst = DST / rel_dst
    if not src.is_dir():
        print(f"  [跳过] 源目录不存在: {src}")
        return
    # 目标目录只删除文件不删目录，避免 CUDA/句柄占用导致整目录删除失败
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == "__pycache__":
            continue
        target = dst / item.name
        if item.is_file():
            shutil.copy2(item, target)
        elif item.is_dir():
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(item, target)
    print(f"  [OK] {rel_dst}/  ({len(list(dst.iterdir()))} 项)")


def verify() -> int:
    """关键文件 hash 一致性 + 包文件数，返回失败项数。"""
    import hashlib

    bad = 0
    for rel_src, rel_dst in FILES:
        src, dst = SRC / rel_src, DST / rel_dst
        if not src.exists():
            continue
        if not dst.exists():
            print(f"  [缺失] {rel_dst}")
            bad += 1
            continue
        a = hashlib.sha256(src.read_bytes()).hexdigest()
        b = hashlib.sha256(dst.read_bytes()).hexdigest()
        if a != b:
            print(f"  [不一致] {rel_dst}")
            bad += 1
    pkg = DST / "agent_core" / "tool_shelves"
    count = len([p for p in pkg.iterdir() if p.is_file()]) if pkg.exists() else 0
    print(f"  tool_shelves 包文件数: {count}（应为 10）")
    if count != 10:
        bad += 1
    return bad


def restart() -> None:
    print("\n[重启] 中枢 18895 ...")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
        "Where-Object { $_.CommandLine -like '*server.py 18895*' } | "
        "Select-Object -First 1"
    )
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                         capture_output=True, text=True).stdout
    pid = None
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pid = int(line)
    if pid:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Stop-Process -Id {pid} -Force"], check=False)
        import time
        time.sleep(2)
    py = r"C:\Users\user\AppData\Local\Python\bin\python.exe"
    subprocess.Popen(
        [py, "console\\server.py", "18895"],
        cwd=str(DST),
        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        stdout=open(DST / "logs" / "hub-18895.out.log", "a", encoding="utf-8"),
        stderr=open(DST / "logs" / "hub-18895.err.log", "a", encoding="utf-8"),
    )
    print("  已启动，等待 5 秒 ...")
    import time
    time.sleep(5)

    print("[重启] AstrBot 容器 ...")
    subprocess.run(["docker", "restart", "astrbot"], capture_output=True, text=True)
    print("  已重启，等待 40 秒让 MCP 与插件加载 ...")
    time.sleep(40)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--restart", action="store_true", help="同步后重启中枢与 AstrBot")
    args = parser.parse_args()

    print(f"[1/3] 同步文件 {SRC} → {DST}")
    for rel_src, rel_dst in FILES:
        copy_file(rel_src, rel_dst)

    print("[2/3] 同步目录")
    for rel_src, rel_dst in PKG_DIRS:
        copy_dir(rel_src, rel_dst)

    print("[3/3] 校验")
    bad = verify()

    if args.restart:
        restart()

    if bad:
        print(f"\n部署完成，但有 {bad} 项校验未通过，请检查上面的 [不一致]/[缺失]。")
        return 1
    print("\n部署完成，校验全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
