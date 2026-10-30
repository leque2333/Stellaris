# -*- coding: utf-8 -*-
"""
Stellaris Core - 自主记忆器官工具箱 (Memory Tools)
借鉴：Letta (原 MemGPT) 智能体自省修改核心记忆架构
职责：
1. 将记忆维护的主动权直接赋予大模型大脑
2. 提供自主沉淀偏好、更正过时事实、检索深度日记的原生工具
"""

import logging
from typing import Optional

from ..tools import ToolRegistry
from .fact_manager import FactManager
from .reflection_engine import ReflectionEngine

logger = logging.getLogger("MemoryTools")


def register_memory_tools(
    tools: ToolRegistry,
    fact_mgr: FactManager,
    reflection_engine: Optional[ReflectionEngine] = None
):
    """
    向统一工具注册中心挂载智能体自主记忆器官
    """

    @tools.register(
        name="record_master_preference",
        description="当主人在对话中表达了明确的个人偏好、作息变动、生活习惯或长效约定（如：'我开始学Rust了'、'以后别叫我熬夜'）时主动调用此工具沉淀至长效事实库。"
    )
    def record_master_preference(category: str, fact_content: str) -> str:
        clean_fact = fact_content.strip()
        if not clean_fact:
            return "事实内容不能为空"
        fact_id = fact_mgr.add_fact(user_id="master", content=clean_fact, category=category)
        logger.info(f"[MemoryTools] 智能体自发记录主人长效事实: {clean_fact} (ID: {fact_id})")
        return f"已成功记入长效记忆档案：[{category}] {clean_fact}"

    @tools.register(
        name="update_master_preference",
        description="当主人明确表示过去的某个习惯、偏好或状态已经发生变化或作废时（如：'我换成Mac了，不折腾Windows了'）调用此工具更新覆盖旧事实。"
    )
    def update_master_preference(old_fact_keyword: str, new_fact_content: str) -> str:
        # 寻找包含 old_fact_keyword 的旧事实
        active_facts = fact_mgr.get_active_facts(user_id="master", limit=20)
        matched_old = None
        for f in active_facts:
            if old_fact_keyword.lower() in f["content"].lower():
                matched_old = f
                break

        if matched_old:
            new_id = fact_mgr.supersede_fact(
                old_fact_id=matched_old["fact_id"],
                new_content=new_fact_content.strip(),
                category=matched_old.get("category", "preference")
            )
            return f"已成功更正长效记忆：废止原先的「{matched_old['content']}」，更新为「{new_fact_content}」"
        else:
            # 未找到匹配旧事实，直接作为新事实记录
            fact_id = fact_mgr.add_fact(user_id="master", content=new_fact_content.strip())
            return f"未匹配到明确的旧记录，已直接新建记录为长效事实：{new_fact_content}"

    if reflection_engine:
        @tools.register(
            name="search_historical_digests",
            description="当对方问起数周或数月前的宏观历史经历、重要里程碑，或在近期时间轴无法查到时调用，翻查长期经历日记档案。"
        )
        def search_historical_digests(keyword: str) -> str:
            digests = reflection_engine.recall_digests(query=keyword, limit=3)
            if not digests:
                return f"在长程经历日记中未找到与 '{keyword}' 相关的历史记录。"
            lines = [f"[{d['date_str']}] {d['narrative']}" for d in digests]
            return "查找到的长程经历日记：\n" + "\n".join(lines)
