import asyncio
import os
import re
from pathlib import Path
from typing import Any, Optional

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Image, Plain
import sys
_CORE_DIR = Path(__file__).resolve().parent.parent.parent
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))
try:
    from agent_core import TimelineHub
    timeline_hub = TimelineHub()
except Exception as e:
    timeline_hub = None

ADMIN_ID = "1000000001"
BRIDGE_URLS = [
    "http://host.docker.internal:8765",
    "http://192.168.65.254:8765",
    "http://127.0.0.1:8765",
]
SECRET_TOKEN = "anan_agy_bridge_sec_8f7b2c9e1a4d"


async def call_bridge_api(
    endpoint: str,
    method: str = "POST",
    data: Optional[dict] = None,
    timeout_sec: int = 70,
) -> tuple[bool, dict]:
    """与宿主机反重力桥梁通信，强制关闭环境代理以直连宿主机网关"""
    headers = {
        "X-Bridge-Token": SECRET_TOKEN,
        "Content-Type": "application/json",
    }
    
    # 容器内直连 host 必须 trust_env=False 绕过 Clash 代理
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    
    for base_url in BRIDGE_URLS:
        url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                trust_env=False,
            ) as session:
                if method.upper() == "GET":
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            res_json = await resp.json()
                            return True, res_json
                        err_text = await resp.text()
                        logger.warning(f"[AnanBridge] 桥梁响应异常 ({resp.status}): {err_text}")
                else:
                    async with session.post(url, headers=headers, json=data or {}) as resp:
                        if resp.status == 200:
                            res_json = await resp.json()
                            return True, res_json
                        err_text = await resp.text()
                        logger.warning(f"[AnanBridge] 桥梁响应异常 ({resp.status}): {err_text}")
        except Exception as e:
            logger.debug(f"[AnanBridge] 连接 {url} 失败: {e}，尝试下一个地址...")
            continue

    return False, {"error": "宿主机反重力服务未响应，请检查宿主机 bridge 服务是否已启动~"}


def remove_ending_period(text: str) -> str:
    """遵循助手人设法则：句末绝不带句号（。）"""
    text = re.sub(r"。+$", "", text.strip())
    text = re.sub(r"(?<!\.)\.(?!\.)$", "", text).rstrip()
    return text


