# -*- coding: utf-8 -*-
"""合并旧的 QQ 号画像到档案 ID。

背景：事实和好感度以前用 QQ 号当 user_id，后来改成 archive_id，
导致同一份数据存了两套（`<你的QQ>` 和 `arc_master`）。

做的事（全部可回溯）：
1. 旧 QQ 号下的事实 → 标记 superseded（不删除，保留痕迹）
2. 旧 QQ 号的 profile → 合并好感度（取较高值）后删除该行

用法：
    python scripts/merge_legacy_identities.py --db <memory.db>          # 只看不动
    python scripts/merge_legacy_identities.py --db <memory.db> --apply  # 真正执行
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def find_legacy_targets(conn: sqlite3.Connection) -> list:
    """找出"有档案、但 facts/profile 还挂在旧 ID 上"的映射。"""
    archives = {
        row["archive_id"]
        for row in conn.execute("SELECT archive_id FROM archives")
    }
    targets = []
    for row in conn.execute(
        "SELECT DISTINCT user_id FROM user_facts "
        "WHERE user_id NOT IN (SELECT archive_id FROM archives) AND status = 'active'"
    ):
        legacy_id = row["user_id"]
        # 旧 ID 可能通过 archive_identities 指向某个档案
        mapped = conn.execute(
            "SELECT archive_id FROM archive_identities WHERE platform_user_id = ? LIMIT 1",
            (legacy_id,),
        ).fetchone()
        if mapped and mapped["archive_id"] in archives:
            targets.append((legacy_id, mapped["archive_id"]))
            continue
        # 或者旧 ID 本身就是 master 的 QQ 号
        try:
            from agent_core.identity import IdentityResolver

            if str(legacy_id) == str(IdentityResolver.get_master_qq()):
                master = conn.execute(
                    "SELECT archive_id FROM archives WHERE role = 'admin' "
                    "ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if master:
                    targets.append((legacy_id, master["archive_id"]))
        except Exception:
            pass
    return sorted(set(targets))


def merge(db_path: Path, apply: bool = False) -> dict:
    report = {"db": str(db_path), "applied": apply, "pairs": [], "facts_moved": 0, "profiles_merged": 0}

    if apply:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = db_path.with_name(f"{db_path.stem}.bak_merge_{stamp}{db_path.suffix}")
        shutil.copy2(db_path, backup)
        report["backup"] = str(backup)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        pairs = find_legacy_targets(conn)
        report["pairs"] = [{"legacy": a, "archive": b} for a, b in pairs]
        now = time.time()

        for legacy_id, archive_id in pairs:
            # 1) 事实：目标档案已有同名内容 → 旧的标 superseded
            legacy_facts = conn.execute(
                "SELECT fact_id, content FROM user_facts "
                "WHERE user_id = ? AND status = 'active'",
                (legacy_id,),
            ).fetchall()
            target_contents = {
                row["content"]
                for row in conn.execute(
                    "SELECT content FROM user_facts WHERE user_id = ? AND status = 'active'",
                    (archive_id,),
                )
            }
            for fact in legacy_facts:
                if fact["content"] in target_contents:
                    if apply:
                        conn.execute(
                            "UPDATE user_facts SET status = 'superseded', updated_at = ? "
                            "WHERE fact_id = ?",
                            (now, fact["fact_id"]),
                        )
                    report["facts_moved"] += 1
                else:
                    # 目标档案没有的，迁过去保留
                    if apply:
                        conn.execute(
                            "UPDATE user_facts SET user_id = ?, updated_at = ? WHERE fact_id = ?",
                            (archive_id, now, fact["fact_id"]),
                        )
                    report["facts_moved"] += 1

            # 2) profile：好感度取较高值，历史保留较长的一份，然后删旧行
            legacy_profile = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (legacy_id,)
            ).fetchone()
            target_profile = conn.execute(
                "SELECT * FROM user_profiles WHERE user_id = ?", (archive_id,)
            ).fetchone()
            if legacy_profile and target_profile:
                legacy_aff = int(legacy_profile["affection"] or 0)
                target_aff = int(target_profile["affection"] or 0)
                if apply:
                    conn.execute(
                        "UPDATE user_profiles SET affection = ?, updated_at = ? WHERE user_id = ?",
                        (max(legacy_aff, target_aff), now, archive_id),
                    )
                    conn.execute("DELETE FROM user_profiles WHERE user_id = ?", (legacy_id,))
                report["profiles_merged"] += 1
            elif legacy_profile and not target_profile:
                if apply:
                    conn.execute(
                        "UPDATE user_profiles SET user_id = ? WHERE user_id = ?",
                        (archive_id, legacy_id),
                    )
                report["profiles_merged"] += 1

        if apply:
            conn.commit()
    finally:
        conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="合并旧 QQ 号画像到档案")
    parser.add_argument("--db", default=str(ROOT / "data" / "memory.db"))
    parser.add_argument("--apply", action="store_true", help="真正执行（默认只读预览）")
    args = parser.parse_args()

    db = Path(args.db)
    if not db.is_file():
        print(f"数据库不存在：{db}")
        return 1

    report = merge(db, apply=args.apply)
    print(f"数据库：{report['db']}")
    print(f"模式：{'执行' if report['applied'] else '预览（加 --apply 执行）'}")
    if report.get("backup"):
        print(f"备份：{report['backup']}")
    for pair in report["pairs"]:
        print(f"  {pair['legacy']}  →  {pair['archive']}")
    print(f"事实处理：{report['facts_moved']} 条")
    print(f"画像合并：{report['profiles_merged']} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
