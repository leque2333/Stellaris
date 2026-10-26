#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
快速调整助手 B 站巡检与发评配额的极简控制脚本
用法示例：
    python adjust_bili_quota.py status               # 查看当前所有配额与巡回状态
    python adjust_bili_quota.py reset                # 立即清零今日与当前巡回发评计数
    python adjust_bili_quota.py add                  # 手动开启 1 小时巡回（直接分配全新 5 次发评额度）
    python adjust_bili_quota.py session_quota 8      # 将单次巡回发评配额临时改为 8 次
    python adjust_bili_quota.py interval 300         # 将发评匀速间隔改为 300 秒 (5分钟)
"""

import sys
import json
import time
import subprocess
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent / "astrbot" / "data" / "plugins" / "astrbot_plugin_human_patrol"
STATE_FILE = PLUGIN_DIR / "state.json"
CONFIG_FILE = PLUGIN_DIR / "config.json"
CONTAINER_NAME = "astrbot"


def load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(p, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def restart_container():
    print("正在热重启 AstrBot 容器生效...")
    subprocess.run(["docker", "restart", CONTAINER_NAME], stdout=subprocess.DEVNULL)
    print("✅ 容器已重启成功！")


def action_status():
    st = load_json(STATE_FILE)
    cfg = load_json(CONFIG_FILE).get("bilibili", {})
    
    now = time.time()
    today_cnt = st.get("today_comment_count", 0)
    manual_until = st.get("manual_active_until", 0)
    cur_manual_id = st.get("current_manual_session_id", "无")
    session_stats = st.get("session_stats", {})
    
    max_session = cfg.get("max_comments_per_session", 5)
    interval = cfg.get("min_comment_interval_seconds", 600)
    
    is_active = now < manual_until
    remain_min = int((manual_until - now) / 60) if is_active else 0
    
    print("\n========== 📊 助手 B 站巡查配额状态 ==========")
    print(f"今日全天发评总计: {today_cnt} 条")
    print(f"单次巡回发评限额: {max_session} 条 / 轮")
    print(f"发评匀速冷却间隔: {interval} 秒 (约 {interval // 60} 分钟)")
    print(f"手动巡回激活状态: {'🟢 活跃中 (剩余约 ' + str(remain_min) + ' 分钟)' if is_active else '⚪ 未激活'}")
    print(f"当前手动巡回标识: {cur_manual_id}")
    
    if cur_manual_id in session_stats:
        cur_stat = session_stats[cur_manual_id]
        cur_cnt = cur_stat.get("count", 0)
        last_ts = cur_stat.get("last_comment_ts", 0)
        elapsed = int(now - last_ts) if last_ts else 9999
        cd_left = max(0, int(interval - elapsed)) if last_ts else 0
        print(f"本轮巡回已用配额: {cur_cnt}/{max_session} 次")
        print(f"距上次发评经过: {elapsed} 秒 (冷却剩余: {cd_left} 秒)")
    print("=============================================\n")


def action_reset():
    st = load_json(STATE_FILE)
    st["today_comment_count"] = 0
    st["session_stats"] = {}
    save_json(STATE_FILE, st)
    print("✅ 已成功清零今日全部发评计数与历史会话配额！")
    restart_container()


def action_add():
    st = load_json(STATE_FILE)
    now = time.time()
    new_session_id = f"manual_{int(now)}"
    st["manual_active_until"] = now + 3600
    st["current_manual_session_id"] = new_session_id
    st.setdefault("session_stats", {})[new_session_id] = {
        "count": 0,
        "last_comment_ts": 0.0
    }
    save_json(STATE_FILE, st)
    print(f"✅ 已成功开启 1 小时额外巡回 ({new_session_id})，分配全新独立 5 次发评额度！")
    restart_container()


def action_set_session_quota(quota: int):
    cfg = load_json(CONFIG_FILE)
    cfg.setdefault("bilibili", {})["max_comments_per_session"] = int(quota)
    save_json(CONFIG_FILE, cfg)
    print(f"✅ 已将单次巡回发评配额修改为: {quota} 次")
    restart_container()


def action_set_interval(seconds: int):
    cfg = load_json(CONFIG_FILE)
    cfg.setdefault("bilibili", {})["min_comment_interval_seconds"] = int(seconds)
    save_json(CONFIG_FILE, cfg)
    print(f"✅ 已将发评间隔冷却时间修改为: {seconds} 秒")
    restart_container()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
        
    cmd = args[0].lower()
    if cmd == "status":
        action_status()
    elif cmd == "reset":
        action_reset()
    elif cmd == "add":
        action_add()
    elif cmd == "session_quota":
        if len(args) < 2:
            print("❌ 请输入数值，例如: python adjust_bili_quota.py session_quota 8")
        else:
            action_set_session_quota(int(args[1]))
    elif cmd == "interval":
        if len(args) < 2:
            print("❌ 请输入秒数，例如: python adjust_bili_quota.py interval 300")
        else:
            action_set_interval(int(args[1]))
    else:
        print(f"❌ 未知命令: {cmd}，运行 python adjust_bili_quota.py 查看帮助。")
