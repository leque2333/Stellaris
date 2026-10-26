# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 统一工具总线 (Tool & Skill Engine)
提供声明式工具注册 (@tool_registry.register)、自动类型推导生成 Schema、异步/同步执行与统一分发
"""

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("AgentTools")


@dataclass
class ToolDefinition:
    """工具元信息与执行体"""
    name: str
    description: str
    func: Callable
    parameters: Dict[str, Any]
    is_async: bool = False


class ToolRegistry:
    """
    统一工具注册中心
    允许任意业务模块（PC控制、B站巡检、网页搜索、游戏Wiki）以声明式方式一键挂载
    """
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        """
        装饰器：注册一个工具函数
        示例：
        @tool_registry.register(name="lock_pc", description="锁定宿主机电脑屏幕")
        async def lock_pc() -> str:
            ...
        """
        def decorator(func: Callable):
            tool_name = name or func.__name__
            tool_desc = (description or func.__doc__ or "No description provided").strip()
            
            # 若未显式提供 parameters，自动从函数类型注解推断 Schema
            schema = parameters or self._infer_schema(func)
            is_async = inspect.iscoroutinefunction(func)

            self._tools[tool_name] = ToolDefinition(
                name=tool_name,
                description=tool_desc,
                func=func,
                parameters=schema,
                is_async=is_async,
            )
            logger.info(f"[ToolRegistry] 已注册智能体工具: {tool_name}")
            return func

        return decorator

    def _infer_schema(self, func: Callable) -> Dict[str, Any]:
        """从函数签名自动推导 JSON Schema"""
        sig = inspect.signature(func)
        properties = {}
        required = []

        type_mapping = {
            int: "integer",
            float: "number",
            str: "string",
            bool: "boolean",
            list: "array",
            dict: "object",
        }

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = type_mapping.get(param.annotation, "string")
            param_def: Dict[str, Any] = {"type": param_type}

            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                param_def["default"] = param.default

            properties[param_name] = param_def

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def list_tools(self) -> List[ToolDefinition]:
        return list(self._tools.values())

    def get_gemini_tools_schema(self) -> List[Dict[str, Any]]:
        """生成符合 Gemini / Google GenAI 标准的 Function Declarations"""
        declarations = []
        for tool in self._tools.values():
            declarations.append({
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            })
        return [{"function_declarations": declarations}] if declarations else []

    async def execute(self, name: str, args: Dict[str, Any]) -> Any:
        """分发并异步执行工具"""
        tool = self.get_tool(name)
        if not tool:
            return f"错误：未找到名为 '{name}' 的可用工具。"

        try:
            if tool.is_async:
                res = await tool.func(**args)
            else:
                res = tool.func(**args)
            return res
        except Exception as e:
            logger.error(f"[ToolRegistry] 执行工具 '{name}' 异常: {e}")
            return f"执行工具 '{name}' 时出错: {str(e)}"


# 全局单例
tool_registry = ToolRegistry()
