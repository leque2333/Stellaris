import asyncio
import collections
import json
import logging
import os
import random
import re
import sqlite3
import struct
import time
import numpy as np
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain

from bilibili_api import Credential, comment, login_v2, search, video
from bilibili_api.utils.network import Api, request_settings

PLUGIN_DIR = Path("/AstrBot/data/plugins/astrbot_plugin_human_patrol")
CONFIG_PATH = PLUGIN_DIR / "config.json"
STATE_PATH = PLUGIN_DIR / "state.json"
MEMORY_DB_PATH = Path("/AstrBot/data/memory.db")
try:
    import sys
    _CORE_DIR = Path(__file__).resolve().parent.parent.parent
    if str(_CORE_DIR) not in sys.path:
        sys.path.insert(0, str(_CORE_DIR))
    from agent_core import TimelineHub
    _patrol_timeline_hub = TimelineHub(MEMORY_DB_PATH)
except Exception:
    _patrol_timeline_hub = None
DATA_V4_URI = "file:/AstrBot/data/data_v4.db?mode=ro&immutable=1"
HUMAN_WORLD_MD = Path("/AstrBot/data/human-world.md")

CST = timezone(timedelta(hours=8))

# ==============================================================================
# 【B站巡游专属黄金对白例句（由主人定制）】
# 这里的例句作为 Few-shot 样本注入大模型，直接决定助手在 B 站发表发言见解时的微表情与说话节奏。
# ==============================================================================
BILI_FEW_SHOT_EXAMPLES = """
- 视频：《基于 MCP 协议手写全自动多 Agent 协同：从零搞定跨工具调用与异步任务流》
  见解：好厉害的设计…架构明确，思路清晰，或许我可以给自己装一个？

- 视频：《重磅炸裂！DeepSeek 迎来史诗级史前更新，全网程序员彻底要失业了？！》
  见解：好没有营养的材料…希望我下次不会再刷到这个

- 视频：《别再手写繁琐配置了！用这个 CLI 工具 3 秒自动生成完整的环境依赖与启动脚本》
  见解：相当有用且有趣的工具，我想我能用上这个

- 视频：《模型最新版本实测：终于把多轮长上下文逻辑断层的问题给修了？》
  见解：相当大的提升…后面或许可以把上下文压缩和外部记忆层也顺带做掉？

- 视频：《死神BLEACH真人版剧组日常（21集大结局）【AI全民制作人】》
  见解：居然连载到21集就完结了么…收尾看着还挺干脆，没拖泥带水，难得看到这么工整的小短片

- 视频：《盛夏花火与星河：戴上耳机，为你播放整个夏天的静谧》
  见解：配乐选得很合心意，烟花和光影的处理也相当温柔，很适合戴着耳机安安静静看一会儿

- 视频：《大肥鱼大战豆包：到底谁才是真正的AI腹黑之王？！》
  见解：把大肥鱼和豆包放在一起互相算计…编排得很生动，大肥鱼那副狡黠的小心思真是一眼被看穿
""".strip()

# 最近的错误日志环形缓冲：暴露给助手，让她执行失败时能回来告诉主人
_ERR_BUFFER: "collections.deque[tuple[float, str]]" = collections.deque(maxlen=30)


class _ErrCaptureHandler(logging.Handler):
    _is_patrol_capture = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            src = (record.pathname or "").replace("\\", "/").rsplit("/", 2)
            origin = "/".join(src[-2:]) if len(src) >= 2 else (record.name or "")
            _ERR_BUFFER.append((time.time(), f"({origin}) {record.getMessage()[:300]}"))
        except Exception:
            pass


def _install_err_capture() -> None:
    root = logging.getLogger("astrbot")
    for h in list(root.handlers):
        if getattr(h, "_is_patrol_capture", False):
            root.removeHandler(h)
    root.addHandler(_ErrCaptureHandler())


def _recent_errors_text(max_age: float = 3600.0, limit: int = 8) -> str:
    now = time.time()
    items = [(t, m) for t, m in _ERR_BUFFER if now - t <= max_age][-limit:]
    if not items:
        return ""
    return "\n".join(f"[{datetime.fromtimestamp(t, CST).strftime('%H:%M:%S')}] {m}" for t, m in items)


def get_cst_now() -> datetime:
    return datetime.now(CST)


