# -*- coding: utf-8 -*-
"""
助手独立智能体交互终端 (CLI Runner)
直接在终端与助手自由对话，调试人设语感、时间轴记忆唤醒与自主工具调用
使用方式：
    python -m agent_core.cli
"""

import asyncio
import os
import sys

from .agent import AnanAgent


async def main():
    print("=" * 60)
    print("✨ 助手独立智能体 (AnanAgent V6.3) 交互终端已启动 ✨")
    print("输入 'exit' 或 'quit' 退出终端；输入 '/status' 查看当前好感度与时间轴状态")
    print("=" * 60)

    agent = AnanAgent()
    user_id = "master"
    user_name = "主人"

    while True:
        try:
            user_input = input(f"\n[{user_name}]: ").strip()
            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("\n[助手]: 去忙你的吧，有事随时叫我~")
                break

            if user_input == "/status":
                aff = agent.timeline_hub.get_user_affection("master")
                recent = agent.timeline_hub.get_recent_stream(limit=3)
                print(f"\n📊 [中枢状态] 当前主人好感度: {aff}/100")
                print("⏳ [最新时间轴事件]:")
                for ev in recent:
                    print(f"   - [{ev.time_str}] ({ev.source_module}) {ev.narrative[:50]}...")
                continue

            # 调用智能体
            response = await agent.chat(
                text=user_input,
                channel="cli",
                user_id=user_id,
                user_name=user_name,
            )

            # 输出工具调用日志（如果有）
            if response.executed_tools:
                for t in response.executed_tools:
                    print(f"   ⚙️ [自主调用工具] {t['tool']} ➜ {t['result']}")

            print(f"[助手] (好感:{response.current_affection}): {response.text}")

        except (KeyboardInterrupt, EOFError):
            print("\n[助手]: 溜啦~")
            break
        except Exception as e:
            print(f"交互出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())
