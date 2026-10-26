import os
import sys
import json
import sqlite3
import shutil
import time
from datetime import datetime

BASE_DIR = os.getenv("ROBOT_BASE_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
ASTRBOT_DATA_DIR = os.getenv("ASTRBOT_DATA_DIR", os.path.join(BASE_DIR, "astrbot", "data"))
DB_PATH = os.path.join(ASTRBOT_DATA_DIR, "data_v4.db")
MEMORY_DB_PATH = os.path.join(ASTRBOT_DATA_DIR, "memory.db")
PATROL_CONFIG_PATH = os.path.join(ASTRBOT_DATA_DIR, "plugins", "astrbot_plugin_human_patrol", "config.json")
PATROL_STATE_PATH = os.path.join(ASTRBOT_DATA_DIR, "plugins", "astrbot_plugin_human_patrol", "state.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups", "console_backups")

os.makedirs(BACKUP_DIR, exist_ok=True)

def get_db_connection(path=DB_PATH, read_only=True):
    """
    跨平台（Windows宿主机 / Linux Docker容器）安全 SQLite 连接器
    采用只读+不可变参数绕过容器卷 CIFS/VirtioFS 的文件锁竞争
    """
    if read_only:
        # 使用 URI 模式，以只读且 immutable=1 打开，避免文件锁冲突
        uri = f"file:{os.path.abspath(path)}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    else:
        conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn

# ----------------- 角色卡模块 -----------------

def get_current_persona():
    """获取当前生效的系统提示词"""
    if not os.path.exists(DB_PATH):
        return {"error": "Database not found"}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        row = cur.execute("SELECT persona_id, system_prompt, updated_at FROM personas WHERE persona_id = 'default'").fetchone()
        if row:
            return {
                "persona_id": row["persona_id"],
                "system_prompt": row["system_prompt"],
                "updated_at": str(row["updated_at"]),
                "length": len(row["system_prompt"] or "")
            }
        return {"error": "Default persona not found"}
    finally:
        conn.close()

def get_preset_personas():
    """获取内置角色卡预设列表"""
    presets = []
    # 扫描根目录与 astrbot/data 的 markdown / txt 设定
    candidate_files = [
        ("助手 V6.3 平衡版 (当前最新)", os.path.join(BASE_DIR, "anan_core_settings_v5_3.md")),
        ("哈基米 V5.2 经典傲娇版", os.path.join(BASE_DIR, "hakimi_core_settings_v5_2.md")),
        ("AstrBot 暂存新设定 (new_persona.txt)", os.path.join(ASTRBOT_DATA_DIR, "new_persona.txt")),
        ("历史合并设定 (persona_v4_merged.md)", os.path.join(ASTRBOT_DATA_DIR, "persona_v4_merged.md")),
        ("原初基底设定 (persona_v4_original.md)", os.path.join(ASTRBOT_DATA_DIR, "persona_v4_original.md")),
    ]
    for name, path in candidate_files:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                presets.append({
                    "name": name,
                    "filename": os.path.basename(path),
                    "length": len(content),
                    "content": content
                })
            except Exception as e:
                pass
    return presets

def save_persona(new_prompt, backup=True):
    """保存并热应用新角色卡"""
    if backup and os.path.exists(DB_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"persona_backup_{ts}.txt")
        curr = get_current_persona()
        if "system_prompt" in curr:
            with open(backup_file, "w", encoding="utf-8") as f:
                f.write(curr["system_prompt"])
    
    # 在容器中如果直接连接被 AstrBot 占用独占锁，则采用安全的影子事务更新
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = get_db_connection(DB_PATH, read_only=False)
        cur = conn.cursor()
        cur.execute("UPDATE personas SET system_prompt = ?, updated_at = ? WHERE persona_id = 'default'", (new_prompt, now_str))
        conn.commit()
        conn.close()
    except Exception:
        # 降级：采用 shadow copy 安全覆写
        temp_db = os.path.join(BACKUP_DIR, f"temp_{int(time.time())}.db")
        shutil.copy2(DB_PATH, temp_db)
        conn = sqlite3.connect(temp_db)
        cur = conn.cursor()
        cur.execute("UPDATE personas SET system_prompt = ?, updated_at = ? WHERE persona_id = 'default'", (new_prompt, now_str))
        conn.commit()
        conn.close()
        shutil.copy2(temp_db, DB_PATH)
        if os.path.exists(temp_db):
            os.remove(temp_db)

    # 同步更新本地 new_persona.txt 以便追踪
    sync_txt = os.path.join(ASTRBOT_DATA_DIR, "new_persona.txt")
    with open(sync_txt, "w", encoding="utf-8") as f:
        f.write(new_prompt)
    return {"success": True, "length": len(new_prompt), "updated_at": now_str}

# ----------------- B站巡检配置模块 -----------------

def get_patrol_config():
    """读取 B 站巡航与 Token 监视器配置"""
    if not os.path.exists(PATROL_CONFIG_PATH):
        return {"error": "Patrol config not found"}
    with open(PATROL_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_patrol_config(new_config):
    """保存 B 站巡航配置，同时生成自动备份"""
    if os.path.exists(PATROL_CONFIG_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"patrol_config_backup_{ts}.json")
        shutil.copy2(PATROL_CONFIG_PATH, backup_file)
    with open(PATROL_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(new_config, f, ensure_ascii=False, indent=2)
    return {"success": True}

def get_patrol_status():
    """读取巡检实时运行状态 (state.json)"""
    if not os.path.exists(PATROL_STATE_PATH):
        return {}
    try:
        with open(PATROL_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

# ----------------- Token 消耗与大模型调用监控 -----------------

def get_token_analytics():
    """统计今日与总体 Token 消耗情况"""
    if not os.path.exists(DB_PATH):
        return {}
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        today_str = datetime.now().strftime("%Y-%m-%d")
        
        # 今日调用总量
        row_today = cur.execute("""
            SELECT 
                COUNT(*) as call_count,
                COALESCE(SUM(token_input_other + token_input_cached), 0) as total_input,
                COALESCE(SUM(token_output), 0) as total_output,
                COALESCE(SUM(token_input_cached), 0) as cached_input
            FROM provider_stats 
            WHERE date(created_at) = ?
        """, (today_str,)).fetchone()
        
        # 全部调用总量
        row_all = cur.execute("""
            SELECT 
                COUNT(*) as call_count,
                COALESCE(SUM(token_input_other + token_input_cached), 0) as total_input,
                COALESCE(SUM(token_output), 0) as total_output
            FROM provider_stats
        """).fetchone()

        # 最近 10 次调用流水
        recent_rows = cur.execute("""
            SELECT id, provider_model, token_input_other, token_input_cached, token_output, created_at
            FROM provider_stats 
            ORDER BY id DESC 
            LIMIT 10
        """).fetchall()

        recent_calls = []
        for r in recent_rows:
            recent_calls.append({
                "id": r["id"],
                "model": r["provider_model"],
                "input": r["token_input_other"] + r["token_input_cached"],
                "cached": r["token_input_cached"],
                "output": r["token_output"],
                "time": str(r["created_at"])
            })

        return {
            "today": {
                "date": today_str,
                "calls": row_today["call_count"],
                "input_tokens": row_today["total_input"],
                "output_tokens": row_today["total_output"],
                "cached_tokens": row_today["cached_input"],
                "total_tokens": row_today["total_input"] + row_today["total_output"]
            },
            "overall": {
                "calls": row_all["call_count"],
                "input_tokens": row_all["total_input"],
                "output_tokens": row_all["total_output"],
                "total_tokens": row_all["total_input"] + row_all["total_output"]
            },
            "recent_calls": recent_calls
        }
    finally:
        conn.close()

# ----------------- 记忆库与概览统计 -----------------

def get_timeline_events(limit=25):
    """获取助手自传式时间轴最近经历流"""
    if not os.path.exists(MEMORY_DB_PATH):
        return []
    conn = get_db_connection(MEMORY_DB_PATH)
    try:
        cur = conn.cursor()
        tbl = cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_timeline'").fetchone()
        if not tbl:
            return []
        rows = cur.execute(
            """
            SELECT event_id, timestamp, time_str, source_module, actor_name, narrative, emotional_impact, importance 
            FROM agent_timeline 
            ORDER BY timestamp DESC 
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()

def get_system_summary():
    """获取系统整体简报"""
    summary = {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "persona_status": "Ready",
        "memory_count": 0,
        "timeline_count": 0,
        "master_affection": 90,
        "user_profile_count": 0
    }
    if os.path.exists(MEMORY_DB_PATH):
        try:
            m_conn = get_db_connection(MEMORY_DB_PATH)
            m_cur = m_conn.cursor()
            m_cnt = m_cur.execute("SELECT COUNT(*) FROM episodic_memories").fetchone()[0]
            u_cnt = m_cur.execute("SELECT COUNT(*) FROM user_profiles").fetchone()[0]
            summary["memory_count"] = m_cnt
            summary["user_profile_count"] = u_cnt
            
            # 时间轴总事件数
            t_tbl = m_cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_timeline'").fetchone()
            if t_tbl:
                summary["timeline_count"] = m_cur.execute("SELECT COUNT(*) FROM agent_timeline").fetchone()[0]
            
            # 主人好感度
            aff_row = m_cur.execute("SELECT affection FROM user_profiles WHERE user_id IN ('1000000001', 'master')").fetchone()
            if aff_row and aff_row["affection"] is not None:
                summary["master_affection"] = aff_row["affection"]
            m_conn.close()
        except Exception:
            pass
    return summary

if __name__ == "__main__":
    print("Testing backend manager...")
    print("System Summary:", get_system_summary())
    print("Current Persona Length:", get_current_persona().get("length"))
    print("Presets available:", len(get_preset_personas()))
    print("Token Analytics (Today calls):", get_token_analytics().get("today", {}).get("calls"))
