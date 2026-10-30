# -*- coding: utf-8 -*-
"""B 站独立模块入口。

用法（在项目根目录执行）：
  python -m modules.bilibili.main status
  python -m modules.bilibili.main once --dry-run
  python -m modules.bilibili.main run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.bilibili.actions import ActionServer  # noqa: E402
from modules.bilibili.bili_api import BiliClient, BiliError  # noqa: E402
from modules.bilibili.hub_client import HubClient  # noqa: E402
from modules.bilibili.login import qr_login  # noqa: E402
from modules.bilibili.manifest import write_manifest  # noqa: E402
from modules.bilibili.patrol import Patrol  # noqa: E402
from modules.bilibili.state import StateStore  # noqa: E402


def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        example = Path(__file__).parent / "config.example.json"
        raise SystemExit(
            f"找不到配置：{path}\n"
            f"先复制一份：copy \"{example}\" \"{path}\"，再填 B 站 Cookie。"
        )
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    bcfg = config.setdefault("bilibili", {})
    for key, env in (
        ("sessdata", "BILI_SESSDATA"),
        ("bili_jct", "BILI_JCT"),
        ("buvid3", "BILI_BUVID3"),
        ("dedeuserid", "BILI_DEDEUSERID"),
    ):
        if os.environ.get(env):
            bcfg[key] = os.environ[env]
    hub_cfg = config.setdefault("hub", {})
    if os.environ.get("HUB_BASE_URL"):
        hub_cfg["base_url"] = os.environ["HUB_BASE_URL"]
    if os.environ.get("HUB_API_KEY"):
        hub_cfg["api_key"] = os.environ["HUB_API_KEY"]
    if os.environ.get("HUB_MODEL"):
        hub_cfg["model"] = os.environ["HUB_MODEL"]
    return config


def build(config: Dict[str, Any]) -> Patrol:
    bcfg = config.get("bilibili") or {}
    hcfg = config.get("hub") or {}
    bili = BiliClient(
        sessdata=bcfg.get("sessdata", ""),
        bili_jct=bcfg.get("bili_jct", ""),
        buvid3=bcfg.get("buvid3", ""),
        dedeuserid=bcfg.get("dedeuserid", ""),
        proxy=bcfg.get("proxy", ""),
        timeout=int(bcfg.get("timeout", 20)),
    )
    hub = HubClient(
        base_url=hcfg.get("base_url", "http://127.0.0.1:18895/v1"),
        api_key=hcfg.get("api_key", ""),
        model=hcfg.get("model", "gemini-3.8-flash"),
        host_id=hcfg.get("host_id", "bilibili"),
        host_name=hcfg.get("host_name", "B站"),
        host_uid=str(bcfg.get("bot_uid") or bcfg.get("dedeuserid") or ""),
        timeout=int(hcfg.get("timeout", 120)),
    )
    state_path = config.get("state_path") or str(Path(__file__).parent / "data" / "state.json")
    state = StateStore(state_path)
    return Patrol(config, bili, hub, state)


def cmd_status(patrol: Patrol) -> int:
    print("== B 站模块状态 ==")
    try:
        nav = patrol.bili.nav()
    except BiliError as exc:
        print(f"登录检查失败：{exc}")
        return 1
    print(f"登录：{'是' if nav['is_login'] else '否'}")
    print(f"UID：{nav['mid']}  昵称：{nav['uname']}  等级：{nav['level']}")
    session_key, active = patrol.active_session()
    print(f"当前时段：{'活跃' if active else '非活跃'} {session_key}")
    allowed, reason = patrol.quota(session_key or "manual")
    print(f"评论配额：{'可用' if allowed else reason}")
    print(f"追踪评论：{len(patrol.state.tracked_comments())}")
    print(f"已见视频：{len(patrol.state.seen_bvids())}")
    print(f"dry_run：{bool(patrol.bcfg.get('dry_run', True))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B 站独立模块（中枢 /v1 接口）")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "once", "status", "check-login", "login"],
        help="run=循环；once=跑一轮；status=状态；check-login=只查登录；login=扫码登录",
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config.json"),
        help="配置文件路径",
    )
    parser.add_argument("--dry-run", action="store_true", help="只评估不发表")
    parser.add_argument("--live", action="store_true", help="覆盖配置，真正发表（谨慎）")
    parser.add_argument("--login-timeout", type=int, default=600, help="扫码等待秒数")
    args = parser.parse_args(argv)

    config = load_config(Path(args.config))
    write_manifest(config)
    patrol = build(config)
    dry_run = False if args.live else bool(args.dry_run or patrol.bcfg.get("dry_run", True))

    if args.command == "login":
        return qr_login(Path(args.config), timeout=args.login_timeout)

    if args.command in ("status", "check-login"):
        return cmd_status(patrol)

    if not patrol.bili.logged_in:
        print("提示：未配置 SESSDATA / bili_jct / DedeUserID，只能读取，不能评论/回复。")

    if args.command == "once":
        result = patrol.patrol_once(dry_run=dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        reply_result = patrol.check_replies(dry_run=dry_run)
        print(json.dumps(reply_result, ensure_ascii=False, indent=2))
        return 0

    actions_cfg = config.get("actions") or {}
    if actions_cfg.get("enabled", True):
        try:
            ActionServer(config, patrol).start()
            print(
                f"B站 actions 已启动: http://{actions_cfg.get('host') or '127.0.0.1'}:"
                f"{actions_cfg.get('port') or 18901}/actions",
                flush=True,
            )
        except OSError as exc:
            print(f"B站 actions 启动失败（端口占用？）: {exc}", flush=True)

    patrol.run_forever(dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
