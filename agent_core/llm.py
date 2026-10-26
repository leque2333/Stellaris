# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 统一大模型引擎 (LLM Engine)
直连 Google Gemini API，内置自适应代理路由、Function Calling 自动化工具循环、Token 监视与容灾降级
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger("AgentLLM")

# 尝试自动定位 AstrBot cmd_config.json 获取凭据与代理
CONFIG_FILE_CANDIDATES = [
    Path("/AstrBot/data/cmd_config.json"),
    Path(__file__).resolve().parent.parent.parent / "astrbot" / "data" / "cmd_config.json",
    Path("d:/机器人/astrbot/data/cmd_config.json"),
]


class LLMClient:
    """
    Gemini 统一大模型调用中枢
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-3.8-flash",
        proxy: Optional[str] = None,
        api_base: str = "https://generativelanguage.googleapis.com",
    ):
        env_key = os.getenv("GEMINI_API_KEY", "")
        if "你的真实" in env_key or "placeholder" in env_key:
            env_key = ""
        self.api_key = api_key or env_key
        self.model = model
        self.proxy = proxy or os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY")
        self.api_base = api_base.rstrip("/")
        
        # 若未显式传入配置，尝试从 cmd_config.json 自动提取
        if not self.api_key:
            self._load_from_config()

    def _load_from_config(self):
        is_docker = Path("/.dockerenv").exists()
        for p in CONFIG_FILE_CANDIDATES:
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    # 提取 key 与 proxy
                    for ps in cfg.get("provider_sources", []):
                        raw_k = ps.get("key") or ps.get("api_key")
                        if raw_k:
                            if isinstance(raw_k, list) and raw_k:
                                self.api_key = str(raw_k[0]).strip()
                            else:
                                self.api_key = str(raw_k).strip()
                            if ps.get("api_base"):
                                self.api_base = ps.get("api_base").rstrip("/")
                            
                            # 优先从该 provider_source 读取代理配置
                            if ps.get("proxy"):
                                prx = ps.get("proxy")
                                if not is_docker and ("192.168.65.254" in prx or "host.docker.internal" in prx):
                                    self.proxy = "http://127.0.0.1:7897"
                                else:
                                    self.proxy = prx
                            break

                    # 全局代理 fallback
                    if not self.proxy and cfg.get("http_proxy"):
                        raw_proxy = cfg.get("http_proxy")
                        if not is_docker and ("host.docker.internal" in raw_proxy or "192.168.65.254" in raw_proxy):
                            self.proxy = "http://127.0.0.1:7897"
                        else:
                            self.proxy = raw_proxy

                    # 若依然没有代理且运行在宿主机，默认尝试本地 Clash 7897
                    if not self.proxy and not is_docker:
                        self.proxy = "http://127.0.0.1:7897"

                    logger.info(f"[LLMClient] 成功加载 Gemini 凭证 (Proxy: {self.proxy})")
                    return
                except Exception as e:
                    logger.debug(f"[LLMClient] 读取配置 {p} 失败: {e}")

    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: List[Dict[str, Any]],
        tool_registry: Optional[Any] = None,
        max_tool_turns: int = 4,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        带工具调用的自主推理闭环 (ReAct Tool Loop):
        1. 携带工具声明发送请求给大模型
        2. 若模型决策需要调用工具 (functionCall)，本地执行工具并回传结果
        3. 重复循环直到模型输出最终人类回复
        返回: (final_text, executed_tools_log)
        """
        if not self.api_key:
            return "（提示：未检测到有效 Gemini API Key，请配置 cmd_config.json 或 GEMINI_API_KEY）", []

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

        for turn in range(max_tool_turns):
            req_body: Dict[str, Any] = {
                "system_instruction": {
                    "parts": [{"text": system_prompt}]
                },
                "contents": current_contents,
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 1024,
                }
            }
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