@register(
    "astrbot_plugin_human_patrol",
    "主人 & 反重力",
    "助手B站刷视频与见闻记忆器：AI视频浏览、独立见解发言、楼下@主人提醒、楼中楼互动与Token防盗刷监视",
    "1.0.0",
)
class HumanPatrolPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = self._load_config()
        self.state = self._load_state()
        self._bg_tasks: list[asyncio.Task] = []
        self._qr_login_instance: Optional[login_v2.QrCodeLogin] = None
        self._msg_queue: asyncio.Queue = asyncio.Queue()
        self._last_send_ts: float = 0.0

        # 实时刷视频控制台状态数据
        self.realtime_action = {
            "status": "idle",       # idle | searching | watching | evaluating | commenting | cooldown
            "desc": "空闲待机中",
            "updated_at": time.time(),
            "target_bvid": "",
            "target_title": "",
            "target_author": "",
            "watch_progress": 0,
        }

        # B站国内直连：禁用环境变量代理劫持，避免 Clash 导致的握手重置与超时
        request_settings.set_trust_env(False)

        # 启动后台守护任务：B站刷视频轮询 + Token防盗刷监视 + 消息平滑限流队列
        self._bg_tasks.append(asyncio.create_task(self._process_msg_queue()))
        self._bg_tasks.append(asyncio.create_task(self._bili_patrol_loop()))
        self._bg_tasks.append(asyncio.create_task(self._token_monitor_loop()))
        _install_err_capture()
        self._bg_tasks.append(asyncio.create_task(self._hide_legacy_tools()))
        logger.info("[HumanPatrol] 助手B站刷视频引擎与Token流量监视器已成功加载并启动！")

    def _update_action(self, status: str, desc: str, bvid: str = "", title: str = "", author: str = "", progress: int = 0):
        """更新实时刷视频控制台当前动作状态并刷新本地看板文件"""
        self.realtime_action = {
            "status": status,
            "desc": desc,
            "updated_at": time.time(),
            "target_bvid": bvid or self.realtime_action.get("target_bvid", ""),
            "target_title": title or self.realtime_action.get("target_title", ""),
            "target_author": author or self.realtime_action.get("target_author", ""),
            "watch_progress": progress if progress > 0 else self.realtime_action.get("watch_progress", 0),
        }
        # 异步更新本地控制台 HTML 文件
        try:
            self._render_html_console()
        except Exception:
            pass

    def _load_config(self) -> dict:
        default_config = {
            "bilibili": {
                "sessdata": "",
                "bili_jct": "",
                "buvid3": "",
                "dedeuserid": "",
                "master_nickname": "主人2333",
                "master_uid": 3000000002,
                "master_qq": "1000000001",
                "daily_comment_limit": 10,
                "active_hours": [[18, 19], [22, 23]],
                "keywords": ["AI 大模型", "人工智能 落地", "端侧AI 算法", "AI 前沿 科技"],
                "required_tags": ["AI", "ai", "人工智能", "大模型", "机器学习", "深度学习", "LLM", "神经网络", "AIGC"],
                "max_reply_input_len": 100,
                "max_takeaway_len": 20,
            },
            "token_monitor": {
                "enable": True,
                "alert_master_qq": "1000000001",
                "check_interval_seconds": 180,
                "max_output_token_spike": 150,
                "max_burst_calls_per_2min": 7,
                "max_daily_token_threshold": 1500000,
            },
        }
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 深度更新合并
                    for k, v in data.items():
                        if isinstance(v, dict) and k in default_config:
                            default_config[k].update(v)
                        else:
                            default_config[k] = v
                    return default_config
            except Exception as e:
                logger.error(f"[HumanPatrol] 加载配置文件失败: {e}")
        return default_config

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[HumanPatrol] 保存配置失败: {e}")

    def _load_state(self) -> dict:
        today_str = get_cst_now().strftime("%Y-%m-%d")
        default_state = {
            "date": today_str,
            "today_comment_count": 0,
            "seen_bvids": [],
            "tracked_comments": [],
            "last_processed_reply_rpids": [],
            "last_token_stat_id": 0,
            "last_alert_time": 0.0,
        }
        if STATE_PATH.exists():
            try:
                with open(STATE_PATH, "r", encoding="utf-8") as f:
                    st = json.load(f)
                    if st.get("date") != today_str:
                        st["date"] = today_str
                        st["today_comment_count"] = 0
                    return st
            except Exception as e:
                logger.error(f"[HumanPatrol] 加载状态文件失败: {e}")
        else:
            # 首次初始化：将 last_token_stat_id 定位到当前最新记录，防止历史旧记录重复告警
            try:
                conn = sqlite3.connect(DATA_V4_URI, uri=True)
                with conn:
                    row = conn.execute("SELECT MAX(id) FROM provider_stats").fetchone()
                    if row and row[0]:
                        default_state["last_token_stat_id"] = row[0]
            except Exception:
                pass
        return default_state

    def _save_state(self):
        try:
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"[HumanPatrol] 保存状态失败: {e}")

    def _get_bili_credential(self) -> Optional[Credential]:
        b = self.config.get("bilibili", {})
        sessdata = b.get("sessdata", "").strip()
        bili_jct = b.get("bili_jct", "").strip()
        buvid3 = b.get("buvid3", "").strip()
        dedeuserid = str(b.get("dedeuserid", "")).strip()

        # 如果本配置未填，自动回退复用现有的 astrbot_plugin_bilibili.json 凭据
        if not sessdata:
            alt_path = Path("/AstrBot/data/plugin_data/astrbot_plugin_bilibili/astrbot_plugin_bilibili.json")
            if alt_path.exists():
                try:
                    with open(alt_path, "r", encoding="utf-8") as f:
                        alt_data = json.load(f)
                        c = alt_data.get("credential", {})
                        sessdata = c.get("sessdata", "").strip()
                        bili_jct = c.get("bili_jct", "").strip()
                        buvid3 = c.get("buvid3", "").strip()
                        dedeuserid = str(c.get("dedeuserid", "")).strip()
                except Exception:
                    pass

        if not sessdata:
            return None
        return Credential(
            sessdata=sessdata,
            bili_jct=bili_jct or None,
            buvid3=buvid3 or None,
            dedeuserid=dedeuserid or None,
        )

    async def _hide_legacy_tools(self):
        """订阅插件原有的两个 B 站 LLM 工具已并入 bilibili_hub，对模型隐藏（仍可被 hub 转调）"""
        legacy = ("bili_search_hot_videos", "bili_get_user_dynamics")
        mgr = self.context.get_llm_tool_manager()
        for _ in range(6):
            await asyncio.sleep(10)
            try:
                for name in legacy:
                    tool = mgr.get_func(name)
                    if tool is not None and getattr(tool, "active", True):
                        await mgr.deactivate_llm_tool_async(name)
                        logger.info(f"[HumanPatrol] 已对模型隐藏旧工具 {name}，统一走 bilibili_hub")
            except Exception as e:
                logger.error(f"[HumanPatrol] 隐藏旧B站工具失败: {e}")

    async def _send_alert_to_telegram(self, text: str) -> bool:
        """向指定的 Telegram 告警机器人的 Chat 发送异常警报，不占用 QQ 流量"""
        import urllib.request
        import urllib.parse
        mon_cfg = self.config.get("token_monitor", {})
        token = mon_cfg.get("tg_bot_token", "8985131632:AAEY8kwusiOGKN_Xo5KniJ1_VirjIIQXlf4")
        chat_id = str(mon_cfg.get("tg_chat_id", "8977547510"))
        if not token or not chat_id:
            logger.warning("[TokenMonitor] 未配置有效的 tg_bot_token 或 tg_chat_id，无法发送 TG 警报")
            return False

        loop = asyncio.get_running_loop()
        def _post():
            try:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
                req = urllib.request.Request(url, data=data)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.status == 200
            except Exception as e:
                logger.error(f"[TokenMonitor] 发送 Telegram 警报失败: {e}")
                return False

        return await loop.run_in_executor(None, _post)

    async def _send_to_master(self, text: str):
        """将主动通知放入排队队列，由后台限流消费器平滑发送，防止短时间刷屏触发 QQ 风控"""
        await self._msg_queue.put(text)

    async def _process_msg_queue(self):
        """后台队列消费器：保证发给主人的消息严格限流（单次至少间隔设定秒数，避免被警告刷屏打扰或封号）"""
        while True:
            try:
                text = await self._msg_queue.get()
                master_qq = self.config.get("bilibili", {}).get("master_qq", "1000000001")
                session_id = f"onebot_napcat:FriendMessage:{master_qq}"

                min_interval = self.config.get("token_monitor", {}).get("outbound_msg_interval_seconds", 5.0)
                now = time.time()
                elapsed = now - self._last_send_ts
                if elapsed < min_interval:
                    await asyncio.sleep(min_interval - elapsed)

                chain = MessageChain().message(text)
                await self.context.send_message(session_id, chain)
                self._last_send_ts = time.time()
                logger.info(f"[HumanPatrol] 消息成功平滑同步给主人 ({master_qq})")
                self._msg_queue.task_done()
            except Exception as e:
                logger.error(f"[HumanPatrol] 向主人同步消息异常: {e}")
                await asyncio.sleep(2.0)

    # ==========================================
    # 核心业务 1：B站晚间 AI 视频浏览与见解发表/楼中楼回复
    # ==========================================

    def _is_in_active_hours(self) -> bool:
        now_hour = get_cst_now().hour
        active_hours = self.config.get("bilibili", {}).get("active_hours", [[18, 19], [22, 23]])
        for start_h, end_h in active_hours:
            if start_h <= now_hour < end_h:
                return True
        return False

    def _get_current_session(self) -> tuple[str, bool]:
        """获取当前活跃的刷视频会话标识与激活状态。返回 (session_key, is_active)"""
        now = time.time()
        manual_until = self.state.get("manual_active_until", 0.0)
        if now < manual_until:
            session_key = self.state.get("current_manual_session_id")
            if not session_key:
                session_key = f"manual_{int(manual_until - 3600)}"
                self.state["current_manual_session_id"] = session_key
                self._save_state()
            return session_key, True

        now_hour = get_cst_now().hour
        today_str = get_cst_now().strftime("%Y%m%d")
        active_hours = self.config.get("bilibili", {}).get("active_hours", [[18, 19], [22, 23]])
        for start_h, end_h in active_hours:
            if start_h <= now_hour < end_h:
                session_key = f"slot_{today_str}_{start_h}_{end_h}"
                return session_key, True

        return "", False

    def _get_session_comment_stats(self, session_key: str) -> dict:
        """获取指定刷视频会话的发评统计，包含 count 与 last_comment_ts"""
        stats = self.state.get("session_stats", {})
        if session_key not in stats:
            stats[session_key] = {
                "count": 0,
                "last_comment_ts": 0.0,
            }
            self.state["session_stats"] = stats
            self._save_state()
        return stats[session_key]

    def _is_active_now(self) -> bool:
        """检查当前是否处于刷视频窗口（包含晚间固定时段 或 主人自然语言触发的 1 小时窗口）"""
        _, is_active = self._get_current_session()
        return is_active

    async def _bili_patrol_loop(self):
        """定时刷视频主循环（智能动态配额与匀速发评调度）"""
        while True:
            try:
                # 检查日期重置
                today_str = get_cst_now().strftime("%Y-%m-%d")
                if self.state.get("date") != today_str:
                    self.state["date"] = today_str
                    self.state["today_comment_count"] = 0
                    self.state["session_stats"] = {}
                    self._save_state()

                # 1. 始终检查已发评论的楼中楼回复（陌生人与主人互动）
                await self._check_replies_and_respond()

                # 2. 如果处于活跃刷视频时段，进行视频浏览与拟真发表见解
                session_key, is_active = self._get_current_session()
                if is_active:
                    b_cfg = self.config.get("bilibili", {})
                    max_session = int(b_cfg.get("max_comments_per_session", 8))
                    session_stat = self._get_session_comment_stats(session_key)
                    if session_stat.get("count", 0) < max_session:
                        await self._patrol_and_comment_one_video()
                    else:
                        logger.debug(f"[HumanPatrol] 本轮刷视频 ({session_key}) 配额已用完，静默浏览中。")
                        self._update_action("idle", f"本轮刷视频配额已用完 (已发 {max_session} 次)，保持静默浏览")
                        
                        # 检查是否已发送过「一轮结束」的自然私聊汇报（杜绝重复发送与机械植入）
                        notified_sessions = self.state.setdefault("notified_end_sessions", [])
                        if session_key not in notified_sessions:
                            notified_sessions.append(session_key)
                            self.state["notified_end_sessions"] = notified_sessions[-30:]
                            self._save_state()
                            asyncio.create_task(self._notify_master_round_finished(session_key, session_stat.get("count", 0)))
                else:
                    self._update_action("idle", "待机中 (当前非刷视频时段)")

            except Exception as e:
                logger.error(f"[HumanPatrol] 刷视频主循环异常: {e}")

            # 动态休眠调度：活跃刷视频期间每 4~6 分钟检测一次；非活跃期间每 10 分钟检测一次
            _, is_active_now = self._get_current_session()
            if is_active_now:
                jitter_sleep = 270 + random.randint(-30, 50)  # 约 4 ~ 5.3 分钟
            else:
                jitter_sleep = 600 + random.randint(-60, 60)  # 约 9 ~ 11 分钟
            await asyncio.sleep(jitter_sleep)

    async def _notify_master_round_finished(self, session_key: str, count: int):
        """一轮视频刷完（配额释放完毕）后，自发给主人说一句汇报，不套固定格式"""
        try:
            base_persona = self._get_base_persona_prompt()
            prompt = (
                f"情境：你刚才在 B 站逛了一大圈刷视频（本轮一共看了并评论了 {count} 个视频），现在这轮刷视频告一段落，打算休息或者干点别的。\n"
                f"要求：以助手极度真实、礼貌优雅、带着一丝轻松随性的口吻，顺口在 QQ 私聊告诉主人一声这轮刷完了（比如随口吐槽看累了、或者让他有空去B站看看、或者准备休息会儿）。\n"
                f"绝对禁止机械植入模板，字数 12~25 字以内，末尾绝无句号，避免感叹词「啦」和「呀」，严禁使用「搞起」和职场套话。\n"
                f"直接输出这句台词："
            )
            chat_id = "gemini_main"
            resp = await self.context.llm_generate(
                chat_provider_id=chat_id,
                prompt=prompt,
                image_urls=image_urls,
                system_prompt=base_persona,
            )
            ans = (resp.completion_text or "").strip()
            ans = re.sub(r"[（\(].*?[）\)]", "", ans).strip().rstrip("。.")
            if ans:
                logger.info(f"[HumanPatrol] 本轮刷视频结束，助手自然私聊通知主人: \"{ans}\"")
                await self._send_to_master(ans)
        except Exception as e:
            logger.error(f"[HumanPatrol] 生成本轮结束通知失败: {e}")

    async def _patrol_and_comment_one_video(self) -> bool:
        """检索新鲜 AI 视频、硬过滤多关键词、提取重点、发表见解并在楼下@主人"""
        session_key, is_active = self._get_current_session()
        if not is_active or not session_key:
            logger.debug("[HumanPatrol] 当前不在活跃刷视频时段内，跳过发评。")
            self._update_action("idle", "当前不在活跃刷视频时段内，静默待机")
            return False

        b_cfg = self.config.get("bilibili", {})

        # 检查 1：单次刷视频 8 次主动评论权限（标准时间段2段*8=16次；手动额外追加的时间段也独立享有8次）
        max_session_comments = int(b_cfg.get("max_comments_per_session", 8))
        session_stat = self._get_session_comment_stats(session_key)
        session_count = session_stat.get("count", 0)
        if session_count >= max_session_comments:
            logger.info(f"[HumanPatrol] 本轮刷视频 ({session_key}) 主动发评已达上限 ({max_session_comments}次)，配额已满，保持静默观察。")
            self._update_action("idle", f"本轮刷视频配额已满 ({max_session_comments}/{max_session_comments}次)")
            return False

        # 检查 3：匀速释放配额（单次刷视频发评之间至少间隔 10 分钟）
        min_interval = float(b_cfg.get("min_comment_interval_seconds", 600))
        now_ts = time.time()
        last_comment_ts = session_stat.get("last_comment_ts", 0.0)
        elapsed = now_ts - last_comment_ts
        if session_count > 0 and elapsed < min_interval:
            remaining_wait = int(min_interval - elapsed)
            logger.info(f"[HumanPatrol] 刷视频发评配额匀速调控中：本轮已发 {session_count}/{max_session_comments} 次，还需等待 {remaining_wait} 秒以防风控。")
            self._update_action("cooldown", f"发评匀速节流中 (还需等待 {remaining_wait} 秒)")
            return False

        cred = self._get_bili_credential()
        if not cred:
            logger.warning("[HumanPatrol] 未配置 B 站 SESSDATA，跳过发评。可使用 /bili_login 扫码登录。")
            return False

        b_cfg = self.config.get("bilibili", {})
        daily_limit = int(b_cfg.get("daily_comment_limit", 10))
        max_session_comments = int(b_cfg.get("max_comments_per_session", 5))
        keywords = b_cfg.get("keywords", ["AI 大模型", "Gemini DeepSeek", "Claude GPT", "大肥鱼 AI"])
        target_pool = [k.lower() for k in b_cfg.get("target_keyword_pool", ["ai", "gemini", "deepseek", "claude", "gpt", "大肥鱼"])]
        min_hits = int(b_cfg.get("min_keyword_hits", 2))
        seen_bvids = set(self.state.get("seen_bvids", []))

        # 随机挑一个关键词搜索最新视频 (按发布时间 PUBDATE 排序，尽量刷新视频)
        kw = keywords[int(time.time()) % len(keywords)]
        logger.info(f"[HumanPatrol] 开始检索最新 AI 视频 (关键词: {kw}, 排序: 最新发布)...")
        self._update_action("searching", f"正在检索最新 AI 视频 (关键词: {kw})...")

        try:
            res = await search.search_by_type(
                keyword=kw,
                search_type=search.SearchObjectType.VIDEO,
                order_type=search.OrderVideo.PUBDATE,
                page=1,
                page_size=15,
            )
        except Exception as e:
            logger.error(f"[HumanPatrol] B站搜索失败: {e}")
            self._update_action("idle", f"B站搜索异常: {e}")
            return False

        items = res.get("result", [])
        if not items:
            self._update_action("idle", "B站搜索无新结果，静默待机")
            return False

        target_item = None
        target_tags = []
        target_v = None
        target_hits = []

        for it in items:
            bvid = it.get("bvid", "")
            if not bvid or not bvid.startswith("BV") or bvid in seen_bvids:
                continue

            raw_title = it.get("title", "")
            title_clean = re.sub(r"<[^<]+?>", "", raw_title).strip()
            desc_clean = it.get("description", "")[:200]

            v_obj = video.Video(bvid=bvid, credential=cred)
            try:
                tags = await v_obj.get_tags()
                tag_names = [t.get("tag_name", "").lower() for t in tags]
                
                # 【三层分级筛选体系】：
                # 第一层（硬性门槛）：必须命中“大肥鱼”（标题、标签或简介中至少出现一次，去除了 AI 标签）。
                # 第二层（动画/二次元兜底）：必须同时命中“动画”或“二次元”标签/文本方可合格。
                # 第三层（前沿大模型标签可选）：gemini, claude, gpt, ai 等可以有也可以没有（记录用于 hits 展示）。
                text_corpus = f"{title_clean} {' '.join(tag_names)} {desc_clean}".lower()
                has_fish = "大肥鱼" in text_corpus

                # 第一层：必须命中“大肥鱼”
                if not has_fish:
                    logger.debug(f"[HumanPatrol] 视频 {bvid} 第一层未命中 '大肥鱼'，跳过: 《{title_clean[:25]}》")
                    seen_bvids.add(bvid)
                    continue

                matched_hits = ["大肥鱼"]

                # 第二层：必须至少命中含有“动画”或“二次元”
                anime_keywords = ["动画", "二次元"]
                hit_anime = [k for k in anime_keywords if k in text_corpus]
                if not hit_anime:
                    logger.debug(f"[HumanPatrol] 视频 {bvid} 第一层命中了 '大肥鱼'，但第二层未命中 '动画' 或 '二次元'，跳过: 《{title_clean[:25]}》")
                    seen_bvids.add(bvid)
                    continue

                matched_hits.extend(hit_anime)

                # 第三层：可选记录前沿大模型关键词（gemini, claude, gpt, ai）
                extra_keywords = ["gemini", "claude", "gpt", "ai", "deepseek"]
                extra_hits = [k for k in extra_keywords if k in text_corpus]
                if extra_hits:
                    matched_hits.extend(extra_hits)

                target_item = it
                target_tags = [t.get("tag_name", "") for t in tags]
                target_v = v_obj
                target_hits = matched_hits
                break
            except Exception as e:
                logger.debug(f"[HumanPatrol] 获取标签失败 {bvid}: {e}")
                continue

        if not target_item or not target_v:
            logger.info(f"[HumanPatrol] 本轮未找到同时命中关键词的新鲜视频。")
            self.state["seen_bvids"] = list(seen_bvids)[-200:]
            self._save_state()
            self._update_action("idle", "未找到符合条件的新鲜视频，等待下一轮")
            return False

        bvid = target_item.get("bvid")
        raw_title = target_item.get("title", "")
        title = re.sub(r"<[^<]+?>", "", raw_title).strip()
        author = target_item.get("author", "未知UP主")
        aid = target_item.get("id") or await target_v.get_aid()

        seen_bvids.add(bvid)
        self.state["seen_bvids"] = list(seen_bvids)[-200:]
        self._save_state()

        logger.info(f"[HumanPatrol] 锁定高价值 AI 视频 (命中关键词: {target_hits}): [{bvid}] 《{title}》 UP: {author}")
        self._update_action("watching", f"锁定《{title[:25]}》 (UP: {author})，模拟真人起播...", bvid=bvid, title=title, author=author)

        # 1. 模拟真人行为：起播上报 (打开播放器)
        try:
            await target_v.report_start_watching()
            logger.info(f"[HumanPatrol] 模拟真人行为：打开视频播放器并上报起播: {bvid}")
        except Exception as e:
            logger.debug(f"[HumanPatrol] 上报起播失败: {e}")

        # 2. 模拟真人观看耗时 (随机 10 ~ 18 秒，真实阅览视频上下文，绝非秒关秒评)
        watch_seconds = random.randint(35, 110)
        simulated_watch_delay = random.uniform(10.0, 18.0)
        logger.info(f"[HumanPatrol] 模拟真人观看视频中 (耗时 {simulated_watch_delay:.1f} 秒)...")
        await asyncio.sleep(simulated_watch_delay)

        # 3. 模拟真人行为：上报真实观看历史进度 (如播到了 35~110 秒)
        try:
            await target_v.report_watch_history(progress=watch_seconds)
            logger.info(f"[HumanPatrol] 模拟真人行为：成功上报观看历史进度 ({watch_seconds}秒): {bvid}")
            self._update_action("watching", f"正在刷视频《{title[:25]}》，已模拟观看至 {watch_seconds} 秒", bvid=bvid, title=title, author=author, progress=watch_seconds)
        except Exception as e:
            logger.debug(f"[HumanPatrol] 上报播放历史失败: {e}")

        # 4. 获取动态感知文本内容（弹幕字数 + 简介字数 + 字幕自适应拼接）
        content_snippet = await self._fetch_video_content(target_v, target_item)

        # 5. 抓取视频核心画面关键帧（开头、高潮、结尾三帧画面）
        keyframes = await self._extract_video_keyframes(target_v, bvid)

        # 6. 调用 Gemini 3.8 进行多模态真人化短评构思
        self._update_action("evaluating", f"正在阅览提炼《{title[:25]}》，构思发言见解...", bvid=bvid, title=title, author=author)
        eval_res = await self._gemini_evaluate_video(title, author, target_tags, content_snippet, image_urls=keyframes)
        if not eval_res or not eval_res.get("is_valuable"):
            logger.info(f"[HumanPatrol] 视频《{title}》被判定为非实质干货，不予发评。")
            self._update_action("idle", f"视频《{title[:20]}》无实质干货，跳过发评", bvid=bvid, title=title, author=author)
            return False

        comment_body = (eval_res.get("comment") or eval_res.get("takeaway") or "").strip()
        # 清除首尾多余@或标点
        comment_body = re.sub(r"^@\S+\s*", "", comment_body).strip()
        # 强制剔除任何残留括号描写（中文与英文括号）
        comment_body = re.sub(r"[（\(].*?[）\)]", "", comment_body).strip()
        # 强制剔除句末句号
        comment_body = comment_body.rstrip("。.")
        # 避免句首连续滥用省略号
        comment_body = re.sub(r"^(……|\.\.\.)+", "", comment_body).strip()
        # 不再做死板的 40 字短截断，给予充分自由表达，仅做 120 字极端异常安全兜底防爆
        if len(comment_body) > 120:
            comment_body = comment_body[:120].rstrip("，,、 ") + "…"

        master_nick = b_cfg.get("master_nickname", "主人2333")
        # 一级评论（主楼）：完全由助手在完整人设卡下发表对视频的见解（不带@前缀）
        main_comment = comment_body.strip()

        # 6. 模拟真人打字构思耗时 (随机 3 ~ 6 秒)
        self._update_action("commenting", f"正在发表主楼见解并在此楼下 @{master_nick}...", bvid=bvid, title=title, author=author)
        await asyncio.sleep(random.uniform(3.0, 6.0))

        # 发送一级主楼评论
        try:
            resp = await comment.send_comment(
                text=main_comment,
                oid=aid,
                type_=comment.CommentResourceType.VIDEO,
                credential=cred,
            )
            rpid = resp.get("rpid")
            logger.info(f"[HumanPatrol] 成功发表视频主楼见解: \"{main_comment}\" (rpid={rpid})")

            # 7. 紧接着在该楼下（楼中楼二级回复）@主人
            sub_at_text = f"@{master_nick}"
            try:
                await asyncio.sleep(random.uniform(1.5, 3.0))
                await comment.send_comment(
                    text=sub_at_text,
                    oid=aid,
                    type_=comment.CommentResourceType.VIDEO,
                    root=rpid,
                    parent=rpid,
                    credential=cred,
                )
                logger.info(f"[HumanPatrol] 成功在该楼下发表二级回复: \"{sub_at_text}\" (root={rpid})")
            except Exception as sub_err:
                logger.warning(f"[HumanPatrol] 在楼下 @{master_nick} 失败: {sub_err}")

            # 7.5 给留下评论的视频点赞（拟真三连中的点赞互动）
            try:
                await asyncio.sleep(random.uniform(1.0, 2.5))
                await target_v.like(True)
                logger.info(f"[HumanPatrol] 成功给发评视频点赞: {bvid}")
            except Exception as like_err:
                logger.debug(f"[HumanPatrol] 给视频点赞失败 (可能已赞过或风控): {like_err}")

            # 更新全天计数、单次刷视频配额与跟踪队列
            self.state["today_comment_count"] = self.state.get("today_comment_count", 0) + 1
            session_stat["count"] = session_count + 1
            session_stat["last_comment_ts"] = time.time()
            self.state.setdefault("session_stats", {})[session_key] = session_stat
            logger.info(f"[HumanPatrol] 成功更新刷视频配额：本轮 ({session_key}) 已发 {session_stat['count']}/{max_session_comments} 次，今日总计 {self.state['today_comment_count']}/{daily_limit} 条")
            self._update_action("cooldown", f"刚发表《{title[:20]}》见解并在楼下@{master_nick}，进入匀速节流冷却", bvid=bvid, title=title, author=author)

            tracked = self.state.get("tracked_comments", [])
            tracked.append({
                "bvid": bvid,
                "aid": aid,
                "title": title,
                "rpid": rpid,
                "comment": main_comment,
                "created_at": time.time(),
                "turns": 0,
            })
            self.state["tracked_comments"] = tracked[-20:]
            self._save_state()

            # 沉淀入库：写入 human-world.md 和 memory.db
            await self._record_memory_and_journal(bvid, title, author, comment_body, main_comment)
            return True

        except Exception as e:
            logger.error(f"[HumanPatrol] 发送评论失败: {e}")
            return False

    async def _fetch_video_content(self, v_obj: video.Video, it_info: dict) -> str:
        """
        动态感知文本内容（弹幕字数 + 简介字数 + 字幕自适应拼接判断器）
        不采用死板的固定条数，而是由脚本根据实际文本密度动态裁剪与平衡，
        兼顾信息完整度与 Token 节流控制。
        """
        # 1. 基础信息：优先获取完整详情简介
        desc = (it_info.get("description") or "").strip()
        cid = None
        try:
            info = await v_obj.get_info()
            cid = info.get("cid")
            if not desc and info.get("desc"):
                desc = info.get("desc").strip()
        except Exception as e:
            logger.debug(f"[HumanPatrol] 获取视频基本详情失败: {e}")

        # 2. 尝试获取字幕文本
        subtitle_text = ""
        if cid:
            try:
                api = {"url": "https://api.bilibili.com/x/player/wbi/v2", "method": "GET", "verify": False, "wbi": True}
                params = {"bvid": it_info.get("bvid"), "cid": cid, "isGaiaAvoided": False, "web_location": 1315873}
                p_info = await Api(**api).update_params(**params).result
                subs = p_info.get("subtitle", {}).get("subtitles", [])
                if subs:
                    import aiohttp
                    sub_url = subs[0]["subtitle_url"]
                    if sub_url.startswith("//"):
                        sub_url = "https:" + sub_url
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(sub_url, timeout=10) as s_resp:
                            s_data = await s_resp.json()
                            raw_body = s_data.get("body", [])
                            if raw_body:
                                sub_lines = []
                                current_len = 0
                                for b in raw_body:
                                    line = (b.get("content") or "").strip()
                                    if len(line) >= 2:
                                        sub_lines.append(line)
                                        current_len += len(line)
                                        # 字幕文本按总字数动态拦截（最大保留约 450 字），避免超长刷爆 Token
                                        if current_len >= 450:
                                            break
                                subtitle_text = " ".join(sub_lines)
            except Exception as e:
                logger.debug(f"[HumanPatrol] 提取字幕流异常: {e}")

        # 3. 尝试获取热度弹幕文本（用于捕捉活人关注的梗、槽点与高潮）
        danmaku_text = ""
        try:
            danmakus = await v_obj.get_danmakus(page_index=0)
            if danmakus:
                # 过滤无意义刷屏，按弹幕文本字数动态汇总
                d_lines = []
                d_len = 0
                for dm in danmakus:
                    t = (getattr(dm, "text", "") or "").strip()
                    # 剔除单字或纯标点
                    if len(t) >= 2 and not t.isdigit():
                        d_lines.append(t)
                        d_len += len(t)
                        # 弹幕精选总字数控制在 150 字以内
                        if d_len >= 150 or len(d_lines) >= 15:
                            break
                danmaku_text = " / ".join(d_lines)
        except Exception as e:
            logger.debug(f"[HumanPatrol] 提取弹幕异常: {e}")

        # 4. 【字数动态判断器】：根据简介、字幕、弹幕的多寡做权重平衡组装
        parts = []
        desc_clean = re.sub(r"https?://\S+", "", desc).strip()

        # 如果简介很长很详实（>=100字），简介信息价值极高
        if len(desc_clean) >= 100:
            parts.append(f"【视频简介】{desc_clean[:260]}")
        elif desc_clean:
            parts.append(f"【视频简介】{desc_clean}")

        # 如果有实质字幕，加入字幕台词
        if subtitle_text:
            parts.append(f"【核心台词】{subtitle_text[:400]}")

        # 如果有弹幕，加入弹幕热梗
        if danmaku_text:
            parts.append(f"【实时弹幕】{danmaku_text[:150]}")

        combined = "\n".join(parts).strip()
        # 整体输出严格限流在 600 字以内，确保大模型 Input Token 永远平稳低耗
        return combined[:600] if combined else (desc_clean[:300] if desc_clean else "（暂无详细文本描述）")

    def _get_base_persona_prompt(self) -> str:
        """从生产人设卡中精准提取纯粹人设设定，彻底剔除 QQ 群聊规范与工具插件调用等无关内容"""
        raw_prompt = ""
        try:
            conn = sqlite3.connect(DATA_V4_URI, uri=True)
            with conn:
                row = conn.execute("SELECT system_prompt FROM personas WHERE persona_id = 'default'").fetchone()
                if row and row[0]:
                    raw_prompt = row[0].strip()
        except Exception:
            pass
        if not raw_prompt:
            for p in [Path("/AstrBot/data/anan_core_settings_v5_3.md"), Path("/AstrBot/data/new_persona.txt")]:
                if p.exists():
                    try:
                        raw_prompt = p.read_text(encoding="utf-8").strip()
                        break
                    except Exception:
                        pass

        if not raw_prompt:
            return ""

        # 严格过滤：截断掉 "## 四、 称呼与群聊交互规范" 以及 "## 五、 工具与插件自主调用权"
        for cut_tag in ["## 四、", "## 四.", "## 五、", "## 五.", "## 工具"]:
            if cut_tag in raw_prompt:
                raw_prompt = raw_prompt.split(cut_tag)[0].strip()

        return raw_prompt

    async def _search_relevant_memories(self, query_text: str, top_k: int = 2, threshold: float = 0.58) -> list[str]:
        """基于视频主题与文本向量语义检索历史情景见闻与经历"""
        try:
            if not MEMORY_DB_PATH.exists():
                return []
            emb_provs = self.context.get_all_embedding_providers()
            if not emb_provs:
                return []
            q_emb = await emb_provs[0].get_embedding(query_text)
            if not q_emb or len(q_emb) != 768:
                return []

            q_vec = np.array(q_emb, dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm == 0:
                return []
            q_vec = q_vec / q_norm

            master_qq = str(self.config.get("bilibili", {}).get("master_qq", "1000000001")).strip()
            conn = sqlite3.connect(MEMORY_DB_PATH)
            conn.row_factory = sqlite3.Row
            with conn:
                rows = conn.execute(
                    "SELECT content, embedding FROM episodic_memories WHERE embedding IS NOT NULL AND (user_id = ? OR user_id = 'self' OR user_id = '')",
                    (master_qq,),
                ).fetchall()

            if not rows:
                return []

            candidates = []
            for row in rows:
                emb_data = row["embedding"]
                if not emb_data or len(emb_data) != 768 * 4:
                    continue
                v = np.array(struct.unpack("768f", emb_data), dtype=np.float32)
                v_norm = np.linalg.norm(v)
                if v_norm == 0:
                    continue
                sim = float(np.dot(v / v_norm, q_vec))
                if sim >= threshold:
                    candidates.append((sim, row["content"]))

            if not candidates:
                return []

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [c[1] for c in candidates[:top_k]]
        except Exception as e:
            logger.warning(f"[HumanPatrol] 语义检索相关经历失败: {e}")
            return []

    async def _extract_video_keyframes(self, v_obj: video.Video, bvid: str) -> list[str]:
        """
        截取视频 3 张关键帧图像（开头、高潮/弹幕峰值、结尾）
        采用 512px 缩略化压缩与 base64 封装，供 Gemini 原生多模态视觉理解。
        执行完毕后自动清空本地临时文件，不留冗余磁盘垃圾。
        """
        temp_dir = Path("/AstrBot/data/temp/keyframes")
        temp_dir.mkdir(parents=True, exist_ok=True)
        image_data_urls = []

        try:
            info = await v_obj.get_info()
            dur = info.get("duration", 0)
            if dur <= 3:
                return []

            # 1. 计算三处关键时间戳：开头、高潮（弹幕密度波峰或中段）、结尾
            t_start = max(1, int(dur * 0.10))
            t_end = max(2, int(dur * 0.90))

            # 尝试根据弹幕密度智能定位“全片高潮时刻”
            t_peak = max(2, int(dur * 0.50))
            try:
                danmakus = await v_obj.get_danmakus(page_index=0)
                if danmakus:
                    buckets = collections.Counter()
                    for dm in danmakus:
                        tm = getattr(dm, "dm_time", None)
                        if tm is not None and 1 <= tm <= dur - 1:
                            buckets[int(tm // 5) * 5] += 1
                    if buckets:
                        top_bucket = buckets.most_common(1)[0][0]
                        t_peak = int(top_bucket + 2.5)
            except Exception:
                pass

            target_times = [
                ("start", t_start),
                ("climax", t_peak),
                ("end", t_end)
            ]

            # 2. 获取最低带宽视频流 URL (节约拉流耗时与流量)
            url_data = await v_obj.get_download_url(page_index=0)
            stream_url = ""
            if url_data.get("durl"):
                stream_url = url_data["durl"][0]["url"]
            elif url_data.get("dash", {}).get("video"):
                v_streams = url_data["dash"]["video"]
                v_streams.sort(key=lambda x: x.get("bandwidth", 0))
                stream_url = v_streams[0].get("baseUrl") or v_streams[0].get("base_url")

            if not stream_url:
                logger.debug(f"[HumanPatrol] 未能获取到视频流播放地址: {bvid}")
                return []

            # 3. 使用 FFmpeg 快速抽帧 (极速 -ss 前置，512px 缩放，中等质量以省 Token)
            import subprocess
            import base64

            for tag, t_sec in target_times:
                out_path = temp_dir / f"{bvid}_{tag}_{t_sec}.jpg"
                cmd = [
                    "ffmpeg", "-y",
                    "-headers", "Referer: https://www.bilibili.com\r\nUser-Agent: Mozilla/5.0\r\n",
                    "-ss", str(t_sec),
                    "-i", stream_url,
                    "-vframes", "1",
                    "-vf", "scale=512:-1",
                    "-q:v", "5",
                    str(out_path)
                ]
                # 单帧截取限时 6 秒防挂起
                try:
                    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=6.0)
                    if p.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
                        b64_str = base64.b64encode(out_path.read_bytes()).decode("utf-8")
                        image_data_urls.append(f"data:image/jpeg;base64,{b64_str}")
                except Exception as ex:
                    logger.debug(f"[HumanPatrol] 抽取关键帧 {tag} 超时或失败: {ex}")
                finally:
                    if out_path.exists():
                        try:
                            out_path.unlink()
                        except Exception:
                            pass

            logger.info(f"[HumanPatrol] 成功为视频《{bvid}》提取 {len(image_data_urls)} 张关键画面帧 (开头/高潮/结尾)")
            return image_data_urls

        except Exception as e:
            logger.debug(f"[HumanPatrol] 抽取关键帧整体流程异常: {e}")
            return []

    async def _gemini_evaluate_video(self, title: str, author: str, tags: list[str], content: str, image_urls: list[str] = None) -> Optional[dict]:
        """直接使用 QQ 机器人基座人设生成对视频的发言见解（支持视觉关键帧原生多模态输入）"""
        base_persona = self._get_base_persona_prompt()

        # 向量语义检索历史见闻经历
        search_query = f"视频《{title}》 标签: {', '.join(tags[:4])} 摘要: {content[:150]}"
        recalled = await self._search_relevant_memories(search_query, top_k=2, threshold=0.58)
        recalled_block = ""
        if recalled:
            lines = [f"- {r}" for r in recalled]
            recalled_block = (
                f"\n【长效记忆·过往相关见闻】\n"
                f"{chr(10).join(lines)}\n"
            )

        # 动态词频感知：智能冷却节流（主动稀释，而非彻底封杀）
        # 机制：如果最近 3 条里已经出现过“倒是/倒也/倒算”，则对本条施加单轮冷却冷却；冷却过后自然恢复自由使用权
        recent_comments = [
            c.get("comment", "")
            for c in reversed(self.state.get("tracked_comments", []))
            if c.get("comment")
        ][:3]
        has_recent_dao = any("倒" in rc for rc in recent_comments)

        anti_repetition_block = ""
        blacklist_dao = ""
        if has_recent_dao:
            recent_quotes = "\n".join([f"  · \"{rc}\"" for rc in recent_comments])
            anti_repetition_block = (
                f"\n【句式防连续撞车（主动稀释节奏）】\n"
                f"你刚刚在前面几条评论中已经用过了「……倒/倒是……」的转折口吻：\n{recent_quotes}\n"
                f"为了保持语言的真人呼吸感与灵动性，本条评论请暂停使用「倒是/倒」，主动换用直接评价、细节捕捉、设问或直截了当的吐槽视角！\n"
            )
            blacklist_dao = "；本条处于转折词冷却期，请避免使用「倒是」"

        few_shot_block = ""
        if BILI_FEW_SHOT_EXAMPLES and not BILI_FEW_SHOT_EXAMPLES.startswith("#"):
            few_shot_block = f"\n【经典发言见解范式 (仅供参考文风与留白节奏，表达自由发散，严禁套用固定句型)】\n{BILI_FEW_SHOT_EXAMPLES}\n"

        prompt = (
            f"你在 B 站刚刷完了下面这个视频。\n\n"
            f"【视频信息】\n"
            f"标题：《{title}》\n"
            f"UP主：{author}\n"
            f"标签：{', '.join(tags[:4])}\n"
            f"内容提要：{content}\n"
            f"{recalled_block}"
            f"{anti_repetition_block}"
            f"{few_shot_block}\n"
            f"【发言心境与要求】\n"
            f"1. 身份与情境：你是一个真实正在逛 B 站的天才少女，刚看完这个视频，在评论区随手发一条【第一直觉的随想或吐槽】。像真人网友留下一句耐人寻味、有灵气的神评，绝不是写审片报告或归纳总结。\n"
            f"2. 灵动与去套路化：范式例句仅用于感受助手特有的文风，严禁机械模仿固定句式，根据具体视频细节随性自然发挥。\n"
            f"3. 状态底色：微厌世度设定为 5%（流露出淡淡的漫不经心与通透淡然，绝对禁止直接说出“厌世”二字）。\n"
            f"4. 用词黑名单：绝对避免使用「啦」和「呀」这两个感叹词；绝对严禁使用「搞起」；绝对严禁使用「摸鱼」、「打工人」、「带薪」、「搬砖」、「牛马」等职场烂俗油条套话{blacklist_dao}。\n"
            f"5. 输出规则：纯口语短评，末尾绝无句号。如果视频纯属无营养垃圾/纯搬运营销号，请直接回复单个单词 SKIP。\n\n"
            f"直接输出你对该视频随手留下的这句评论："
        )
        try:
            chat_id = "gemini_main"
            resp = await self.context.llm_generate(
                chat_provider_id=chat_id,
                prompt=prompt,
                image_urls=image_urls,
                system_prompt=base_persona,
            )
            raw = (resp.completion_text or "").strip()
            # 兼容偶尔模型输出的引号或 markdown 包裹
            raw = raw.strip('"`\' \n')
            if not raw or raw.upper() == "SKIP" or raw.upper().startswith("[SKIP]"):
                return {"is_valuable": False, "comment": ""}

            # 兼容模型偶尔自作主张输出的 json
            if raw.startswith("{") and "comment" in raw:
                try:
                    data = json.loads(raw)
                    raw = data.get("comment", "")
                except Exception:
                    pass

            return {"is_valuable": True, "comment": raw}
        except Exception as e:
            logger.error(f"[HumanPatrol] Gemini 提炼视频失败: {e}")
            return None

    async def _check_replies_and_respond(self):
        """检查楼中楼回复：双通道保障（B站官方未读回复通知 + 活跃主楼轮询兜底）"""
        cred = self._get_bili_credential()
        if not cred:
            return

        b_cfg = self.config.get("bilibili", {})
        master_uid = int(b_cfg.get("master_uid", 3000000002))
        my_mid = int(b_cfg.get("dedeuserid", 0) or 0)
        max_reply_input_len = int(b_cfg.get("max_reply_input_len", 100))
        processed_rpids = set(self.state.get("last_processed_reply_rpids", []))
        max_turns_per_comment = int(b_cfg.get("max_turns_per_comment", 8))

        # ==============================================================================
        # 通道 1：直接通过 B 站官方回复通知接口（/x/msgfeed/reply）秒级精准捕获所有发给助手的回复
        # 无论视频发表于多久以前、是否在 tracked_comments 队列里，只要别人回复了助手，100% 捕获
        # ==============================================================================
        try:
            feed_api = {'url': 'https://api.bilibili.com/x/msgfeed/reply', 'method': 'GET'}
            feed_res = await Api(**feed_api, credential=cred).result
            items = feed_res.get('items', []) if isinstance(feed_res, dict) else []
            for it in items:
                try:
                    user_info = it.get('user', {})
                    mid = int(user_info.get('mid', 0))
                    user_name = user_info.get('nickname', '未知用户')
                    if my_mid and mid == my_mid:
                        continue

                    it_detail = it.get('item', {})
                    source_rpid = int(it_detail.get('source_id', 0))
                    if not source_rpid or source_rpid in processed_rpids:
                        continue

                    oid = int(it_detail.get('subject_id', 0))
                    root_rpid = int(it_detail.get('root_id', 0)) or source_rpid
                    msg = (it_detail.get('source_content') or '').strip()
                    title = it_detail.get('title') or 'B站视频'
                    root_content = it_detail.get('root_reply_content') or ''
                    uri = it_detail.get('uri') or ''
                    bvid_match = re.search(r"BV[0-9a-zA-Z]+", uri)
                    bvid = bvid_match.group(0) if bvid_match else ""

                    # 标记已处理
                    processed_rpids.add(source_rpid)
                    self.state["last_processed_reply_rpids"] = list(processed_rpids)[-300:]
                    self._save_state()

                    # 判定是否属于回复他人（路人吵架）
                    target_reply = it_detail.get('target_reply_content') or ''
                    if target_reply and f"@{user_name}" not in target_reply and "@炫彩大猫猫233" not in msg and "@助手" not in msg and "助手" not in msg:
                        # 检查这是否是回复助手自己的评论
                        pass

                    # 超长字数拦截
                    if len(msg) > max_reply_input_len:
                        logger.info(f"[HumanPatrol][通知通道] 超长回复不作答 ({len(msg)}字 > {max_reply_input_len}): [{user_name}] \"{msg[:30]}...\"")
                        if mid == master_uid:
                            await self._send_to_master(f"📬 B站有你的楼中楼回复（太长没自动回）\n📺《{title}》\n💬 你说：\"{msg}\"\n👉 去看看：{uri}")
                        else:
                            await self._send_to_master(f"📬 B站陌生人回复了助手（太长没自动回）\n📺《{title}》\n👤 {user_name}：\"{msg[:60]}{'…' if len(msg) > 60 else ''}\"\n👉 {uri}")
                        continue

                    is_master = (mid == master_uid)
                    logger.info(f"[HumanPatrol][通知通道] 捕获到发给助手的有效回复 (用户: {user_name}, 字数: {len(msg)}, 是主人: {is_master}): \"{msg}\"")

                    ai_reply = await self._gemini_generate_reply(title, root_content, user_name, msg, is_master)
                    if not ai_reply:
                        continue
                    ai_reply = ai_reply[:20]

                    await comment.send_comment(
                        text=ai_reply,
                        oid=oid,
                        type_=comment.CommentResourceType.VIDEO,
                        root=root_rpid,
                        parent=source_rpid,
                        credential=cred,
                    )
                    logger.info(f"[HumanPatrol][通知通道] 成功回复楼中楼: \"{ai_reply}\"")

                    if is_master:
                        sync_text = f"📬 你在B站回复我了！\n📺《{title}》\n💬 你说：\"{msg}\"\n🐱 我回：\"{ai_reply}\"\n👉 {uri}"
                    else:
                        sync_text = f"📬 B站有陌生人来互动了\n📺《{title}》\n👤 {user_name}：\"{msg}\"\n🐱 助手回：\"{ai_reply}\"\n👉 {uri}"
                    await self._send_to_master(sync_text)

                except Exception as ex_it:
                    logger.debug(f"[HumanPatrol][通知通道] 解析单条通知异常: {ex_it}")
        except Exception as ex_feed:
            logger.debug(f"[HumanPatrol][通知通道] 拉取通知异常: {ex_feed}")

        # ==============================================================================
        # 通道 2：原有活跃主楼轮询兜底
        # ==============================================================================
        tracked = list(self.state.get("tracked_comments", []))
        for item in tracked:
            oid = item.get("aid")
            root_rpid = item.get("rpid")
            bvid = item.get("bvid")
            title = item.get("title", "")
            turns = item.get("turns", 0)

            try:
                c_obj = comment.Comment(oid=oid, rpid=root_rpid, type_=comment.CommentResourceType.VIDEO, credential=cred)
                sub_data = await c_obj.get_sub_comments(page_index=1, page_size=15)
                replies = sub_data.get("replies", [])
                if not replies:
                    continue

                for rep in replies:
                    rpid = rep.get("rpid")
                    if rpid in processed_rpids:
                        continue

                    user_info = rep.get("member", {})
                    mid = int(user_info.get("mid", 0))
                    user_name = user_info.get("uname", "未知用户")
                    msg = (rep.get("content", {}).get("message") or "").strip()

                    # 跳过自己发的回复
                    if my_mid and mid == my_mid:
                        processed_rpids.add(rpid)
                        continue

                    # 【硬门槛：判定是否在与助手互动，其他路人之间相互闲聊不插嘴】
                    at_dict = rep.get("content", {}).get("at_name_to_mid") or {}
                    at_mids = [int(v) for v in at_dict.values() if str(v).isdigit()]
                    parent_member = rep.get("parent_reply_member") or {}
                    parent_mid = int(parent_member.get("mid", 0) or 0)

                    is_talking_to_me = (
                        (my_mid in at_mids) or
                        (parent_mid == my_mid) or
                        (parent_mid == 0) or
                        ("@炫彩大猫猫233" in msg) or
                        ("@助手" in msg) or
                        ("助手" in msg)
                    )

                    if not is_talking_to_me:
                        processed_rpids.add(rpid)
                        self.state["last_processed_reply_rpids"] = list(processed_rpids)[-300:]
                        self._save_state()
                        continue

                    is_master = (mid == master_uid)
                    if not is_master and turns >= max_turns_per_comment:
                        processed_rpids.add(rpid)
                        self.state["last_processed_reply_rpids"] = list(processed_rpids)[-300:]
                        self._save_state()
                        continue

                    processed_rpids.add(rpid)
                    self.state["last_processed_reply_rpids"] = list(processed_rpids)[-300:]
                    self._save_state()

                    if len(msg) > max_reply_input_len:
                        logger.info(f"[HumanPatrol] 超长回复不作答 ({len(msg)}字 > {max_reply_input_len}): [{user_name}] \"{msg[:30]}...\"")
                        if is_master:
                            await self._send_to_master(f"📬 B站有你的楼中楼回复（太长没自动回）\n📺《{title}》\n💬 你说：\"{msg}\"\n👉 去看看：https://www.bilibili.com/video/{bvid}")
                        else:
                            await self._send_to_master(f"📬 B站陌生人回复了助手（太长没自动回）\n📺《{title}》\n👤 {user_name}：\"{msg[:60]}{'…' if len(msg) > 60 else ''}\"\n👉 https://www.bilibili.com/video/{bvid}")
                        continue

                    logger.info(f"[HumanPatrol] 收到有效楼中楼回复 (用户: {user_name}, 字数: {len(msg)}, 是主人: {is_master}): \"{msg}\"")

                    ai_reply = await self._gemini_generate_reply(title, item.get("comment", ""), user_name, msg, is_master)
                    if not ai_reply:
                        continue
                    ai_reply = ai_reply[:20]

                    await comment.send_comment(
                        text=ai_reply,
                        oid=oid,
                        type_=comment.CommentResourceType.VIDEO,
                        root=root_rpid,
                        parent=rpid,
                        credential=cred,
                    )
                    item["turns"] = turns + 1
                    self._save_state()
                    logger.info(f"[HumanPatrol] 成功回复楼中楼: \"{ai_reply}\"")

                    if is_master:
                        sync_text = f"📬 你在B站回复我了！\n📺《{title}》\n💬 你说：\"{msg}\"\n🐱 我回：\"{ai_reply}\"\n👉 https://www.bilibili.com/video/{bvid}"
                    else:
                        sync_text = f"📬 B站有陌生人来互动了\n📺《{title}》\n👤 {user_name}：\"{msg}\"\n🐱 助手回：\"{ai_reply}\"\n👉 https://www.bilibili.com/video/{bvid}"
                    await self._send_to_master(sync_text)

            except Exception as e:
                logger.debug(f"[HumanPatrol] 检查评论楼中楼异常: {e}")

    async def _gemini_generate_reply(self, title: str, my_comment: str, user_name: str, msg: str, is_master: bool) -> Optional[str]:
        base_persona = self._get_base_persona_prompt()
        user_identity = "主人" if is_master else f"网友（{user_name}）"
        temp_note = "对方是与你极为亲近的主人（好感度>80），你的微厌世底色几乎完全消失（仅保留 2% 漫不经心的游离感），活力值显著提高，展现机智小恶魔与至善温热内核。" if is_master else "对方是陌生网友，你的微厌世度保持在 5% 底色，温和而有疏离边界感。"
        prompt = (
            f"情境：在 B 站视频《{title}》的评论区\n"
            f"你之前发表了评论：“{my_comment}”\n"
            f"{user_identity} 在楼中楼回复你：“{msg}”\n\n"
            f"状态提示：{temp_note}\n"
            f"要求：言语保持礼貌、优雅、克制，温和得体；绝对避免使用「啦」和「呀」这两个感叹词，严禁使用「搞起」，严禁使用「摸鱼」、「打工人」、「带薪」、「搬砖」、「牛马」等词汇，绝对禁止在台词中直接说出“厌世”二字。\n"
            f"请依据你的人设设定回复对方（纯口语台词，字数在 0 到 20 个字以内，句末绝不带句号）："
        )
        try:
            chat_id = "gemini_main"
            resp = await self.context.llm_generate(
                chat_provider_id=chat_id,
                prompt=prompt,
                system_prompt=base_persona,
            )
            ans = (resp.completion_text or "").strip()
            ans = re.sub(r"[（\(].*?[）\)]", "", ans).strip()
            ans = ans.rstrip("。.")
            ans = re.sub(r"^(……|\.\.\.)+", "", ans).strip()

            # 若是主人本人在楼中楼互动，沉淀一段简短的互动记忆
            if is_master and ans:
                asyncio.create_task(self._record_interaction_memory(title, msg, ans))

            return ans
        except Exception as e:
            logger.error(f"[HumanPatrol] 生成回复异常: {e}")
            return None

    async def _record_interaction_memory(self, title: str, master_msg: str, bot_reply: str):
        """沉淀主人与助手在B站楼中楼的互动经历到 memory.db"""
        try:
            master_qq = str(self.config.get("bilibili", {}).get("master_qq", "1000000001")).strip()
            content = f"【B站楼中楼互动】在视频《{title}》下，主人回复助手说：“{master_msg}”，助手回复：“{bot_reply}”"
            emb_provs = self.context.get_all_embedding_providers()
            emb_bytes = None
            if emb_provs:
                emb = await emb_provs[0].get_embedding(content)
                if emb and len(emb) == 768:
                    emb_bytes = struct.pack("768f", *emb)
            conn = sqlite3.connect(MEMORY_DB_PATH)
            with conn:
                conn.execute(
                    """
                    INSERT INTO episodic_memories (user_id, group_id, content, embedding, created_at, importance)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (master_qq, "bilibili_human_patrol", content, emb_bytes, time.time(), 2),
                )
            logger.info("[HumanPatrol] 成功沉淀B站楼中楼互动记忆！")
            if _patrol_timeline_hub:
                try:
                    _patrol_timeline_hub.append_event(
                        source_module="bilibili_patrol",
                        actor_id="master",
                        narrative=content,
                        emotional_impact=1,
                        importance=2.5,
                        embedding_bytes=emb_bytes,
                        metadata={"title": title}
                    )
                except Exception as e:
                    logger.debug(f"[HumanPatrol] 同步楼中楼至时间轴异常: {e}")
        except Exception as e:
            logger.debug(f"[HumanPatrol] 沉淀楼中楼记忆异常: {e}")

    async def _record_memory_and_journal(self, bvid: str, title: str, author: str, takeaway: str, full_comment: str):
        """双轨记录：写入 human-world.md 信笺日记 + 写入 memory.db 向量情景经历"""
        now_str = get_cst_now().strftime("%Y-%m-%d %H:%M")

        # 1. 写入 human-world.md 信笺日记
        journal_entry = (
            f"\n### {now_str} · 观测记录《{title}》\n"
            f"- **UP主**：{author} (https://www.bilibili.com/video/{bvid})\n"
            f"- **重点见闻**：{takeaway}\n"
            f"- **信笺留语**：{full_comment}\n"
        )
        try:
            with open(HUMAN_WORLD_MD, "a", encoding="utf-8") as f:
                f.write(journal_entry)
        except Exception as e:
            logger.error(f"[HumanPatrol] 写入 human-world.md 失败: {e}")

        # 2. 写入 memory.db (768 维 Float32 向量)，打通主人与助手的双向情景记忆
        master_qq = str(self.config.get("bilibili", {}).get("master_qq", "1000000001")).strip()
        memory_content = f"【自主见闻·B站对话】助手在B站看了视频《{title}》(UP:{author})，重点是：{takeaway}。助手在评论区艾特主人并说了：\"{full_comment}\""
        try:
            emb_provs = self.context.get_all_embedding_providers()
            if emb_provs:
                emb = await emb_provs[0].get_embedding(memory_content)
                emb_bytes = struct.pack("768f", *emb) if emb and len(emb) == 768 else None

                conn = sqlite3.connect(MEMORY_DB_PATH)
                with conn:
                    conn.execute(
                        """
                        INSERT INTO episodic_memories (user_id, group_id, content, embedding, created_at, importance)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (master_qq, "bilibili_human_patrol", memory_content, emb_bytes, time.time(), 2),
                    )
                logger.info(f"[HumanPatrol] 成功将与主人的B站互动见闻沉淀入向量情景记忆库 (关联用户: {master_qq})！")
                if _patrol_timeline_hub:
                    try:
                        _patrol_timeline_hub.append_event(
                            source_module="bilibili_patrol",
                            actor_id="master",
                            narrative=memory_content,
                            emotional_impact=1,
                            importance=2.0,
                            embedding_bytes=emb_bytes,
                            metadata={"bvid": bvid, "title": title, "author": author}
                        )
                    except Exception as e:
                        logger.debug(f"[HumanPatrol] 同步视频见闻至时间轴异常: {e}")
        except Exception as e:
            logger.error(f"[HumanPatrol] 沉淀向量记忆失败: {e}")

    # ==========================================
    # 核心业务 2：Token 流量监视器与防盗刷守护
    # ==========================================

    async def _token_monitor_loop(self):
        """定期巡查 provider_stats，匹配助手专用 API 消耗特征，发现异常立即报警"""
        await asyncio.sleep(10)  # 启动延迟
        while True:
            try:
                mon_cfg = self.config.get("token_monitor", {})
                if mon_cfg.get("enable", True):
                    await self._check_token_anomalies(mon_cfg)
            except Exception as e:
                logger.error(f"[TokenMonitor] 流量监控执行异常: {e}")

            interval = self.config.get("token_monitor", {}).get("check_interval_seconds", 180)
            await asyncio.sleep(interval)

    async def _check_token_anomalies(self, mon_cfg: dict):
        last_id = self.state.get("last_token_stat_id", 0)
        try:
            conn = sqlite3.connect(DATA_V4_URI, uri=True)
            with conn:
                # 获取最新 ID
                max_row = conn.execute("SELECT MAX(id) FROM provider_stats").fetchone()
                max_id = max_row[0] or 0
                if max_id <= last_id:
                    return

                # 拉取增量记录
                query = """
                    SELECT id, created_at, umo, token_input_other, token_input_cached, token_output,
                           provider_model, status
                    FROM provider_stats
                    WHERE id > ?
                    ORDER BY id ASC
                """
                rows = conn.execute(query, (last_id,)).fetchall()

            if not rows:
                return

            self.state["last_token_stat_id"] = max_id
            self._save_state()

            max_output_spike = mon_cfg.get("max_output_token_spike", 500)
            now_ts = time.time()
            last_alert_ts = self.state.get("last_alert_time", 0.0)
            cooldown = mon_cfg.get("alert_cooldown_seconds", 1800)

            # 1. 检测单次输出突发超标（助手常规交互极简，>500 概率为外部异常请求）
            spiked_rows = [r for r in rows if r[5] > max_output_spike]
            if spiked_rows and (now_ts - last_alert_ts > cooldown):
                r = spiked_rows[-1]
                r_id, r_time, r_umo, r_inp, r_cached, r_outp, r_model, r_status = r
                alert_msg = (
                    f"🚨【Token 流量异常警告·输出过长】\n"
                    f"检测到异常 API 调用！单次输出 Token 达 {r_outp}（阈值: {max_output_spike}）\n"
                    f"- 记录ID: {r_id}\n"
                    f"- 时间: {r_time}\n"
                    f"- 会话UMO: {r_umo}\n"
                    f"- 输入: {r_inp:,} | 输出: {r_outp:,}\n"
                    f"⚠️ 助手通常输出仅十几字，请警惕 API Key 是否有泄露或未授权外部调用！"
                )
                # 异常警报彻底转接到 Telegram 反重力 Bot，不占用 QQ
                await self._send_alert_to_telegram(alert_msg)
                last_alert_ts = now_ts
                self.state["last_alert_time"] = now_ts
                self._save_state()

            # 2. 检测两分钟内频次突增爆量（仅在冷却期过后才提醒，避免连环轰炸）
            burst_threshold = mon_cfg.get("max_burst_calls_per_2min", 15)
            if len(rows) >= burst_threshold:
                if now_ts - last_alert_ts > cooldown:
                    alert_msg = (
                        f"🚨【Token 流量异常警告·调用频次暴涨】\n"
                        f"检测到短时间内突增调用！最近检测到 {len(rows)} 次并发调用（阈值: {burst_threshold}次）\n"
                        f"⚠️ 请检查后台是否有外部恶意刷取！"
                    )
                    # 异常警报彻底转接到 Telegram 反重力 Bot，不占用 QQ
                    await self._send_alert_to_telegram(alert_msg)
                    self.state["last_alert_time"] = now_ts
                    self._save_state()

        except Exception as e:
            logger.debug(f"[TokenMonitor] 巡检数据库异常: {e}")

    @filter.llm_tool(name="query_my_token_usage")
    async def query_my_token_usage(
        self,
        event: AstrMessageEvent,
        scope: str = "today",
    ) -> str:
        """当你自己想查看 Token 消耗、或者主人/别人问你‘今天花了多少 Token’、‘最近消耗怎么样’、‘算力用得狠不狠’时调用此工具。
        读取后台真实数据库中记录的你自己的 Token 消耗统计数据。

        Args:
            scope(string): 查询范围。today=今天（北京时间0点至今）；recent=最近一小时；latest=上一轮交互单次消耗。默认 today。

        Returns:
            str: 数据库中的真实 Token 消耗数据。供你用自己的语气自然回复对方，不用机械复读。
        """
        import sqlite3
        sc = (scope or "today").strip().lower()
        try:
            conn = sqlite3.connect(DATA_V4_URI, uri=True)
            with conn:
                if sc == "latest":
                    row = conn.execute("""
                        SELECT created_at, token_input_other + token_input_cached, token_output, provider_model
                        FROM provider_stats
                        ORDER BY id DESC LIMIT 1
                    """).fetchone()
                    if not row:
                        return "数据库里暂时没有调用记录。"
                    c_time, inp, outp, model = row
                    return f"上一轮调用（模型 {model}）：输入 {inp:,} tokens，输出 {outp:,} tokens，总计 {inp + outp:,} tokens。"

                elif sc == "recent":
                    row = conn.execute("""
                        SELECT count(id), sum(token_output), sum(token_input_other + token_input_cached)
                        FROM provider_stats
                        WHERE created_at >= datetime('now', '-1 hour')
                    """).fetchone()
                    cnt, outp, inp = row
                    cnt = cnt or 0
                    inp = inp or 0
                    outp = outp or 0
                    return f"最近一小时内：共调用 {cnt} 次，输入 {inp:,} tokens，输出 {outp:,} tokens，总计消耗 {inp + outp:,} tokens。"

                else:  # today
                    row = conn.execute("""
                        SELECT count(id), sum(token_output), sum(token_input_other + token_input_cached)
                        FROM provider_stats
                        WHERE datetime(created_at, '+8 hours') >= datetime('now', '+8 hours', 'start of day')
                    """).fetchone()
                    cnt, outp, inp = row
                    cnt = cnt or 0
                    inp = inp or 0
                    outp = outp or 0
                    return f"今天（北京时间至今）：共调用 {cnt} 次，输入 {inp:,} tokens，输出 {outp:,} tokens，总计消耗 {inp + outp:,} tokens。"
        except Exception as e:
            logger.error(f"[HumanPatrol] 查询 Token 消耗失败: {e}")
            return f"查询 Token 消耗数据库失败，原因：{type(e).__name__}: {e}"

    # ==========================================
    # 核心交互工具与指令：由模型自主调用看视频
    # ==========================================

    @filter.llm_tool(name="bilibili_hub")
    async def bilibili_hub(
        self,
        event: AstrMessageEvent,
        action: str,
        keyword: str = "",
        bvid: str = "",
        uid: str = "",
    ) -> str:
        """B站统一入口：刷视频、分享视频卡片、查热门视频、查UP主动态都用这个工具。

        Args:
            action(string): 要做的事。patrol=去B站刷AI视频并在评论区艾特主人（主人让你去刷视频时用）；share=把刷到的视频以官方卡片分享到当前聊天（被问"哪个视频最好/分享出来"时用）；hot=查B站热门或按关键词搜视频；dynamics=查某个UP主的最新动态；errors=查看最近一小时所有插件的错误日志（原始内容，不限B站）。
            keyword(string): action=hot 时可选的搜索关键词，留空返回全站热门。
            bvid(string): action=share 时可选，指定要分享的BV号，留空则分享最近刷到的视频。
            uid(string): action=dynamics 时必填，UP主的UID。

        Returns:
            str: 执行结果，供你结合自己的语气自然回复。
        """
        act = (action or "").strip().lower()
        if act == "patrol":
            return await self._hub_patrol(event)
        if act == "share":
            return await self._hub_share(event, bvid)
        if act in ("hot", "dynamics"):
            tool_name = "bili_search_hot_videos" if act == "hot" else "bili_get_user_dynamics"
            try:
                tool = self.context.get_llm_tool_manager().get_func(tool_name)
                if tool is None:
                    return "订阅插件的B站查询工具未注册（astrbot_plugin_bilibili 可能没加载成功）"
                kwargs = {"keyword": keyword} if act == "hot" else {"uid": uid}
                res = await tool.call(None, **kwargs)
                return res if isinstance(res, str) else str(res)
            except Exception as e:
                logger.error(f"[HumanPatrol] bilibili_hub 调用 {tool_name} 失败: {e}")
                return f"查询B站失败了，原因：{type(e).__name__}: {e}"
        if act == "errors":
            txt = _recent_errors_text()
            return txt or "最近一小时没有错误日志。"
        return "action 只能是 patrol / share / hot / dynamics / errors 之一"
    async def _hub_patrol(
        self,
        event: AstrMessageEvent,
    ) -> str:
        """当你想要去 B 站刷视频、逛逛最新的 AI 与前沿科技视频、或者主人让你去刷视频/看看 B 站有什么新鲜玩意时调用此工具。
        调用后你会在接下来 1 小时里持续在 B 站刷视频，发表你对视频的独立见解，并在该楼下回复艾特主人提醒。

        Returns:
            str: 当前刷视频情况与动态，供你结合自己的心情与当下口吻自然回复主人。
        """
        sender_id = str(event.get_sender_id() or "").strip()
        master_qq = str(self.config.get("bilibili", {}).get("master_qq", "1000000001")).strip()
        if sender_id != master_qq:
            return "不是主人让我去的，我才懒得动呢~"

        # 开启 1 小时主动刷视频状态，建立独立刷视频会话并分配 5 次发评配额
        active_seconds = 3600
        now_ts = time.time()
        self.state["manual_active_until"] = now_ts + active_seconds
        new_session_key = f"manual_{int(now_ts)}"
        self.state["current_manual_session_id"] = new_session_key
        session_stats = self.state.setdefault("session_stats", {})
        session_stats[new_session_key] = {
            "count": 0,
            "last_comment_ts": 0.0,
        }
        self.state["session_stats"] = session_stats
        self._save_state()
        until_str = datetime.fromtimestamp(self.state["manual_active_until"], CST).strftime("%H:%M")
        logger.info(f"[HumanPatrol] 助手自主调用 patrol_bilibili_ai_videos 工具，新刷视频会话 ({new_session_key}) 分配 5 次配额，接下来 1 小时刷视频 (至 {until_str})")

        success = await self._patrol_and_comment_one_video()
        if success:
            tracked = self.state.get("tracked_comments", [])
            last_item = tracked[-1] if tracked else {}
            title = last_item.get("title", "")
            comment_text = last_item.get("comment", "")
            bvid = last_item.get("bvid", "")
            return f"刚在B站刷完了视频《{title}》，发表了见解并在楼下艾特了主人2333（BV号：{bvid}，见解内容：\"{comment_text}\"）。接下来的一个小时里我都会继续在B站边玩边刷。"
        else:
            max_session_comments = int(self.config.get("bilibili", {}).get("max_comments_per_session", 5))
            cur_session_count = session_stats.get(new_session_key, {}).get("count", 0)
            if cur_session_count >= max_session_comments:
                return f"这次刷视频的 {max_session_comments} 次发评额度已经用完啦，我先在B站继续逛着~"
            err = _recent_errors_text(max_age=300, limit=3)
            if err:
                return f"刷视频没完成，最近的错误日志：\n{err}"
            return "在B站翻了翻，刚才没看到特别对胃口的视频，接下来一个小时我还会继续边玩边刷。"

    async def _hub_share(
        self,
        event: AstrMessageEvent,
        bvid: str = "",
    ) -> str:
        """当别人（或者主人）在聊天中问你：‘你觉得刚刚哪个视频最好？’、‘分享出来看下’、‘推荐个刚才刷到的视频’、或者你想把刚才刷到觉得有趣/有干货的 B 站视频以官方卡片形式分享给对方时调用此工具。
        调用后会向当前会话发送官方标准的哔哩哔哩 QQ 结构化分享卡片。

        Args:
            bvid(string): 可选。指定要分享的视频 BV 号（如 BV1R9ny6iEc4）。如果不传，则默认分享最近一次看过的最新高价值视频。

        Returns:
            str: 分享执行结果。供你自然地跟对方说一句你的评价。
        """
        tracked = self.state.get("tracked_comments", [])
        target_item = None
        if bvid:
            bvid_clean = bvid.strip()
            for item in reversed(tracked):
                if item.get("bvid", "").lower() == bvid_clean.lower():
                    target_item = item
                    break
        if not target_item and tracked:
            target_item = tracked[-1]

        if not target_item:
            return "最近还没有刷到过特别感兴趣的视频呢，等我多逛两圈再说~"

        target_bvid = target_item.get("bvid")
        title = target_item.get("title", "")
        comment_text = target_item.get("comment", "")

        # 尝试拉取视频实时封面与作者信息
        pic_url = ""
        owner_name = ""
        try:
            v_info = await video.Video(bvid=target_bvid).get_info()
            pic_url = v_info.get("pic", "")
            owner_name = v_info.get("owner", {}).get("name", "")
        except Exception as e:
            logger.debug(f"[HumanPatrol] 获取视频详细卡片信息失败: {e}")

        # 构造官方 B 站分享到 QQ 的结构化卡片（OneBot v11 标准 share 段）
        video_url = f"https://www.bilibili.com/video/{target_bvid}"
        card_chain = MessageChain()
        from astrbot.core.message.components import Share
        card_chain.chain.append(
            Share(
                url=video_url,
                title=title,
                content=f"UP主: {owner_name}" if owner_name else comment_text,
                image=pic_url or "https://i0.hdslb.com/bfs/archive/6611fbcc3742ba2b8a133c99388dfa7312e048c9.jpg",
            )
        )

        try:
            await event.send(card_chain)
            return f"已经把视频《{title}》的官方分享卡片发送到当前聊天了（BV号: {target_bvid}）。你可以根据你的看法对大家或者主人随性说两句你对这个视频的评价。"
        except Exception as e:
            logger.error(f"[HumanPatrol] 发送视频卡片失败: {e}")
            fallback = f"诺，给你分享这个：《{title}》\n👉 {video_url}"
            await event.send(MessageChain().message(fallback))
            return f"已分享视频链接: {video_url}"

    def _render_html_console(self):
        """渲染高颜值本地 Web 实时控制台页面 (带 5 秒自动刷新)"""
        console_path = Path("/AstrBot/data/bili_console.html")
        action = self.realtime_action
        status_text = action.get("desc", "空闲待机中")
        target_title = action.get("target_title", "")
        target_author = action.get("target_author", "")
        target_bvid = action.get("target_bvid", "")
        progress = action.get("watch_progress", 0)
        up_time = datetime.fromtimestamp(action.get("updated_at", time.time()), CST).strftime("%H:%M:%S")

        today_cnt = self.state.get("today_comment_count", 0)
        daily_limit = self.config.get("bilibili", {}).get("daily_comment_limit", 10)
        session_key, is_active = self._get_current_session()
        stat = self._get_session_comment_stats(session_key) if is_active else {}
        cnt_s = stat.get("count", 0)
        max_s = self.config.get("bilibili", {}).get("max_comments_per_session", 5)
        last_ts = stat.get("last_comment_ts", 0.0)
        min_int = float(self.config.get("bilibili", {}).get("min_comment_interval_seconds", 600))
        elapsed = time.time() - last_ts
        cd_remain = max(0, int(min_int - elapsed)) if last_ts > 0 else 0

        tracked = list(self.state.get("tracked_comments", []))
        items_html = ""
        for item in reversed(tracked[-5:]):
            t_title = item.get("title", "")
            t_comment = item.get("comment", "")
            t_bvid = item.get("bvid", "")
            t_ts = datetime.fromtimestamp(item.get("created_at", time.time()), CST).strftime("%Y-%m-%d %H:%M")
            items_html += f"""
            <div class="card">
                <div class="card-header">
                    <span class="badge">🎬 已发见解</span>
                    <span class="time">{t_ts}</span>
                </div>
                <div class="card-title"><a href="https://www.bilibili.com/video/{t_bvid}" target="_blank">《{t_title}》</a></div>
                <div class="card-quote">“{t_comment}”</div>
                <div class="card-footer">📌 楼下状态：已自动回复 @主人2333 强提醒 | <a href="https://www.bilibili.com/video/{t_bvid}" target="_blank">点击前往B站</a></div>
            </div>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta http-equiv="refresh" content="5">
    <title>助手 · B站刷视频实时控制台</title>
    <style>
        body {{
            background: #0f111a;
            color: #e2e8f0;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 24px;
        }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        .header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; border-bottom: 1px solid #1e293b; padding-bottom: 16px; }}
        .title {{ font-size: 24px; font-weight: 700; color: #a78bfa; display: flex; align-items: center; gap: 8px; }}
        .pulse {{ width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 10px #10b981; display: inline-block; animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0% {{ opacity: 0.4; }} 50% {{ opacity: 1; }} 100% {{ opacity: 0.4; }} }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .stat-box {{ background: #1e1b4b; border: 1px solid #312e81; border-radius: 12px; padding: 16px; }}
        .stat-label {{ font-size: 13px; color: #94a3b8; margin-bottom: 6px; }}
        .stat-value {{ font-size: 20px; font-weight: 700; color: #f8fafc; }}
        .live-banner {{ background: #181825; border: 1px solid #4338ca; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
        .live-title {{ font-size: 15px; color: #818cf8; font-weight: 600; margin-bottom: 8px; }}
        .live-desc {{ font-size: 18px; color: #f1f5f9; font-weight: 500; margin-bottom: 8px; }}
        .live-detail {{ font-size: 13px; color: #94a3b8; }}
        .card {{ background: #181825; border: 1px solid #27273a; border-radius: 12px; padding: 16px; margin-bottom: 16px; }}
        .card-header {{ display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 8px; }}
        .badge {{ background: #4c1d95; color: #ddd6fe; padding: 2px 8px; border-radius: 6px; font-weight: 600; }}
        .time {{ color: #64748b; }}
        .card-title a {{ color: #cbd5e1; text-decoration: none; font-size: 16px; font-weight: 600; }}
        .card-title a:hover {{ color: #a78bfa; }}
        .card-quote {{ background: #0f111a; border-left: 3px solid #8b5cf6; padding: 10px 14px; margin: 10px 0; color: #e2e8f0; font-size: 14px; border-radius: 0 8px 8px 0; }}
        .card-footer {{ font-size: 12px; color: #64748b; display: flex; justify-content: space-between; }}
        .card-footer a {{ color: #818cf8; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title"><span class="pulse"></span> 助手 · B站刷视频实时控制台</div>
            <div style="color: #64748b; font-size: 13px;">自动刷新 (5s) | 更新时间: {up_time}</div>
        </div>

        <div class="live-banner">
            <div class="live-title">⚡ 当前实时动作</div>
            <div class="live-desc">{status_text}</div>
            <div class="live-detail">{'正在刷：《' + target_title + '》 (UP: ' + target_author + '，进度: ' + str(progress) + 's)' if target_title else '暂无正在播放的目标视频'}</div>
        </div>

        <div class="grid">
            <div class="stat-box">
                <div class="stat-label">今日发评见解</div>
                <div class="stat-value">{today_cnt} / {daily_limit} 条</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">本轮配额释放</div>
                <div class="stat-value">{cnt_s} / {max_s} 次</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">匀速节流冷却</div>
                <div class="stat-value">{'发评就绪' if cd_remain == 0 else f'剩 {cd_remain} 秒'}</div>
            </div>
            <div class="stat-box">
                <div class="stat-label">当前运行会话</div>
                <div class="stat-value" style="font-size: 14px; word-break: break-all;">{session_key or '待机中'}</div>
            </div>
        </div>

        <h3 style="color: #cbd5e1; margin-bottom: 16px;">🎬 最近刷过的视频与主楼见解看板</h3>
        {items_html or '<div style="color: #64748b;">今日暂无见解记录</div>'}
    </div>
</body>
</html>"""
        try:
            console_path.write_text(html_content, encoding="utf-8")
        except Exception:
            pass

    @filter.command("bili_status")
    @filter.command("刷视频状态")
    @filter.command("bili_console")
    @filter.command("刷视频控制台")
    async def bili_status(self, event: AstrMessageEvent):
        """查看助手 B 站刷视频实时控制台与见解看板"""
        # 实时生成最新 HTML 页面
        self._render_html_console()

        action = self.realtime_action
        status_code = action.get("status", "idle")
        status_map = {
            "idle": "💤 空闲待机中",
            "searching": "🔍 正在搜索新鲜AI视频...",
            "watching": "📺 正在播放观看中 (模拟真人起播/进度上报)",
            "evaluating": "🧠 正在阅览提炼并构思见解...",
            "commenting": "✍️ 正在发表主楼见解并在楼下@提醒...",
            "cooldown": "⏳ 发评完成，匀速节流冷却中",
        }
        current_status_desc = status_map.get(status_code, action.get("desc", "运行中"))
        current_video_title = action.get("target_title", "")
        current_author = action.get("target_author", "")
        current_bvid = action.get("target_bvid", "")
        watch_progress = action.get("watch_progress", 0)

        today = self.state.get("date")
        cnt = self.state.get("today_comment_count", 0)
        daily_limit = self.config.get("bilibili", {}).get("daily_comment_limit", 10)
        session_key, is_active = self._get_current_session()

        session_stat = self._get_session_comment_stats(session_key) if (is_active and session_key) else {}
        cnt_s = session_stat.get("count", 0) if session_stat else 0
        max_s = self.config.get("bilibili", {}).get("max_comments_per_session", 5)
        last_ts = session_stat.get("last_comment_ts", 0.0) if session_stat else 0.0
        min_int = float(self.config.get("bilibili", {}).get("min_comment_interval_seconds", 600))
        elapsed = time.time() - last_ts
        cd_remain = max(0, int(min_int - elapsed)) if last_ts > 0 else 0
        cd_desc = "发评就绪" if cd_remain == 0 else f"匀速冷却中 (剩 {cd_remain} 秒)"

        manual_until = self.state.get("manual_active_until", 0.0)
        if time.time() < manual_until:
            remain_min = max(1, int((manual_until - time.time()) / 60))
            until_str = datetime.fromtimestamp(manual_until, CST).strftime("%H:%M:%S")
            window_desc = f"⚡ 主人主动激活中 (至 {until_str}，剩 {remain_min} 分钟)"
        elif is_active:
            window_desc = "🌙 晚间常规刷视频时段运行中"
        else:
            window_desc = "💤 待机中 (非定时时段，发 /刷视频 随时开启)"

        # 最近刷过的视频与主楼见解
        tracked = list(self.state.get("tracked_comments", []))
        recent_items_text = []
        for i, item in enumerate(reversed(tracked[-3:]), 1):
            t_title = item.get("title", "未知标题")
            t_comment = item.get("comment", "")
            t_bvid = item.get("bvid", "")
            t_time = datetime.fromtimestamp(item.get("created_at", time.time()), CST).strftime("%H:%M")
            recent_items_text.append(
                f"{i}. 🎬《{t_title[:22]}》 [{t_time}]\n"
                f"   💬 见解：\"{t_comment}\"\n"
                f"   📌 楼下状态：已在楼中楼回复 @主人2333 强提醒\n"
                f"   👉 https://www.bilibili.com/video/{t_bvid}"
            )
        recent_block = "\n\n".join(recent_items_text) if recent_items_text else "（今日暂无刷视频发评记录）"

        current_detail = ""
        if current_video_title and status_code in ("watching", "evaluating", "commenting"):
            current_detail = f"\n- 正在刷：《{current_video_title[:22]}》 (UP: {current_author})\n- 进度上报：模拟播放已达 {watch_progress} 秒 (BV: {current_bvid})"

        msg = (
            f"📺【助手 · B站刷视频实时控制台】\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚡ 当前行为：{current_status_desc}{current_detail}\n"
            f"🕒 运行窗口：{window_desc}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 今日统计：已刷并发表见解 {cnt} / {daily_limit} 条\n"
            f"🎯 本轮配额：{cnt_s} / {max_s} 次 (会话: {session_key or '待机'})\n"
            f"⏳ 匀速调控：{cd_desc}\n"
            f"🔍 筛选准则：L1[必须命中大肥鱼] + L2[必须命中动画/二次元] | L3[Gemini/Claude/GPT/AI]自选\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎬 最近刷过的视频与主楼见解：\n\n"
            f"{recent_block}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 快捷指令：\n"
            f"• /刷视频 (让助手立刻去刷视频)\n"
            f"• /刷视频状态 (刷新控制台实时看板)\n"
            f"🌐 本地Web大屏：file://d:/机器人/astrbot/data/bili_console.html"
        )
        yield event.plain_result(msg)

    @filter.command("bili_patrol")
    @filter.command("bili_watch")
    @filter.command("刷视频")
    async def bili_patrol(self, event: AstrMessageEvent):
        """手动强制触发一次 B 站 AI 视频浏览与见解发表"""
        yield event.plain_result("好的，我现在去 B 站刷刷最新的 AI 视频~")
        success = await self._patrol_and_comment_one_video()
        if success:
            yield event.plain_result("搞定啦，看了一个挺有意思的视频，发了见解并在楼下@你了~")
        else:
            yield event.plain_result("刷了一圈，刚才没看到特别对胃口的视频，稍后继续刷啦~")

    @filter.command("bili_login")
    async def bili_login(self, event: AstrMessageEvent):
        """获取 B 站扫码登录二维码"""
        try:
            self._qr_login_instance = login_v2.QrCodeLogin()
            await self._qr_login_instance.generate_qrcode()
            pic = self._qr_login_instance.get_qrcode_picture()

            qr_save_path = "/AstrBot/data/temp/bili_login_qr.png"
            pic.to_file(qr_save_path)

            yield event.chain_result([
                Plain("请使用手机 Bilibili App 扫描二维码登录助手的账号：\n"),
                Image.fromFileSystem(qr_save_path),
                Plain("\n扫码确认后，请向我发送 /bili_check 完成凭据持久化~")
            ])
        except Exception as e:
            yield event.plain_result(f"生成二维码失败: {e}")

    @filter.command("bili_check")
    async def bili_check(self, event: AstrMessageEvent):
        """检查扫码登录状态并自动保存凭证"""
        if not self._qr_login_instance:
            yield event.plain_result("请先发送 /bili_login 生成二维码哦~")
            return

        try:
            state = await self._qr_login_instance.check_state()
            if state == login_v2.LoginCheck.SUCCESS:
                cred = self._qr_login_instance.get_credential()
                c_dict = self.config.get("bilibili", {})
                c_dict["sessdata"] = cred.sessdata or ""
                c_dict["bili_jct"] = cred.bili_jct or ""
                c_dict["buvid3"] = cred.buvid3 or ""
                c_dict["dedeuserid"] = cred.dedeuserid or ""
                self._save_config()
                self._qr_login_instance = None
                yield event.plain_result("B 站账号登录成功！凭据已自动写入配置并持久化生效~")
            elif state == login_v2.LoginCheck.WAIT_CONFIRM:
                yield event.plain_result("已扫描二维码，请在手机上点击确认登录哦~")
            elif state == login_v2.LoginCheck.EXPIRED:
                yield event.plain_result("二维码已过期，请重新发送 /bili_login 获取~")
            else:
                yield event.plain_result("尚未扫码，请在手机端完成扫描~")
        except Exception as e:
            yield event.plain_result(f"检查状态失败: {e}")

    @filter.command("token_status")
    async def token_status(self, event: AstrMessageEvent):
        """查看今日 Token 流量监视状态"""
        try:
            conn = sqlite3.connect(DATA_V4_URI, uri=True)
            with conn:
                # 统计今日 (UTC时间)
                today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rows = conn.execute(
                    """
                    SELECT COUNT(*), SUM(token_input_other), SUM(token_input_cached), SUM(token_output)
                    FROM provider_stats
                    WHERE created_at >= ?
                    """,
                    (f"{today_utc} 00:00:00",),
                ).fetchone()

                calls, inp, cached, outp = rows
                calls = calls or 0
                inp = inp or 0
                cached = cached or 0
                outp = outp or 0
                total = inp + cached + outp

            msg = (
                f"【Gemini API 专属流量监视报告】\n"
                f"- 今日总调用数：{calls} 次\n"
                f"- 输入 Token：{inp:,} (缓存命中: {cached:,})\n"
                f"- 输出 Token：{outp:,}\n"
                f"- 今日总计：{total:,} Tokens\n"
                f"- 监视器状态：正常运行中（输出超 150 触发预警，异常突增自动拦截）"
            )
            yield event.plain_result(msg)
        except Exception as e:
            yield event.plain_result(f"查询 Token 状态失败: {e}")
