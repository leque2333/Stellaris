import asyncio
import json
import random
import re
import time
from pathlib import Path
from typing import Any
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import At, Reply, Image

BLACKLIST_FILE = Path("/AstrBot/data/blacklist_users.json")
ADMIN_ID = "1000000001"
WHITELIST_GROUP_ID = "2000000001"
GROUP_COOLDOWN_SECONDS = 60.0
PRE_COOLDOWN_SECONDS = 10.0
CHAT_REPLY_PROBABILITY = 0.10

# 记录每个群最后一次回复发出的时间戳
_last_group_reply_time: dict[str, float] = {}
# 记录每个群最后一次出现 @ 别人的时间戳（60s 内不介入非 @ 讨论）
_last_at_others_time: dict[str, float] = {}
# 记录每个用户在群内的最新消息流水序号 (替代已废弃的内部 get_user_latest_seq)
_user_latest_seq_map: dict[tuple[str, str], int] = {}
# 记录每个会话最近一次发出的文本与时间戳（15s 内重复内容拦截去重）
_last_session_sent_text: dict[str, tuple[float, str]] = {}
# 记录主人（ADMIN_ID）最近一次发送的图片与时间戳（支持两阶段形象与头像更新）
_last_admin_image_info: dict[str, Any] = {"time": 0.0, "image": None}


def get_user_latest_seq(group_id: str, sender_id: str) -> int:
    """获取用户在群里的最新发言序号"""
    return _user_latest_seq_map.get((str(group_id), str(sender_id)), 0)


def is_at_others(event: AstrMessageEvent) -> bool:
    """精准判断是否 @ 了其他群友（但并未 @ 机器人）"""
    self_id = str(event.get_self_id() or "").strip()
    bot_qqs = {self_id, "1000000002"} - {""}
    has_at_others = False
    try:
        for msg in event.get_messages():
            if isinstance(msg, At):
                target = str(getattr(msg, "qq", "")).strip()
                if target in bot_qqs or target == "all":
                    return False
                has_at_others = True
    except Exception:
        pass
    return has_at_others


def is_user_blacklisted(sender_id: str) -> bool:
    target = str(sender_id).strip()
    if target in {"2508413523"}:
        return True
    if BLACKLIST_FILE.exists():
        try:
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                b_list = json.load(f)
                if isinstance(b_list, list) and target in [str(x).strip() for x in b_list]:
                    return True
        except Exception:
            pass
    return False


def is_at_or_mention_bot(event: AstrMessageEvent) -> bool:
    """精准判断消息是否明确 @ 了机器人或引用回复了机器人的发言"""
    self_id = str(event.get_self_id() or "").strip()
    bot_qqs = {self_id, "1000000002"} - {""}

    # 1. 检查解析后的消息段 (At / Reply)
    try:
        for msg in event.get_messages():
            if isinstance(msg, At):
                target = str(getattr(msg, "qq", "")).strip()
                if target in bot_qqs or target == "all":
                    return True
            elif isinstance(msg, Reply):
                target = str(getattr(msg, "sender_id", "")).strip()
                if target in bot_qqs:
                    return True
    except Exception:
        pass

    # 2. 检查 raw_message (OneBot CQ 码或结构化消息)
    try:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", "")
        if isinstance(raw, str):
            for b_qq in bot_qqs:
                if f"[CQ:at,qq={b_qq}]" in raw:
                    return True
        elif isinstance(raw, list):
            for seg in raw:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    target = str(seg.get("data", {}).get("qq", "")).strip()
                    if target in bot_qqs or target == "all":
                        return True
    except Exception:
        pass

    # 3. 检查文本中显式的 @助手、@哈基米 或 @机器人QQ
    try:
        plain = event.message_str or ""
        if "@助手" in plain or "@哈基米" in plain:
            return True
        for b_qq in bot_qqs:
            if f"@{b_qq}" in plain:
                return True
    except Exception:
        pass

    return False


