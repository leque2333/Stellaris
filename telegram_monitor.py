import asyncio
import json
import logging
import os
import subprocess
import time
import requests
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("TGMonitor")

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tg_monitor_config.json")


def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取配置失败: {e}")
    return {}


_INIT_CONFIG = load_config()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or _INIT_CONFIG.get("bot_token", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
PROXIES = {
    "http": os.getenv("HTTP_PROXY", "http://127.0.0.1:7897"),
    "https": os.getenv("HTTPS_PROXY", "http://127.0.0.1:7897"),
}


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存配置失败: {e}")


def send_tg_message(chat_id, text):
    """向 Telegram 发送纯文本通知，支持 Markdown，绝不发二维码图片"""
    url = f"{TELEGRAM_API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    try:
        resp = requests.post(url, json=payload, proxies=PROXIES, timeout=15)
        return resp.json().get("ok", False)
    except Exception as e:
        logger.error(f"发送 Telegram 消息失败: {e}")
        return False


async def check_qq_online_async() -> bool:
    """通过 OneBot v11 正向 WebSocket 精准检测 QQ 是否处于在线状态"""
    try:
        async with websockets.connect("ws://127.0.0.1:3001", open_timeout=2) as ws:
            payload = {"action": "get_status", "echo": "status_check"}
            await ws.send(json.dumps(payload))
            for _ in range(5):
                resp = await asyncio.wait_for(ws.recv(), timeout=2.5)
                data = json.loads(resp)
                if data.get("echo") == "status_check":
                    is_online = data.get("data", {}).get("online", False)
                    return bool(is_online)
    except Exception:
        return False
    return False


def check_qq_online() -> bool:
    try:
        return asyncio.run(check_qq_online_async())
    except Exception:
        return False


def get_docker_status():
    try:
        proc = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return proc.stdout.strip()
    except Exception:
        return "无法获取容器信息"


def action_status(chat_id):
    is_online = check_qq_online()
    qq_status = "✅ 在线正常" if is_online else "❌ 掉线 / 离线"
    docker_info = get_docker_status()
    msg = f"📊 *QQ 状态*：{qq_status}\n\n*容器运行状态*：\n```text\n{docker_info}\n```"
    send_tg_message(chat_id, msg)


def action_restart_all(chat_id):
    send_tg_message(chat_id, "🔄 正在重启机器人服务 (NapCat & AstrBot)...")
    subprocess.run(["docker", "restart", "astrbot", "napcat"], capture_output=True)
    time.sleep(4)
    is_online = check_qq_online()
    qq_status = "✅ 在线" if is_online else "⏳ 启动中或等待登录"
    send_tg_message(chat_id, f"✅ 重启完成，QQ 状态: {qq_status}")


def action_help(chat_id):
    help_text = (
        "🤖 *哈基米 Telegram 监控助手*\n\n"
        "• `/status` 或 `状态` - 查看 QQ 在线状态与容器运行情况\n"
        "• `/restart` 或 `重启` - 重启 QQ 与 AstrBot 容器\n"
        "• `/help` 或 `帮助` - 查看帮助\n\n"
        "💡 *掉线提醒*：QQ 掉线时会自动发送文字提醒，无需扫码发图。"
    )
    send_tg_message(chat_id, help_text)


def process_user_command(chat_id, text):
    cmd = text.strip().lower()
    logger.info(f"收到用户指令: {cmd}")

    if cmd in ["/status", "status", "状态"]:
        action_status(chat_id)
    elif cmd in ["/restart", "restart", "重启"]:
        action_restart_all(chat_id)
    elif cmd in ["/help", "help", "/start", "帮助"]:
        action_help(chat_id)
    elif cmd in ["/qr", "qr", "二维码", "/refresh", "refresh", "刷新"]:
        is_online = check_qq_online()
        if is_online:
            send_tg_message(chat_id, "✅ QQ 机器人当前在线运行中，无需扫码。")
        else:
            send_tg_message(chat_id, "⚠️ QQ 当前处于离线状态；如需重启服务可发送 `/restart`。")


def main():
    logger.info("启动 Telegram QQ 掉线监控服务（仅文字提醒，不发二维码）...")
    config = load_config()
    default_chat_id = config.get("chat_id")
    last_update_id = config.get("last_update_id", 0)

    # 首次启动：获取最新的 update_id，避免旧消息回放
    try:
        init_resp = requests.get(
            f"{TELEGRAM_API_BASE}/getUpdates",
            params={"offset": -1, "timeout": 5},
            proxies=PROXIES,
            timeout=10,
        )
        init_data = init_resp.json()
        if init_data.get("ok") and init_data.get("result"):
            last_update_id = init_data["result"][-1]["update_id"]
            config["last_update_id"] = last_update_id
            save_config(config)
    except Exception as e:
        logger.warning(f"初始化 offset 异常: {e}")

    # 状态机：跟踪 QQ 当前状态
    current_state = "ONLINE" if check_qq_online() else "OFFLINE"
    logger.info(f"初始 QQ 状态: {current_state}")

    offline_consecutive = 0
    online_consecutive = 0
    last_check_time = 0
    CHECK_INTERVAL = 15  # 每 15 秒检查一次在线状态

    while True:
        try:
            # 1. 检查 Telegram 用户指令（长轮询 5 秒）
            updates_url = f"{TELEGRAM_API_BASE}/getUpdates"
            params = {"timeout": 5}
            if last_update_id > 0:
                params["offset"] = last_update_id + 1

            resp = requests.get(updates_url, params=params, proxies=PROXIES, timeout=12)
            data = resp.json()
            if data.get("ok") and data.get("result"):
                for item in data["result"]:
                    last_update_id = item["update_id"]
                    config["last_update_id"] = last_update_id
                    save_config(config)

                    msg = item.get("message")
                    if msg and "text" in msg:
                        from_chat_id = msg["chat"]["id"]
                        if not default_chat_id or default_chat_id != from_chat_id:
                            default_chat_id = from_chat_id
                            config["chat_id"] = default_chat_id
                            save_config(config)
                        process_user_command(from_chat_id, msg["text"])

        except Exception as e:
            logger.debug(f"TG 轮询异常: {e}")
            time.sleep(2)

        # 2. 定期检测 QQ 在线状态
        now = time.time()
        if now - last_check_time >= CHECK_INTERVAL:
            last_check_time = now
            is_online = check_qq_online()

            if is_online:
                online_consecutive += 1
                offline_consecutive = 0
                if current_state == "OFFLINE" and online_consecutive >= 2:
                    current_state = "ONLINE"
                    logger.info("QQ 机器人已恢复在线！")
                    if default_chat_id:
                        send_tg_message(
                            default_chat_id,
                            "✅ *【QQ 恢复在线】*\nQQ 机器人 (哈基米) 已重新上线并恢复正常运行。",
                        )
            else:
                offline_consecutive += 1
                online_consecutive = 0
                if current_state == "ONLINE" and offline_consecutive >= 2:
                    current_state = "OFFLINE"
                    logger.warning("检测到 QQ 机器人已掉线！发送文字提醒...")
                    if default_chat_id:
                        send_tg_message(
                            default_chat_id,
                            "⚠️ *【QQ 掉线提醒】*\n检测到 QQ (哈基米) 已掉线，请留意！\n如需尝试重启可发送 `/restart`。",
                        )


if __name__ == "__main__":
    main()
