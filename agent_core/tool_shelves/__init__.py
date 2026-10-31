# -*- coding: utf-8 -*-
"""工具按需加载（Tool Shelves）。

在 /v1 网关把上游送来的全量 tools 按"大类"裁剪后再交给主力 AI：
1. 纯语气词 / 工具结果回灌 / 无 tools 请求 → 硬规则，0 次模型调用；
2. 其余 → 只把「当前时间 + 最近三句 + 大类名与一句话简介」发给执行 AI（DeepSeek），
   由它挑类别；
3. 只把选中类别下的工具 + 常驻工具塞进请求；判定不用工具时，
   tools / tool_choice / parallel_tool_calls 三个字段整段删除。

任何异常都不允许影响对话：闸门失败一律全量放行。
"""

from .cleaner import compress_messages, compress_tool_result  # noqa: F401
from .gate import ToolGate, tool_gate  # noqa: F401

__all__ = ["ToolGate", "tool_gate", "compress_messages", "compress_tool_result"]