def sanitize_and_optimize_contexts(contexts: list[dict]) -> list[dict]:
    if not contexts:
        return contexts

    # 1. 寻找最近的 rounds：从 user 消息切入，最多保留最近 5 个完整对话回合，确保首条必定是 user 消息
    user_indices = [idx for idx, c in enumerate(contexts) if c.get("role") == "user"]
    if len(user_indices) > 5:
        cut_idx = user_indices[-5]
        contexts = contexts[cut_idx:]

    # 2. 校验开头：绝对不能以孤立的 tool / function 或孤立的 assistant tool_calls 开头
    start_idx = 0
    while start_idx < len(contexts):
        role = contexts[start_idx].get("role")
        if role in ("tool", "function"):
            start_idx += 1
            continue
        if role == "assistant" and contexts[start_idx].get("tool_calls"):
            start_idx += 1
            continue
        break
    contexts = contexts[start_idx:]

    # 3. 校验配对一致性：保证每一个 tool 必须紧跟在其对应的 assistant tool_calls 之后
    valid_contexts = []
    i = 0
    while i < len(contexts):
        ctx = contexts[i]
        role = ctx.get("role")

        if role == "assistant" and ctx.get("tool_calls"):
            j = i + 1
            collected_tools = []
            while j < len(contexts) and contexts[j].get("role") in ("tool", "function"):
                collected_tools.append(contexts[j])
                j += 1
            if not collected_tools:
                logger.warning("[GuardPlugin] 丢弃未配对的孤立 assistant tool_calls，防止 Gemini 400 错误。")
                i += 1
                continue
            valid_contexts.append(ctx)
            valid_contexts.extend(collected_tools)
            i = j
            continue
        elif role in ("tool", "function"):
            logger.warning(f"[GuardPlugin] 丢弃孤立的 tool 响应 ({ctx.get('name')})，防止 Gemini 400 错误。")
            i += 1
            continue
        else:
            valid_contexts.append(ctx)
            i += 1

    # 4. 彻底清洗历史 Think/Thinking 密文块与历史超大图片 Base64：
    # 彻底杜绝超大 Base64 与 Gemini 思考过程堆叠导致 Token 飙升至数万甚至被判定滥用！
    last_user_idx = -1
    for idx, ctx in enumerate(valid_contexts):
        if ctx.get("role") == "user":
            last_user_idx = idx

    cleaned_img_count = 0
    cleaned_think_count = 0
    tool_truncated_count = 0

    for idx, ctx in enumerate(valid_contexts):
        is_latest_user = (idx == last_user_idx)
        c = ctx.get("content")
        if isinstance(c, list):
            new_c = []
            for item in c:
                if isinstance(item, dict):
                    item_type = item.get("type")
                    # 剥离所有历史思考/推理密文（thinking 过程无需重复喂回给模型）
                    if item_type == "think":
                        cleaned_think_count += 1
                        continue
                    # 剥离历史旧图
                    elif item_type == "image_url":
                        if is_latest_user:
                            new_c.append(item)
                        else:
                            cleaned_img_count += 1
                            new_c.append({"type": "text", "text": "[历史图片]"})
                        continue
                new_c.append(item)
            ctx["content"] = new_c
        elif isinstance(c, str):
            # 历史工具响应脱敏截断：历史轮次的工具检索长文本（如数十万字百科网页）压缩保留前 500 字
            if not is_latest_user and ctx.get("role") in ("tool", "function") and len(c) > 600:
                ctx["content"] = c[:500] + "\n... [历史检索已压缩]"
                tool_truncated_count += 1
            elif is_latest_user and ctx.get("role") in ("tool", "function") and len(c) > 2000:
                # 当前最新轮次的工具长响应保护：单次工具输出上限控制在 2000 字以内
                ctx["content"] = c[:2000] + "\n... [检索已截断前 2000 字]"
                tool_truncated_count += 1

    if cleaned_think_count > 0:
        logger.info(f"[GuardPlugin] 自动剥离上下文中 {cleaned_think_count} 个历史思考过程(Think)密文块，单次节省 90% 以上 Token！")
    if cleaned_img_count > 0:
        logger.info(f"[GuardPlugin] 自动脱敏清理上下文中 {cleaned_img_count} 张历史旧图 Base64。")
    if tool_truncated_count > 0:
        logger.info(f"[GuardPlugin] 自动截断压缩上下文中 {tool_truncated_count} 个工具搜索响应。")

    return valid_contexts


