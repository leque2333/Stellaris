# -*- coding: utf-8 -*-
"""
Stellaris Core - 统一大模型引擎 (LLM Engine)
直连 Google Gemini API / 项目内置模型凭据池，支持无审查安全策略配置 (BLOCK_NONE)
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("AgentLLM")

# 自动定位项目模型与凭据配置
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE_CANDIDATES = [
    BASE_DIR / "data" / "models_config.json",
    BASE_DIR / "data" / "compute_providers.json",
]


class LLMClient:
    """
    Gemini 统一大模型调用中枢 (支持无审查安全设置与自由对话模式)
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-3.8-flash",
        proxy: Optional[str] = None,
        api_base: str = "https://generativelanguage.googleapis.com",
        uncensored: bool = True,
    ):
        env_key = os.getenv("GEMINI_API_KEY", "")
        if "你的真实" in env_key or "placeholder" in env_key:
            env_key = ""
        self.api_key = api_key or env_key
        self.model = model
        self.proxy = proxy or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
        self.api_base = api_base.rstrip("/")
        self.uncensored = uncensored
        
        # 若未显式传入配置，尝试从统一配置中自动提取
        if not self.api_key:
            self._load_from_config()

    def _load_from_config(self):
        for p in CONFIG_FILE_CANDIDATES:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    
                    # 1. 优先从 models_config.json 中的 master_credentials 提取真实活跃凭据
                    master_creds = cfg.get("master_credentials", [])
                    for cred in master_creds:
                        if cred.get("is_active", True) and cred.get("api_key"):
                            self.api_key = cred["api_key"]
                            if cred.get("model"):
                                self.model = cred["model"]
                            return
                        elif cred.get("api_key"):
                            self.api_key = cred["api_key"]
                            if cred.get("model"):
                                self.model = cred["model"]
                            return

                    # 2. 从 compute_providers.json 提取
                    provs = cfg.get("providers", {})
                    if "google_gemini_official" in provs:
                        k = provs["google_gemini_official"].get("api_key")
                        if k and k != "none":
                            self.api_key = k
                            return
                    
                    # 3. 从 models_config.json api_pool 提取
                    for item in cfg.get("api_pool", []):
                        k = item.get("key") or item.get("api_key")
                        if k:
                            self.api_key = k
                            return
                except Exception as e:
                    logger.debug(f"[LLMClient] 读取配置 {p} 失败: {e}")

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tool_registry: Optional[Any] = None,
        max_tool_turns: int = 4,
        temperature: float = 0.8,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        带工具调用的自主推理闭环 (ReAct Tool Loop):
        1. 携带工具声明与 BLOCK_NONE 无审查安全策略发送请求给大模型
        2. 若模型决策需要调用工具 (functionCall)，本地执行工具并回传结果
        3. 重复循环直到模型输出最终人类回复
        返回: (final_text, executed_tools_log)
        """
        if not self.api_key:
            return "（提示：未检测到有效大模型 API Key，请配置 models_config.json 或环境变量）", []

        if "generativelanguage.googleapis.com" not in self.api_base:
            return await self._generate_openai(
                system_prompt, messages, tool_registry, max_tool_turns, temperature
            )

        url = f"{self.api_base}/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        executed_tool_logs: List[Dict[str, Any]] = []

        # 构造消息历史
        contents = []
        for m in messages:
            role = "user" if m["role"] in ("user", "human") else "model"
            contents.append({
                "role": role,
                "parts": [{"text": str(m["content"])}]
            })

        # 组装工具
        tools_payload = []
        if tool_registry:
            tools_payload = tool_registry.get_gemini_tools_schema()

        current_contents = list(contents)

        # 无审查安全策略（关闭全部五项安全阈值过滤）
        safety_settings = []
        if self.uncensored:
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
            ]

        for turn in range(max_tool_turns):
            req_body: Dict[str, Any] = {
                "system_instruction": {
                    "parts": [{"text": system_prompt}]
                },
                "contents": current_contents,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 2048,
                }
            }
            if safety_settings:
                req_body["safetySettings"] = safety_settings

            if tools_payload:
                req_body["tools"] = tools_payload

            # 调用接口
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                try:
                    async with session.post(
                        url,
                        json=req_body,
                        proxy=self.proxy,
                        timeout=aiohttp.ClientTimeout(total=45),
                    ) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            logger.error(f"[LLMClient] Gemini 响应错误 ({resp.status}): {err_text}")
                            return f"唔，思考时接口发生异常了 ({resp.status})", executed_tool_logs

                        res_json = await resp.json()
                except Exception as e:
                    logger.error(f"[LLMClient] 请求大模型失败: {e}")
                    return f"连接模型中枢超时啦：{e}", executed_tool_logs

            candidates = res_json.get("candidates", [])
            if not candidates:
                # 检查是否命中内容过滤器
                prompt_feedback = res_json.get("promptFeedback", {})
                block_reason = prompt_feedback.get("blockReason")
                if block_reason:
                    return f"（底层过滤器拦截: {block_reason}）", executed_tool_logs
                return "……", executed_tool_logs

            candidate = candidates[0]
            content_part = candidate.get("content", {})
            parts = content_part.get("parts", [])

            # 检查是否有工具调用
            tool_calls = [p.get("functionCall") for p in parts if p.get("functionCall")]

            if not tool_calls:
                # 纯文本回复，推理结束
                final_text = "".join([p.get("text", "") for p in parts if p.get("text")])
                return final_text, executed_tool_logs

            # 模型决定调用工具：执行工具并准备下一轮汇报
            current_contents.append(content_part)

            for tc in tool_calls:
                fn_name = tc.get("name")
                fn_args = tc.get("args", {})
                logger.info(f"[LLMClient] 智能体自主调用工具: {fn_name}({fn_args})")

                tool_result = await tool_registry.execute(fn_name, fn_args)
                executed_tool_logs.append({
                    "tool": fn_name,
                    "args": fn_args,
                    "result": tool_result
                })

                # 向上下文追加工具返回结果 (Gemini 规范要求 role 为 'user')
                current_contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": fn_name,
                            "response": {"result": str(tool_result)}
                        }
                    }]
                })

        return "思考轮次过多，暂时停下啦", executed_tool_logs

    async def _generate_openai(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tool_registry: Optional[Any],
        max_tool_turns: int,
        temperature: float,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """OpenAI 兼容协议（DeepSeek / 自定义中转）"""
        tools = []
        if tool_registry:
            for tool in tool_registry.list_tools():
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                })
        history = [{"role": "system", "content": system_prompt}]
        history.extend(messages)
        logs: List[Dict[str, Any]] = []
        url = f"{self.api_base.rstrip('/')}/chat/completions"

        for _ in range(max_tool_turns):
            req_body: Dict[str, Any] = {
                "model": self.model,
                "messages": history,
                "temperature": temperature,
                "max_tokens": 2048,
            }
            if tools:
                req_body["tools"] = tools
                req_body["tool_choice"] = "auto"
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                try:
                    async with session.post(
                        url,
                        json=req_body,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        proxy=self.proxy,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        if resp.status != 200:
                            err_text = await resp.text()
                            logger.error(f"[LLMClient] OpenAI 响应错误 ({resp.status}): {err_text}")
                            return f"唔，思考时接口发生异常了 ({resp.status})", logs
                        res_json = await resp.json()
                except Exception as e:
                    logger.error(f"[LLMClient] 请求 OpenAI 兼容接口失败: {e}")
                    return f"连接模型中枢超时啦：{e}", logs

            choices = res_json.get("choices") or []
            if not choices:
                return "……", logs
            message = choices[0].get("message") or {}
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                return str(message.get("content") or ""), logs

            history.append({
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                fn = call.get("function") or {}
                name = fn.get("name") or ""
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                result = await tool_registry.execute(name, args)
                logs.append({"tool": name, "args": args, "result": result})
                history.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "content": str(result),
                })
        return "思考轮次过多，暂时停下啦", logs
