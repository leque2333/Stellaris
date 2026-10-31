# -*- coding: utf-8 -*-
"""身份线索扫描：把所有插件里的 UID 罗列出来。

只罗列，不判断。人看完列表后自己决定哪个 UID 是谁。

用法：
    python scripts/scan_identities.py                 # 表格
    python scripts/scan_identities.py --json          # JSON
    python scripts/scan_identities.py --out ids.json  # 写文件
    python scripts/scan_identities.py --roots "D:\\AstrBot\\data;D:\\plugin"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.identity_scan import scan  # noqa: E402


def _open_store(db_path: str = ""):
    """打开指定库；不指定时沿用中枢的默认库。"""
    import os

    try:
        from agent_core.storage.sqlite_store import SQLiteStore

        path = db_path or os.getenv("MEMORY_HUB_DB_PATH", "").strip()
        path = path or str(ROOT / "data" / "memory.db")
        return SQLiteStore(Path(path)), path
    except Exception as exc:
        print(f"! 打开数据库失败（已配置状态会不准）: {exc}")
        return None, ""


def print_table(result: dict) -> None:
    hits = result.get("hits") or []
    print(f"扫描根：{', '.join(result.get('roots') or [])}")
    if result.get("db"):
        print(f"已配置状态来自：{result['db']}")
    print(f"文件 {result.get('scanned_files', 0)} 个 / 线索 {result.get('total_sources', 0)} 条 "
          f"/ 唯一 UID {result.get('total_uids', 0)} 个 "
          f"（已配置 {result.get('configured', 0)}，待配置 {result.get('unconfigured', 0)}）")
    print()
    if not hits:
        print("没有扫到任何 UID。")
        return

    print(f"{'UID':<14}{'平台':<10}{'来源':<6}{'状态':<6}{'档案':<14}{'最先出现的来源'}")
    print("-" * 118)
    for hit in hits:
        status = "已配" if hit["configured"] else "待配"
        archive = hit["archive_id"] if hit["configured"] else "-"
        src = (hit["sources"] or [{}])[0]
        plugin = src.get("plugin") or "(根目录)"
        origin = f"{plugin} / {Path(src.get('path') or '').name} / {src.get('field')}"
        print(f"{hit['uid']:<14}{hit['platform_hint']:<10}{hit['source_count']:<6}{status:<6}{archive:<14}{origin[:62]}")

    unconfigured = [h["uid"] for h in hits if not h["configured"]]
    if unconfigured:
        print()
        print(f"待配置 {len(unconfigured)} 个：{', '.join(unconfigured)}")
        print("去控制台「档案 → 身份」里把这些 UID 填到对应档案即可。")

    print()
    print("提示：平台只是从插件名/路径猜的，不是判断结果；以来源字段为准。")


def main() -> int:
    parser = argparse.ArgumentParser(description="罗列所有插件里的 UID")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--out", default="", help="把 JSON 写到文件")
    parser.add_argument("--roots", default="", help="扫描目录，多个用分号分隔")
    parser.add_argument("--db", default="", help="中枢数据库（默认 data/memory.db）")
    args = parser.parse_args()

    roots = None
    if args.roots:
        roots = [Path(p.strip()) for p in args.roots.split(";") if p.strip()]

    store, db_path = _open_store(args.db)
    result = scan(roots=roots, store=store)
    result["db"] = db_path
    if store is None or not (result.get("configured") or 0):
        result["db_note"] = "该库里没有已配置的档案/绑定，已配置状态仅供参考"

    if args.out:
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已写入 {args.out}（{result.get('total_uids', 0)} 个 UID）")
        return 0

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print_table(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