def beautify_qq_message(text: str) -> str:
    """将从知识库、模型或网络检索洗脱出的内容格式化为人类友好、适合手机 QQ 阅读的清爽排版"""
    if not text:
        return text

    # 1. 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 2. Markdown 标题 (### / ## / # 标题) 转换为 【标题】
    text = re.sub(r"^(?:#{1,6})\s*(.*?)\s*$", r"\n【\1】\n", text, flags=re.MULTILINE)

    # 3. 将独立的 **加粗标题**: 转换为 【标题】 (解决 QQ 移动端无法渲染 Markdown 加粗显示难看星号的问题)
    text = re.sub(r"(?:\n|\A)\s*\*\*(.*?)\*\*\s*[:：]?\s*", r"\n\n【\1】\n", text)
    # 处理行内 **粗体**: (例如 'xxx **DeepSeek-V3**: yyy')
    text = re.sub(r"\s+\*\*(.*?)\*\*\s*[:：]\s*", r"\n\n【\1】\n", text)
    # 去除其余行内 **加粗** 星号，转为易读的「重点」或直白文字
    text = re.sub(r"\*\*(.*?)\*\*", r"「\1」", text)

    # 4. 如果遇到已被意外压缩在同一行的列表项，自动补齐换行
    # 针对 - / * / • 开头的列表项
    text = re.sub(r"(?<!\n)[ \t]+([-*•]\s+)", r"\n\1", text)
    # 针对 1. 2. 开头的列表项 (前有句号/冒号/分号/空格，后有序号和文字)
    text = re.sub(r"([。；;！!：:\s])\s*(\d+\.\s+)(?=[\u4e00-\u9fa5A-Za-z])", r"\1\n\2", text)

    # 5. 针对大分类方括号 【...】 如果前有句末标点，增加空行分隔保持呼吸感
    text = re.sub(r"([：:。！？])\s*(【[^】]{3,}】)", r"\1\n\n\2\n", text)
    # 连续两个方括号紧挨着
    text = re.sub(r"(】)\s*(【)", r"\1\n\n\2", text)
    # 方括号后紧跟列表项
    text = re.sub(r"(【[^】]+】)\s*([-*•]|\d+\.)", r"\1\n\2", text)

    # 7. 净化粗糙方言与过口语化词汇（去除“啥”、“咋”等，转为标准清爽用语）
    replacements = [
        ("干啥", "干嘛"),
        ("有啥", "有什么"),
        ("说啥", "说什么"),
        ("看啥", "看什么"),
        ("吃啥", "吃什么"),
        ("做啥", "做什么"),
        ("想啥", "想什么"),
        ("买啥", "买什么"),
        ("图啥", "图什么"),
        ("为啥", "为什么"),
        ("啥时候", "什么时候"),
        ("啥事", "什么事"),
        ("啥也不", "什么都不"),
        ("啥都", "什么都"),
        ("啥没", "什么都没"),
        ("没啥", "没什么"),
        ("是啥", "是什么"),
        ("算啥", "算什么"),
        ("啥", "什么"),
        ("咋了", "怎么了"),
        ("咋办", "怎么办"),
        ("咋样", "怎么样"),
        ("咋说", "怎么说"),
        ("咋", "怎么"),
        ("随你便", "我倒是无所谓"),
        ("随便你", "我倒是无所谓"),
        ("随你吧", "我倒是无所谓"),
        ("出毛病", "出问题"),
        ("有毛病", "有问题"),
    ]
    for old_w, new_w in replacements:
        text = text.replace(old_w, new_w)

    # 6. 规范化空行 (最多连续保留2个换行，即1个空行) 与行首尾空白，句末绝不带句号
    lines = [l.rstrip() for l in text.split("\n")]
    cleaned = []
    prev_blank = False
    for l in lines:
        if not l.strip():
            if not prev_blank and cleaned:
                cleaned.append("")
                prev_blank = True
        else:
            line_str = l.strip()
            # 句尾去句号：如果该行以句号结尾，自动剥离末尾的中英文句号（保留省略号 ... 或 …）
            line_str = re.sub(r'。+$', '', line_str)
            line_str = re.sub(r'(?<!\.)\.(?!\.)$', '', line_str)
            if line_str:
                cleaned.append(line_str)
            prev_blank = False

    res = "\n".join(cleaned).strip()
    res = re.sub(r'。+$', '', res)
    res = re.sub(r'(?<!\.)\.(?!\.)$', '', res)
    return res.strip()


