import asyncio
import json
import re
import time
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, register
from .memory_manager import MemoryManager


@register(
    "astrbot_plugin_memory",
    "Admin",
    "高级三层长效记忆与人际好感度系统：好感亲疏温差 + 主人专属偏好经历沉淀 + 向量检索",
    "1.1.0",
)
class MemoryPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.mem_mgr = MemoryManager()
        self._embedding_provider = None
        self._chat_provider = None

    def _get_embedding_provider(self):
        if self._embedding_provider:
            return self._embedding_provider
        try:
            providers = self.context.get_all_embedding_providers()
            if providers:
                self._embedding_provider = providers[0]
                return self._embedding_provider
        except Exception as e:
            logger.warning(f"[MemoryPlugin] 获取 Embedding Provider 失败: {e}")
        return None

    async def _get_chat_provider(self, umo: str | None = None):
        if self._chat_provider:
            return self._chat_provider
        try:
            prov = await self.context.get_using_provider_async(umo)
            self._chat_provider = prov
            return self._chat_provider
        except Exception as e:
            logger.warning(f"[MemoryPlugin] 获取 Chat Provider 失败: {e}")
        return None

    @filter.on_llm_request(priority=500)
    async def inject_memory_to_context(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """在请求 LLM 前，按用户 ID 精准召回好感度、亲疏温度与主人专属核心事实（单人仅几十 Token）"""
        if not req or not req.contexts:
            return

        sender_id = str(event.get_sender_id() or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        user_text = (event.message_str or "").strip()

        memory_blocks = []

        # 1. 动态好感度与亲疏态度指引（初始：主人 90，其他群友 50，浮动由模型自主决定）
        affection = self.mem_mgr.get_user_affection(sender_id)
        if affection < 60:
            stage_desc = "不熟/初识（冷淡而温柔）"
            attitude_hint = "对不熟的人略微冷淡，克制疏离、客气简短，保持猫咪的边界感；但内心深处依旧温柔善良，绝不刻薄伤人"
        elif affection < 80:
            stage_desc = "熟悉/朋友（温和友善）"
            attitude_hint = "自然随和，亲切温和，真诚待人"
        else:
            stage_desc = "极度熟悉/至交与主人（小腹黑而至善）"
            attitude_hint = "非常熟悉亲昵，带点聪明的小腹黑、机敏打趣与猫咪特有的嘴硬不服输，坦率狡黠不搞别扭傲娇；骨子里全心全意温柔善良、极度信任依赖"

        user_nick = event.get_sender_name() or ("主人" if sender_id == "1000000001" else f"用户({sender_id})")
        role_desc = "你的唯一主人与管理员" if sender_id == "1000000001" else "群友"

        memory_blocks.append(
            f"【当前交互对象关系与好感度】\n"
            f"- 交互对象：{user_nick} ({role_desc})\n"
            f"- 当前好感度：{affection}/100（当前状态：{stage_desc}）\n"
            f"- 态度指引：{attitude_hint}。好感值的升降由你在交互中心理浮动判断。"
        )

        # 2. 结构化长效偏好注入（仅针对主人 1000000001）
        if sender_id == "1000000001":
            profile = self.mem_mgr.get_user_profile(sender_id)
            if profile and profile.get("facts"):
                facts_str = "；".join(profile["facts"][-6:])
                memory_blocks.append(
                    f"【长效记忆·主人已知偏好与事实】\n- 关键偏好：{facts_str}"
                )

        # 3. 向量语义检索高度相关的历史经历（时间轴三维拟人召回）
        if len(user_text) >= 4 and not user_text.startswith(("/", "!", "#")):
            try:
                emb_prov = self._get_embedding_provider()
                if emb_prov:
                    q_emb = await emb_prov.get_embedding(user_text)
                    recalled = self.mem_mgr.search_episodic_memories(
                        query_embedding=q_emb,
                        user_id=sender_id,
                        group_id=group_id,
                        top_k=2,
                        threshold=0.45,
                    )
                    if recalled:
                        lines = [f"- {r['content']}" for r in recalled]
                        memory_blocks.append(
                            "【长效记忆·唤醒的相关历史经历】\n" + "\n".join(lines)
                        )
            except Exception as e:
                logger.warning(f"[MemoryPlugin] 语义检索情景记忆异常: {e}")

        # 4. 时序经历流感知：当用户询问近期行踪或经历时，自动注入连续时间轴经历
        temporal_keywords = ["刚才", "今天干", "今天做", "在干嘛", "忙什么", "干嘛去了", "今天过得", "最近干", "在做什么"]
        if any(kw in user_text for kw in temporal_keywords):
            try:
                recent_events = self.mem_mgr.hub.get_recent_stream(limit=3)
                if recent_events:
                    recent_lines = [
                        f"- [{ev.time_str}] ({ev.source_module}) {ev.narrative}"
                        for ev in recent_events
                    ]
                    memory_blocks.append(
                        "【时间轴·助手近期连续生命经历流】\n" + "\n".join(recent_lines)
                    )
            except Exception as e:
                logger.warning(f"[MemoryPlugin] 检索近期时间轴异常: {e}")

        if not memory_blocks:
            return

        injected_text = "\n\n".join(memory_blocks)

        # 注入系统提示：追加在现有 system 提示末尾，或置于开头
        injected = False
        for ctx in req.contexts:
            if ctx.get("role") == "system":
                c = ctx.get("content", "")
                if isinstance(c, str):
                    ctx["content"] = c + "\n\n" + injected_text
                    injected = True
                    break
        if not injected:
            req.contexts.insert(0, {"role": "system", "content": injected_text})

        logger.info(f"[MemoryPlugin] 成功为用户 {sender_id} 动态注入长效记忆与情景关联！")

    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        """对话发送后，在后台异步协程静默提取新的个人事实或关键约定，完全不阻塞当前聊天"""
        asyncio.create_task(self._background_memory_extraction(event))

    async def _background_memory_extraction(self, event: AstrMessageEvent):
        sender_id = str(event.get_sender_id() or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        user_text = (event.message_str or "").strip()

        # 过滤无意义/过短/指令消息
        if len(user_text) < 3 or user_text.startswith(("/", "!", "#", "【")):
            return
        if user_text in ("在吗", "在不在", "你好", "哈喽", "草", "哈哈", "确实", "好的", "行"):
            return

        # 获取机器人的回复内容
        bot_text = ""
        result = event.get_result()
        if result and result.chain:
            from astrbot.core.message.components import Plain
            bot_text = "".join(
                [c.text for c in result.chain if isinstance(c, Plain) and c.text]
            )

        if not bot_text:
            return

        sender_name = event.get_sender_name() or ("主人" if sender_id == "1000000001" else f"用户{sender_id}")
        is_admin = (sender_id == "1000000001")

        extract_prompt = (
            f"你是一个人际好感度与长效记忆分析器。请根据以下对话，由你自主决定好感度浮动，并提取核心偏好记忆。\n"
            f"用户（{sender_name}，ID:{sender_id}）发言：\"{user_text}\"\n"
            f"机器人回复：\"{bot_text}\"\n\n"
            f"规则：\n"
            f"1. 【好感度浮动评估（由你自主决定）】：\n"
            f"   - 贴心关怀、幽默互动、投喂零食/小鱼干、认可赞扬：affection_delta 可在 +1 到 +3 之间；\n"
            f"   - 粗鲁冒犯、恶意挑衅、故意找茬攻击：affection_delta 可在 -1 到 -3 之间；\n"
            f"   - 正常日常闲聊或普通问答无明显情绪波动：affection_delta 填 0。\n"
            f"2. 【长效偏好事实（仅限主人 1000000001）】：\n"
            f"   - 仅当用户是主人（主人，ID: 1000000001）时，才提取对长期陪伴有价值的核心偏好（15字以内）或约定事件；\n"
            f"   - 对其他普通群友，new_facts 严格返回空列表 []，episodic_event 留空字符串。\n"
            f"3. 严格输出合法 JSON（无任何 markdown 标记）：\n"
            f'{{\"affection_delta\": 0, \"affection_reason\": \"\", \"has_memory\": false, \"new_facts\": [], \"episodic_event\": \"\", \"importance\": 1}}\n'
        )

        try:
            provider = await self._get_chat_provider(event.unified_msg_origin)
            if not provider:
                return

            resp = await provider.text_chat(
                prompt=extract_prompt,
                system_prompt="你是一个精准的人格与记忆分析引擎，只输出符合格式的合法 JSON。",
            )

            raw_resp = (resp.completion_text or "").strip()
            cleaned_json = re.sub(r"^```(?:json)?|```$", "", raw_resp, flags=re.MULTILINE).strip()
            data = json.loads(cleaned_json)

            # 1. 好感度自主浮动更新
            aff_delta = int(data.get("affection_delta") or 0)
            aff_reason = str(data.get("affection_reason") or "").strip()
            if aff_delta != 0:
                self.mem_mgr.update_user_affection(
                    user_id=sender_id,
                    delta=aff_delta,
                    reason=aff_reason,
                    nickname=sender_name,
                )

            # 2. 主人专属长效记忆沉淀
            if is_admin and data.get("has_memory"):
                new_facts = data.get("new_facts") or []
                episodic_event = (data.get("episodic_event") or "").strip()
                importance = int(data.get("importance") or 1)

                if new_facts:
                    self.mem_mgr.update_user_profile(
                        user_id=sender_id,
                        nickname=sender_name,
                        new_facts=new_facts,
                        role_type="admin",
                    )
                    logger.info(
                        f"[MemoryPlugin] 成功为主人 {sender_id} 沉淀长效事实画像: {new_facts}"
                    )

                if episodic_event:
                    emb_prov = self._get_embedding_provider()
                    emb_vec = None
                    if emb_prov:
                        try:
                            emb_vec = await emb_prov.get_embedding(episodic_event)
                        except Exception as e:
                            logger.warning(f"[MemoryPlugin] 向量化情景记忆失败: {e}")

                    self.mem_mgr.add_episodic_memory(
                        user_id=sender_id,
                        group_id=group_id,
                        content=episodic_event,
                        embedding=emb_vec,
                        importance=importance,
                    )
                    logger.info(
                        f"[MemoryPlugin] 成功为主人 {sender_id} 向量沉淀情景事件记忆: {episodic_event}"
                    )

        except Exception as e:
            logger.debug(f"[MemoryPlugin] 后台好感/记忆处理结束: {e}")

    # ==========================================
    # 【主动记忆/回忆/好感度工具（让模型可以主动调用）】
    # ==========================================

    @filter.llm_tool(name="adjust_favorability")
    async def adjust_favorability(self, event: AstrMessageEvent, delta: int, reason: str) -> str:
        """由你自主决定微调对当前用户的好感度。当对方的言行令你感到开心、感动、被冒犯或挑衅时调用。
        Args:
            delta (int): 好感度增减量（如 -3 到 +3，最大不超过 5）
            reason (str): 好感度浮动的简要原因（如：对方贴心安慰、投喂零食、无端找茬等）
        """
        sender_id = str(event.get_sender_id() or "").strip()
        sender_name = event.get_sender_name() or ""
        old_v, new_v = self.mem_mgr.update_user_affection(
            user_id=sender_id, delta=delta, reason=reason, nickname=sender_name
        )
        change_str = f"+{delta}" if delta > 0 else str(delta)
        return f"你对该用户的好感度已变动：{old_v} -> {new_v} ({change_str}，原因：{reason})"

    @filter.llm_tool(name="remember_user_fact")
    async def remember_user_fact(self, event: AstrMessageEvent, fact: str) -> str:
        """当用户明确要求记住某件事、某个偏好或信息时调用此工具保存。
        Args:
            fact (str): 要记住的核心事实、偏好或事件（例如：主人喜欢喝冰美式咖啡）
        """
        sender_id = str(event.get_sender_id() or "").strip()
        if sender_id != "1000000001":
            return "哈基米只记录主人的长效记忆喵~"

        sender_name = event.get_sender_name() or "你"
        self.mem_mgr.update_user_profile(
            user_id=sender_id,
            nickname=sender_name,
            new_facts=[fact],
        )

        emb_prov = self._get_embedding_provider()
        emb_vec = None
        if emb_prov:
            try:
                emb_vec = await emb_prov.get_embedding(fact)
            except Exception:
                pass

        self.mem_mgr.add_episodic_memory(
            user_id=sender_id,
            group_id=str(event.get_group_id() or ""),
            content=fact,
            embedding=emb_vec,
            importance=3,
        )
        return f"已成功把「{fact}」刻入我的长期记忆库啦！"

    @filter.llm_tool(name="recall_user_facts")
    async def recall_user_facts(self, event: AstrMessageEvent, keyword: str = "") -> str:
        """当被询问“你还记得我喜欢什么吗”、“我之前跟你说过什么”时，主动翻查长期记忆。
        Args:
            keyword (str): 要回忆的关键词（例如：喜好、工作、咖啡、猫）
        """
        sender_id = str(event.get_sender_id() or "").strip()
        profile = self.mem_mgr.get_user_profile(sender_id)
        facts = profile.get("facts", []) if profile else []

        if keyword:
            matched = [f for f in facts if keyword in f]
            if matched:
                return f"关于「{keyword}」的记忆：\n" + "\n".join([f"- {m}" for m in matched])

        if facts:
            return "关于你的已知记忆画像：\n" + "\n".join([f"- {f}" for f in facts[-8:]])
        return "记忆库里暂时还没有记录更多关于这个的细节喵~"

    # ==========================================
    # 【管理指令 /mem】
    # ==========================================

    @filter.command("mem")
    async def cmd_memory(self, event: AstrMessageEvent, action: str = "profile", content: str = ""):
        """高级长效记忆库管理指令：/mem [profile/list/clear/add/favor]"""
        sender_id = str(event.get_sender_id() or "").strip()
        action = action.lower().strip()

        if sender_id != "1000000001" and action in ("add", "clear"):
            yield event.plain_result("只有主人才能修改长效记忆库喵~")
            return

        if action in ("favor", "affection", "好感"):
            aff = self.mem_mgr.get_user_affection(sender_id)
            profile = self.mem_mgr.get_user_profile(sender_id)
            history = profile.get("affection_history", []) if profile else []
            history_lines = [
                f"- {time.strftime('%m-%d %H:%M', time.localtime(h['time']))}: {h['old']} -> {h['new']} ({'+' if h['delta'] > 0 else ''}{h['delta']}) 原因: {h['reason']}"
                for h in history[-5:]
            ]
            history_text = "\n" + "\n".join(history_lines) if history_lines else " 暂无浮动记录"
            yield event.plain_result(
                f"【好感度档案】\n"
                f"当前好感度：{aff}/100\n"
                f"近期浮动明细：{history_text}"
            )
            return

        elif action in ("profile", "view"):
            profile = self.mem_mgr.get_user_profile(sender_id)
            if not profile or not profile.get("facts"):
                yield event.plain_result(f"QQ {sender_id} 的记忆画像暂为空。")
                return

            facts_text = "\n".join([f"{i+1}. {f}" for i, f in enumerate(profile["facts"])])
            yield event.plain_result(
                f"【{profile.get('nickname', '用户')} 的长期画像档案】\n"
                f"身份：{profile.get('role_type')}\n"
                f"已沉淀偏好与长效事实：\n{facts_text}"
            )
            return

        elif action in ("list", "episodes"):
            recent = self.mem_mgr.get_recent_memories(user_id=sender_id, limit=5)
            if not recent:
                yield event.plain_result("暂无情景经历记录。")
                return

            lines = [f"#{r['id']}: {r['content']}" for r in recent]
            yield event.plain_result("【最近唤醒的情景事件片段】\n" + "\n".join(lines))
            return

        elif action == "add":
            if not content:
                yield event.plain_result("用法：/mem add <要记住的事实内容>")
                return
            self.mem_mgr.update_user_profile(
                user_id=sender_id,
                nickname=event.get_sender_name() or "你",
                new_facts=[content.strip()],
            )
            yield event.plain_result(f"已手动为你的画像添加记忆：\n「{content.strip()}」")
            return

        elif action == "clear":
            self.mem_mgr.update_user_profile(
                user_id=sender_id,
                nickname=event.get_sender_name() or "你",
                new_facts=[],
            )
            yield event.plain_result("已清空你在记忆库中的画像事实。")
            return

        else:
            yield event.plain_result(
                "【高级长效记忆库指令指南】\n"
                "- /mem favor : 查看当前好感度与浮动明细\n"
                "- /mem profile : 查看当前记录的画像与偏好\n"
                "- /mem list : 查看近期沉淀的情景经历片段\n"
                "- /mem add <内容> : 手动添加一条记忆事实\n"
                "- /mem clear : 清空个人画像记录"
            )
