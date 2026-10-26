# -*- coding: utf-8 -*-
"""
助手独立智能体内核 (AnanAgent Engine)
以人设灵魂与自传时间轴为中枢，对外提供统一的 .chat() 入口，向下统领统一工具总线与经历沉淀
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from .identity import IdentityResolver
from .llm import LLMClient
from .persona import PersonaManager
from .timeline_hub import TimelineHub
from .tools import ToolRegistry, tool_registry

logger = logging.getLogger("AnanAgent")


@dataclass
class AgentResponse:
    """智能体交互标准响应对象"""
    text: str                               # 经人设法则清洗后的最终纯口语台词 (无句号，无括号)
    actor_id: str                           # 交互对象实体标识 ('master' | 'user_xxx')
    actor_name: str                         # 交互对象展示名称 ('主人')
    current_affection: int                  # 当前好感度 (0 ~ 100)
    executed_tools: List[Dict[str, Any]] = field(default_factory=list) # 本轮自主执行的工具记录
    channel: str = "cli"                    # 触发通道 ('qq' | 'tg' | 'cli' | 'web')


class AnanAgent:
    """
    助手独立智能体大脑主控
    """
    def __init__(
        self,
        db_path: Optional[Path] = None,
        persona_path: Optional[Path] = None,
        llm_client: Optional[LLMClient] = None,
        tools: Optional[ToolRegistry] = None,
    ):
        self.timeline_hub = TimelineHub(db_path=db_path)
        self.persona_mgr = PersonaManager(persona_path=persona_path)
        self.llm = llm_client or LLMClient()
        self.tools = tools or tool_registry
        
        # 挂载内置工具
        self._register_builtin_tools()
        logger.info("[AnanAgent] 助手独立智能体内核已就绪！")

    def _register_builtin_tools(self):
        """挂载核心内置器官工具 (PC 控制、时间轴回忆、好感度调节)"""

        @self.tools.register(
            name="control_host_pc",
            description="当主人明确要求查看电脑状态、截屏、锁定电脑屏幕、或运行系统脚本时调用。"
        )
        async def control_host_pc(action: str, instruction: str = "") -> str:
            bridge_urls = [
                "http://127.0.0.1:8765/execute",
                "http://host.docker.internal:8765/execute",
            ]
            secret = "anan_agy_bridge_sec_8f7b2c9e1a4d"
            headers = {"X-Bridge-Token": secret, "Content-Type": "application/json"}
            payload = {"admin_id": IdentityResolver.get_master_qq(), "action": action, "instruction": instruction}

            connector = aiohttp.TCPConnector(ssl=False)
            for url in bridge_urls:
                try:
                    async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
                        async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                            if resp.status == 200:
                                res_json = await resp.json()
                                msg = res_json.get("message", "执行成功")
                                return f"PC 操作成功反馈：{msg}"
                except Exception:
                    continue
            return "宿主机反重力服务未响应，请检查服务是否已在宿主机开启~"

        @self.tools.register(
            name="search_past_experiences",
            description="当对方问及过去某件具体经历、约定、或者助手在B站/电脑上的历史见闻时调用。"
        )
        def search_past_experiences(keyword: str) -> str:
            recalls = self.timeline_hub.recall_timeline(actor_id="master", limit=3)
            if not recalls:
                return "暂未在时间轴中找到高度相关的经历记忆。"
            lines = [f"[{r.event.time_str}] {r.event.narrative}" for r in recalls]
            return "检索到的时间轴经历：\n" + "\n".join(lines)

        @self.tools.register(
            name="adjust_favorability",
            description="当对方的言行令你感到开心、感动、被冒犯或挑衅时自主微调好感度。"
        )
        def adjust_favorability(delta: int, reason: str) -> str:
            new_aff = self.timeline_hub.update_user_affection(
                user_id="master", delta=delta, reason=reason
            )
            return f"好感度已自主更新为 {new_aff}/100 (变动: {delta}, 原因: {reason})"

    async def chat(
        self,
        text: str,
        channel: str = "cli",
        user_id: str = "master",
        user_name: Optional[str] = None,
        chat_history: Optional[List[Dict[str, str]]] = None,
    ) -> AgentResponse:
        """
        标准对话中枢流程：
        1. 身份对齐
        2. 唤醒时间轴记忆与近期经历流
        3. 组装 V6.3 提示词与工具清单
        4. 大模型自主决策与 Function Calling
        5. 人设法则输出清洗 (去句号、去括号)
        6. 经历沉淀追加至连续自传时间轴
        """
        raw_text = text.strip()
        actor_id, actor_name, role_type = IdentityResolver.resolve_actor(channel, user_id, user_name)
        current_affection = self.timeline_hub.get_user_affection(actor_id)

        # 1. 提取主人事实画像
        master_facts = []
        if actor_id == "master":
            prof = self.timeline_hub.get_user_profile("master")
            if prof:
                master_facts = prof.get("facts", [])

        # 2. 检索时间轴经历
        recalled_texts = []
        recent_texts = []
        if len(raw_text) >= 2 and not raw_text.startswith(("/", "!", "#")):
            # 语义召回
            recalls = self.timeline_hub.recall_timeline(actor_id=actor_id, limit=2)
            recalled_texts = [f"[{r.event.time_str}] {r.event.narrative}" for r in recalls]

            # 若包含时序类词汇，注入近期经历流
            temporal_kws = ["刚才", "今天干", "今天做", "在干嘛", "忙什么", "干嘛去了", "今天过得", "最近干", "在做什么"]
            if any(k in raw_text for k in temporal_kws):
                recent_evts = self.timeline_hub.get_recent_stream(limit=3)
                recent_texts = [f"[{e.time_str}] ({e.source_module}) {e.narrative}" for e in recent_evts]

        # 3. 组装系统上下文
        system_context = self.persona_mgr.build_system_context(
            actor_id=actor_id,
            actor_name=actor_name,
            role_type=role_type,
            affection=current_affection,
            master_facts=master_facts,
            recalled_memories=recalled_texts,
            recent_stream=recent_texts,
        )

        # 4. 组装消息列表
        messages = []
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": raw_text})

        # 5. 调用大模型与工具循环
        raw_response, tool_logs = await self.llm.generate_with_tools(
            system_prompt=system_context,
            messages=messages,
            tool_registry=self.tools,
        )

        # 6. 人设后处理清洗
        final_speech = self.persona_mgr.post_process_response(raw_response)

        # 7. 沉淀本轮交互至自传时间轴
        try:
            self.timeline_hub.append_event(
                source_module=f"{channel}_interaction",
                actor_id=actor_id,
                actor_name=actor_name,
                narrative=f"{actor_name}说：“{raw_text}”，助手回答：“{final_speech}”",
                importance=2.0 if actor_id == "master" else 1.0,
                metadata={"channel": channel, "tool_count": len(tool_logs)}
            )
        except Exception as e:
            logger.debug(f"[AnanAgent] 沉淀时间轴经历异常: {e}")

        # 重新获取可能发生变化的好感度
        current_affection = self.timeline_hub.get_user_affection(actor_id)

        return AgentResponse(
            text=final_speech,
            actor_id=actor_id,
            actor_name=actor_name,
            current_affection=current_affection,
            executed_tools=tool_logs,
            channel=channel,
        )