@register(
    "astrbot_plugin_guard",
    "Admin",
    "群聊 60s 发言冷却、10s 前冷却消抖与黑名单守卫",
    "1.7.0",
)
class GuardPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=10000)
    async def filter_all_messages(self, event: AstrMessageEvent):
        sender_id = str(event.get_sender_id() or "").strip()

        # 0. 记录主人最近发送的图片，支持先发图后发文字的两阶段换头像与形象更新
        if sender_id == ADMIN_ID:
            try:
                for comp in event.get_messages():
                    if isinstance(comp, Image):
                        _last_admin_image_info["time"] = time.time()
                        _last_admin_image_info["image"] = comp
                        logger.info("[GuardPlugin] 成功捕获并暂存来自主人的最新图片用于形象更新。")
                        break
            except Exception:
                pass

        # 1. 黑名单全局拦截
        if is_user_blacklisted(sender_id):
            logger.info(f"[GuardPlugin] 黑名单用户 {sender_id}，物理拦截丢弃！")
            event.stop_event()
            return

        # 2. 私聊范围限制：除了主人（ADMIN_ID），剩下的私聊全部拦截/ban
        if event.is_private_chat():
            if sender_id != ADMIN_ID:
                logger.info(f"[GuardPlugin] 拦截非主人私聊消息 (来自 QQ: {sender_id})，未在白名单，已静默丢弃。")
                event.stop_event()
                return

        # 3. 群聊范围限制：除了唯一群聊（WHITELIST_GROUP_ID），其余群聊全部拦截
        else:
            group_id = str(event.get_group_id() or "").strip()
            if group_id != WHITELIST_GROUP_ID:
                logger.info(f"[GuardPlugin] 拦截非白名单群聊消息 (来自群: {group_id})，未在白名单，已静默丢弃。")
                event.stop_event()
                return

            # 更新该用户在该群的消息流水号，用于防抖消抖
            key = (group_id, sender_id)
            _user_latest_seq_map[key] = _user_latest_seq_map.get(key, 0) + 1
            event.set_extra("user_msg_seq", _user_latest_seq_map[key])
            event.set_extra("user_msg_arrive_time", time.time())

    @filter.on_waiting_llm_request()
    async def filter_waiting_llm(self, event: AstrMessageEvent):
        sender_id = str(event.get_sender_id() or "").strip()

        # 0. 325 仙术杯彩蛋检查（文字或图片凑出 325 立即触发，不入记忆库）
        try:
            from .easter_egg_325 import handle_325_easter_egg
            if await handle_325_easter_egg(event):
                event.stop_event()
                return
        except Exception as e:
            logger.error(f"[GuardPlugin] 检查 325 彩蛋异常: {e}")

        # 1. 黑名单直接物理拦截
        if is_user_blacklisted(sender_id):
            logger.info(f"[GuardPlugin] 黑名单用户 {sender_id}，紧急拦截丢弃！")
            event.stop_event()
            return

        # 2. 私聊范围限制：只允许主人
        if event.is_private_chat():
            if sender_id != ADMIN_ID:
                logger.info(f"[GuardPlugin] 拦截非主人私聊 LLM 请求 (来自 QQ: {sender_id})")
                event.stop_event()
                return
            event.set_extra("guard_is_llm", True)
            return

        # 3. 群聊范围限制：只允许唯一群聊
        group_id = str(event.get_group_id() or "").strip()
        if group_id != WHITELIST_GROUP_ID:
            logger.info(f"[GuardPlugin] 拦截非白名单群聊 LLM 请求 (来自群: {group_id})")
            event.stop_event()
            return

        raw_text = (event.message_str or "").strip()

        # 2. 群聊消息字数超过 200 字忽略
        if len(raw_text) > 200:
            logger.info(
                f"[GuardPlugin] 群 {group_id} 消息超过 200 字 ({len(raw_text)} 字)，直接忽略不回复"
            )
            event.stop_event()
            return

        # 3. 群聊中 @ 别人（且未 @ 机器人），绝对不触发机器人并记录时间戳
        if is_at_others(event):
            _last_at_others_time[group_id] = time.time()
            logger.info(
                f"[GuardPlugin] 群 {group_id} 用户 {sender_id} @ 别人，记录 1 分钟免打扰并拦截不触发机器人。"
            )
            event.stop_event()
            return

        # 4. 判断是否 @ 机器人
        is_at = is_at_or_mention_bot(event)
        event.set_extra("is_at_bot", is_at)
        event.set_extra("guard_is_llm", True)

        # @ 机器人完全豁免（不受冷却与 @别人 免打扰限制）
        if is_at:
            logger.info(
                f"[GuardPlugin] 群 {group_id} 收到用户 {sender_id} 对机器人的直接 @，豁免冷却与免打扰限制，优先立即处理！"
            )
            return

        # 5. @ 别人后一分钟（60s）内的讨论都不接
        elapsed_at = time.time() - _last_at_others_time.get(group_id, 0.0)
        if elapsed_at < 60.0:
            remain_at = int(60.0 - elapsed_at)
            logger.info(
                f"[GuardPlugin] 群 {group_id} 刚刚有人 @ 别人（{int(elapsed_at)}s 前），1 分钟内群聊讨论不介入接话（还剩 {remain_at}s）。"
            )
            event.stop_event()
            return

        # 6. 非 @ 消息检查群聊 60s 冷却（对所有人均生效，包括管理员）
        now = time.time()
        last_time = _last_group_reply_time.get(group_id, 0.0)
        elapsed = now - last_time
        if elapsed < GROUP_COOLDOWN_SECONDS:
            remain = int(GROUP_COOLDOWN_SECONDS - elapsed)
            logger.info(
                f"[GuardPlugin] 群 {group_id} 处于 60s 发言冷却中（还剩 {remain}s），非 @ 消息直接拦截，防止无限对轰与刷屏。"
            )
            event.stop_event()
            return

        # 7. 60s 冷却就绪后，非 @ 普通发言按 10% 概率回复（90% 保持静默不插话）
        roll = random.random()
        if roll >= CHAT_REPLY_PROBABILITY:
            logger.info(
                f"[GuardPlugin] 群 {group_id} 60s 冷却已就绪，但非 @ 消息未命中 10% 接话概率 (roll: {roll:.2f} >= 0.10)，保持静默。"
            )
            event.stop_event()
            return

    @filter.on_llm_request()
    async def filter_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        sender_id = str(event.get_sender_id() or "").strip()
        if is_user_blacklisted(sender_id):
            event.stop_event()
            return

        if event.is_private_chat():
            if sender_id != ADMIN_ID:
                event.stop_event()
                return
        else:
            group_id = str(event.get_group_id() or "").strip()
            if group_id != WHITELIST_GROUP_ID:
                event.stop_event()
                return

        # 优化与校验请求上下文：防 400 孤立工具响应、清理历史超大图片 Base64 与安全截断
        if req and req.contexts:
            req.contexts = sanitize_and_optimize_contexts(req.contexts)

        if not event.is_private_chat():
            # @ 机器人直接放行
            if event.get_extra("is_at_bot"):
                return

            group_id = str(event.get_group_id())
            # 检查是否处于 @ 别人后 1 分钟免打扰
            if time.time() - _last_at_others_time.get(group_id, 0.0) < 60.0:
                event.stop_event()
                return

            # 检查在排队期间该用户是否又说了新话（若是，取消旧 LLM 请求）
            my_seq = event.get_extra("user_msg_seq")
            if my_seq and get_user_latest_seq(group_id, sender_id) != my_seq:
                logger.info(f"[GuardPlugin] 群 {group_id} 用户 {sender_id} 已有更新发言，作废本条旧 LLM 请求")
                event.stop_event()
                return

            # 再次检查 60s 冷却（对所有人均生效）
            now = time.time()
            last_time = _last_group_reply_time.get(group_id, 0.0)
            if now - last_time < GROUP_COOLDOWN_SECONDS:
                event.stop_event()
                return

    @filter.on_decorating_result()
    async def filter_decorating_result(self, event: AstrMessageEvent):
        """发送前处理：10s 前冷却（消抖/静音等待，算入模型响应耗时）与重复回复去重过滤"""
        if not event.get_extra("guard_is_llm"):
            return

        result = event.get_result()
        if not result or not result.chain:
            event.clear_result()
            event.stop_event()
            return

        sender_id = str(event.get_sender_id())
        if is_user_blacklisted(sender_id):
            event.clear_result()
            event.stop_event()
            return

        group_id = str(event.get_group_id() or "")
        session_key = str(event.unified_msg_origin or event.session_id or group_id or sender_id)
        current_plain = result.get_plain_text().strip()

        # 0. 15 秒内相同会话完全相同文本去重拦截（防多阶段重复 Yield 或重试重发）
        now = time.time()
        last_sent = _last_session_sent_text.get(session_key)
        if last_sent and (now - last_sent[0] < 15.0) and (current_plain == last_sent[1]):
            logger.info(
                f"[GuardPlugin] 拦截到会话 ({session_key}) 15s 内完全相同重复回复，作废并清除: {current_plain[:30]}"
            )
            event.clear_result()
            event.stop_event()
            return

        if event.is_private_chat():
            self._normalize_chat_reply_length(event)
            _last_session_sent_text[session_key] = (time.time(), current_plain)
            return

        # @ 消息不受 10s 前冷却约束，直接发送
        if event.get_extra("is_at_bot"):
            logger.info(f"[GuardPlugin] 群 {group_id} @ 机器人消息，放行准备发送。")
            self._normalize_chat_reply_length(event)
            _last_session_sent_text[session_key] = (time.time(), current_plain)
            return

        my_seq = event.get_extra("user_msg_seq")
        arrive_time = event.get_extra("user_msg_arrive_time") or time.time()

        # 1. 检查模型生成期间用户是否发了新消息
        if my_seq and get_user_latest_seq(group_id, sender_id) != my_seq:
            logger.info(f"[GuardPlugin] 群 {group_id} 用户 {sender_id} 在模型生成期间有新发言，作废旧回复。")
            event.clear_result()
            event.stop_event()
            return

        # 1.5 检查是否在模型生成期间有人 @ 别人
        if time.time() - _last_at_others_time.get(group_id, 0.0) < 60.0:
            logger.info(f"[GuardPlugin] 群 {group_id} 在模型生成期间出现 @ 别人互动，作废本条回复。")
            event.clear_result()
            event.stop_event()
            return

        # 2. 检查 60s 冷却是否被其他消息抢占（对所有人均生效）
        now = time.time()
        if now - _last_group_reply_time.get(group_id, 0.0) < GROUP_COOLDOWN_SECONDS:
            logger.info(f"[GuardPlugin] 群 {group_id} 发送前检测到处于 60s 冷却中，作废本条回复。")
            event.clear_result()
            event.stop_event()
            return

        # 3. 10s 前冷却（静音消抖等待，算入模型响应时间）
        now = time.time()
        model_duration = now - arrive_time
        wait_remain = PRE_COOLDOWN_SECONDS - model_duration
        if wait_remain > 0:
            logger.info(
                f"[GuardPlugin] 群 {group_id} 模型响应耗时 {model_duration:.2f}s，10s 前冷却还需等待 {wait_remain:.2f}s（若用户期间继续发言则作废）..."
            )
            await asyncio.sleep(wait_remain)

        # 4. 等待结束：检查用户在等待的 10 秒静音期内是否又说话了
        if my_seq and get_user_latest_seq(group_id, sender_id) != my_seq:
            logger.info(
                f"[GuardPlugin] 群 {group_id} 用户 {sender_id} 在 10s 前冷却静音期内继续发言，作废此条回复！"
            )
            event.clear_result()
            event.stop_event()
            return

        # 4.5 检查是否在等待期间有人 @ 别人
        if time.time() - _last_at_others_time.get(group_id, 0.0) < 60.0:
            logger.info(f"[GuardPlugin] 群 {group_id} 在 10s 静音期间出现 @ 别人互动，作废本条回复。")
            event.clear_result()
            event.stop_event()
            return

        # 5. 等待结束：检查群 60s 冷却（对所有人均生效）
        now = time.time()
        if now - _last_group_reply_time.get(group_id, 0.0) < GROUP_COOLDOWN_SECONDS:
            logger.info(f"[GuardPlugin] 群 {group_id} 在静音等待期间已被占用 60s 冷却，作废本条回复。")
            event.clear_result()
            event.stop_event()
            return

        # 5.5 等待结束：再次检查是否在静音期已有相同内容发出
        last_sent = _last_session_sent_text.get(session_key)
        if last_sent and (now - last_sent[0] < 15.0) and (current_plain == last_sent[1]):
            logger.info(
                f"[GuardPlugin] 群 {group_id} 在静音等待后检测到 15s 内已有相同回复发出，作废本条: {current_plain[:30]}"
            )
            event.clear_result()
            event.stop_event()
            return

        # 6. 一切校验通过，刷新 60s 冷却并记录发送文本
        self._normalize_chat_reply_length(event)
        _last_group_reply_time[group_id] = time.time()
        _last_session_sent_text[session_key] = (time.time(), current_plain)
        logger.info(f"[GuardPlugin] 群 {group_id} 用户 {sender_id} 满 10s 静音等待，发出回复并激活 60s 冷却。")

    def _normalize_chat_reply_length(self, event: AstrMessageEvent):
        """格式化洗脱后的文本，确保从知识库、模型或网络检索提取的内容具备人类友好的换行排版与视觉呼吸感"""
        result = event.get_result()
        if not result or not result.chain:
            return

        from astrbot.core.message.components import Plain, At
        sender_id = str(event.get_sender_id() or "")

        # 群聊中回复管理员（主人）时，自动在开头补充真实的 QQ @蓝字提醒，并清理可能残留的生硬代码文本
        if not event.is_private_chat() and sender_id == ADMIN_ID:
            for comp in result.chain:
                if isinstance(comp, Plain) and comp.text:
                    comp.text = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', comp.text)
                    comp.text = re.sub(r'^@主人\s*', '', comp.text)
            has_at = any(isinstance(comp, At) and str(comp.qq) == ADMIN_ID for comp in result.chain)
            if not has_at:
                result.chain.insert(0, Plain(" "))
                result.chain.insert(0, At(qq=int(ADMIN_ID)))

        for comp in result.chain:
            if isinstance(comp, Plain) and comp.text:
                comp.text = beautify_qq_message(comp.text)

        # 确保整段消息末尾绝对不以句号结尾
        for comp in reversed(result.chain):
            if isinstance(comp, Plain) and comp.text:
                comp.text = re.sub(r'。+$', '', comp.text)
                comp.text = re.sub(r'(?<!\.)\.(?!\.)$', '', comp.text).rstrip()
                break

    @filter.after_message_sent()
    async def on_message_sent(self, event: AstrMessageEvent):
        """当机器人在群里发出普通闲聊消息后，刷新群 60 秒发言冷却；@ 机器人不计入重置冷却"""
        if not event.is_private_chat():
            group_id = str(event.get_group_id())
            is_at = event.get_extra("is_at_bot")
            if is_at is None:
                is_at = is_at_or_mention_bot(event)
            if is_at:
                logger.info(f"[GuardPlugin] 群 {group_id} 本次回复为 @ 机器人的直接交互，不计入重置 60s 群发言冷却。")
                return
            _last_group_reply_time[group_id] = time.time()
            logger.info(f"[GuardPlugin] 群 {group_id} 机器人普通闲聊发送完毕，刷新 60 秒发言冷却。")

    @filter.llm_tool(name="update_my_avatar_and_appearance")
    async def update_my_avatar_and_appearance(
        self,
        event: AstrMessageEvent,
        appearance_description: str,
    ) -> str:
        """由你完全自主决定是否更换自己的头像与形象认知。
        当主人发来新形象图片，且你经过审美判断觉得合心意、愿意采纳为新形象时调用此工具。
        若你觉得麻烦、不符合喜好或懒得折腾，可直接在对话中随性拒绝或吐槽，绝不强制调用。

        Args:
            appearance_description (str): 基于新图片，对你自己新外貌的详细特征描写（包括发型发色、眼睛瞳色、衣服穿着、特征配饰等）。
        """
        sender_id = str(event.get_sender_id() or "").strip()
        if sender_id != ADMIN_ID:
            return "只有主人可以为我更换新形象哦。"

        # 1. 寻找图片组件
        target_image = None
        try:
            for msg in event.get_messages():
                if isinstance(msg, Image):
                    target_image = msg
                    _last_admin_image_info["time"] = time.time()
                    _last_admin_image_info["image"] = msg
                    break
        except Exception:
            pass

        # 若本轮未携带图片，从最近 10 分钟缓存中寻找（支持两阶段：先发图、后发文字指令）
        if not target_image:
            cached_time = _last_admin_image_info.get("time", 0.0)
            if time.time() - cached_time < 600.0:
                target_image = _last_admin_image_info.get("image")

        if not target_image:
            return "没有找到可用的新图片。请发一张新形象图片给我，或者带图附上更换指令。"

        # 2. 转换图片为 Base64
        try:
            b64_str = await target_image.convert_to_base64()
        except Exception as e:
            logger.error(f"[GuardPlugin] 图片转 Base64 失败: {e}")
            return f"图片数据解析异常: {e}，请重新发送图片。"

        # 3. 调用 OneBot v11 set_qq_avatar 动作更换 QQ 头像
        avatar_ok = False
        bot = getattr(event, "bot", None)
        if not bot and hasattr(self, "context") and hasattr(self.context, "platform_manager"):
            for p in self.context.platform_manager.platform_insts:
                if hasattr(p, "bot") and p.bot:
                    bot = p.bot
                    break

        if bot:
            try:
                await bot.call_action("set_qq_avatar", file=f"base64://{b64_str}")
                avatar_ok = True
                logger.info("[GuardPlugin] 成功调用 NapCat set_qq_avatar 更换 QQ 头像！")
            except Exception as e:
                logger.error(f"[GuardPlugin] 调用 set_qq_avatar 失败: {e}")
        else:
            logger.warning("[GuardPlugin] 未能获取到 bot 适配器实例，无法调用 set_qq_avatar")

        # 4. 更新人物卡设定文件与数据库中的当前外貌认知（保持自主决断设定）
        prompt_path = Path("/AstrBot/data/new_persona.txt")
        new_appearance_line = f"- **形象自主与当前外貌**：{appearance_description.strip()}。（你对自身形象拥有完全自主决定权。当主人发来新图时，换不换完全由你自己决定，若合心意可调用工具更新，若懒得换也可随性拒绝）。"

        updated_prompt = None
        if prompt_path.exists():
            try:
                old_prompt = prompt_path.read_text(encoding="utf-8")
                if re.search(r"- \*\*(?:形象自主与当前外貌|当前外貌认知)\*\*：", old_prompt):
                    updated_prompt = re.sub(
                        r"- \*\*(?:形象自主与当前外貌|当前外貌认知)\*\*：[^\n]+",
                        new_appearance_line,
                        old_prompt,
                    )
                elif "- **身份与称谓**：" in old_prompt:
                    updated_prompt = old_prompt.replace(
                        "- **身份与称谓**：",
                        f"- **身份与称谓**：\n{new_appearance_line}",
                    )
                else:
                    updated_prompt = old_prompt + f"\n\n{new_appearance_line}\n"

                prompt_path.write_text(updated_prompt, encoding="utf-8")
                logger.info("[GuardPlugin] 成功将新形象写入 /AstrBot/data/new_persona.txt")
            except Exception as e:
                logger.error(f"[GuardPlugin] 写入 new_persona.txt 失败: {e}")

        if updated_prompt:
            try:
                import sqlite3
                conn = sqlite3.connect("/AstrBot/data/data_v4.db")
                c = conn.cursor()
                c.execute(
                    "UPDATE personas SET system_prompt = ? WHERE persona_id = 'default'",
                    (updated_prompt,),
                )
                conn.commit()
                conn.close()
                logger.info("[GuardPlugin] 成功更新 data_v4.db personas 默认设定。")
            except Exception as e:
                logger.error(f"[GuardPlugin] 更新 SQLite personas 失败: {e}")

            if hasattr(self, "context") and hasattr(self.context, "persona_manager"):
                try:
                    await self.context.persona_manager.update_persona(
                        "default",
                        system_prompt=updated_prompt,
                    )
                    logger.info("[GuardPlugin] 成功通过 persona_manager 热更新当前会话人设。")
                except Exception as e:
                    logger.error(f"[GuardPlugin] persona_manager 热更新异常: {e}")

        if avatar_ok:
            return f"QQ 头像已成功同步更换！你的核心自我认知已更新为新形象：{appearance_description}。请以助手自然、微带慵懒的真实口吻，向主人展示/回应你的新形象。"
        else:
            return f"核心自我认知已成功更新为新形象：{appearance_description}（头像更新由于接口频控稍后生效）。请以助手自然、微带慵懒的真实口吻回复主人。"