@register(
    "astrbot_plugin_anan_bridge",
    "主人 & 反重力",
    "助手反重力电脑控制桥：传话给反重力操控 Windows 电脑",
    "1.0.0",
)
class AnanBridgePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        logger.info("[AnanBridge] 助手反重力电脑控制桥已成功加载！")

    @filter.command("pc")
    async def pc_command(self, event: AstrMessageEvent):
        """【宿主机控制】指令传话给反重力执行电脑控制任务
        用法示例：
        /pc 状态 - 查看电脑 CPU/内存/显卡/磁盘状态
        /pc 截屏 - 抓取当前电脑屏幕画面
        /pc 锁屏 - 一键锁定电脑
        /pc powershell <命令> - 运行 PowerShell 指令
        /pc <自然语言需求> - 反重力智能执行
        """
        sender_id = str(event.get_sender_id() or "").strip()
        if sender_id != ADMIN_ID:
            yield event.plain_result("不要，你又不是主人，我才不听你的呢~")
            return

        # 提取参数
        raw_msg = (event.message_str or "").strip()
        parts = raw_msg.split(maxsplit=1)
        sub_cmd = parts[1].strip() if len(parts) > 1 else "状态"

        action = "auto"
        command = ""
        instruction = sub_cmd

        if sub_cmd in ("状态", "status", "info"):
            action = "status"
        elif sub_cmd in ("截屏", "截图", "screen", "screenshot"):
            action = "screenshot"
        elif sub_cmd in ("锁屏", "lock"):
            action = "lock"
        elif sub_cmd.lower().startswith(("ps ", "powershell ", "cmd ")):
            action = "powershell"
            command = sub_cmd.split(maxsplit=1)[1]

        payload = {
            "admin_id": sender_id,
            "action": action,
            "command": command,
            "instruction": instruction,
        }

        ok, resp = await call_bridge_api("execute", method="POST", data=payload)
        if not ok:
            err_msg = resp.get("error", "连接宿主机桥梁失败")
            yield event.plain_result(remove_ending_period(f"唔，反重力那边没反应诶，提示：{err_msg}"))
            return

        res_type = resp.get("type", "text")

        # 处理图片（截屏）
        if res_type == "image":
            container_path = resp.get("container_path")
            msg_text = resp.get("message", "屏幕截屏已生成~")
            if container_path and os.path.exists(container_path):
                chain = MessageChain().message(f"{msg_text}\n").file_image(container_path)
                yield event.chain_result(chain)
                return
            else:
                yield event.plain_result(remove_ending_period(f"截屏成功生成，但容器映射路径未找到文件：{container_path}"))
                return

        # 记录到自传式时间轴
        if timeline_hub:
            try:
                narrative = f"响应主人指令，完成了电脑操控任务：{sub_cmd}"
                if res_type == "image":
                    narrative = "响应主人指令，抓取并发送了宿主机电脑屏幕截屏"
                elif action == "lock":
                    narrative = "响应主人指令，成功锁定了宿主机电脑屏幕"
                elif action == "powershell":
                    narrative = f"响应主人指令，在宿主机执行了PowerShell脚本：{command[:40]}"
                timeline_hub.append_event(
                    source_module="pc_bridge",
                    actor_id="master",
                    narrative=narrative,
                    emotional_impact=1,
                    importance=2.5,
                    metadata={"sub_cmd": sub_cmd, "action": action}
                )
            except Exception as e:
                logger.debug(f"[AnanBridge] 记录时间轴异常: {e}")

        # 处理常规文本
        msg = resp.get("message", "执行完成~")
        yield event.plain_result(remove_ending_period(msg))

    @filter.command("电脑")
    async def pc_chinese_command(self, event: AstrMessageEvent):
        """支持中文指令前缀：/电脑 状态、/电脑 截屏 等"""
        async for res in self.pc_command(event):
            yield res

    @filter.llm_tool(name="control_host_pc_via_antigravity")
    async def control_host_pc_via_antigravity(
        self,
        event: AstrMessageEvent,
        instruction: str,
        action: str = "auto",
    ) -> str:
        """当主人需要查询电脑状态、查看电脑屏幕截屏、锁屏、启动软件、或者让反重力在电脑上执行系统/代码任务时，调用此工具将指令传话给反重力执行。
        
        Args:
            instruction (str): 主人的具体要求或命令内容（如：'查看当前电脑负载'、'截取屏幕'、'锁定屏幕'、'查看某个进程'等）。
            action (str): 操作类型，可选：'status'(状态)、'screenshot'(截屏)、'lock'(锁屏)、'powershell'(执行脚本)、'auto'(反重力智能规划)。默认 'auto'。
        """
        sender_id = str(event.get_sender_id() or "").strip()
        if sender_id != ADMIN_ID:
            return "权限被拒绝：只有主人可以控制电脑。"

        payload = {
            "admin_id": sender_id,
            "action": action,
            "instruction": instruction,
        }

        logger.info(f"[AnanBridge] 助手通过 LLM Tool 传话给反重力: {instruction} (action={action})")
        ok, resp = await call_bridge_api("execute", method="POST", data=payload)
        if not ok:
            err_msg = resp.get("error", "连接宿主机桥梁失败")
            return f"反重力宿主机桥梁未能响应：{err_msg}。请检查服务是否开启。"

        res_type = resp.get("type", "text")
        if res_type == "image":
            container_path = resp.get("container_path")
            if container_path and os.path.exists(container_path):
                try:
                    await event.send(MessageChain().message("喏，现在的电脑屏幕给你截过来了~\n").file_image(container_path))
                    return "屏幕截图已成功发送到聊天中。请用助手自然、随性贴心的语气回应主人。"
                except Exception as e:
                    logger.error(f"[AnanBridge] 发送截屏图片失败: {e}")
                    return f"截屏生成成功，但发送图片组件时出错: {e}"

        # 记录到自传式时间轴
        if timeline_hub:
            try:
                timeline_hub.append_event(
                    source_module="pc_bridge",
                    actor_id="master",
                    narrative=f"助手自主调用电脑控制工具：{instruction}",
                    emotional_impact=1,
                    importance=2.5,
                    metadata={"instruction": instruction, "action": action}
                )
            except Exception as e:
                logger.debug(f"[AnanBridge] 记录时间轴异常: {e}")

        msg = resp.get("message", "任务已由反重力执行完毕。")
        return f"反重力执行反馈结果如下：\n{msg}\n请以助手随性自然、末尾不带句号的口吻向主人汇报。"
