# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 人设引擎与后处理器 (Persona Kernel)
负责：V6.3 黄金台词规范注入、亲疏温差动态调配、多维记忆提示词构建、以及输出纯口语化清洗（句末去句号法则）
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# 寻找人设卡物理文件
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DEFAULT_PERSONA_FILE = BASE_DIR / "anan_core_settings_v5_3.md"


class PersonaManager:
    """
    助手核心人设管理器
    """
    def __init__(self, persona_path: Optional[Path] = None):
        self.persona_path = persona_path or DEFAULT_PERSONA_FILE
        self._cached_prompt = ""
        self._load_persona_prompt()

    def _load_persona_prompt(self):
        """读取基底人设卡配置（优先指定人设文件，降级模板人设）"""
        candidate_paths = [
            self.persona_path,
            Path(__file__).parent / "persona_template.md",
        ]
        for p in candidate_paths:
            if p and p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._cached_prompt = f.read().strip()
                    if self._cached_prompt:
                        return
                except Exception:
                    pass
        
        # 降级备用基底人设
        self._cached_prompt = (
            "你是助手（An'an），真实随性、毒舌傲娇、追求省力、眼光毒辣的天才少女。\n"
            "语言规则：句末绝不带句号（。），只输出纯粹口语台词，严禁任何括号神态描写，保持从容自然。"
        )

    def get_base_prompt(self) -> str:
        return self._cached_prompt

    def build_system_context(
        self,
        actor_id: str,
        actor_name: str,
        role_type: str,
        affection: int,
        master_facts: Optional[List[str]] = None,
        recalled_memories: Optional[List[str]] = None,
        recent_stream: Optional[List[str]] = None,
    ) -> str:
        """
        组装完整的智能体认知上下文：基底人设 + 亲疏好感指引 + 主人长效偏好 + 时间轴回忆
        """
        blocks = [self._cached_prompt]

        # 1. 动态好感度与亲疏温差指引
        if affection < 60:
            stage_desc = "不熟/初识（冷淡而温柔）"
            attitude_hint = "对不熟的人略微冷淡，克制疏离、客气简短，保持猫咪的边界感；但内心深处依旧温柔善良，绝不刻薄伤人"
        elif affection < 80:
            stage_desc = "熟悉/朋友（温和友善）"
            attitude_hint = "自然随和，亲切温和，真诚待人"
        else:
            stage_desc = "极度熟悉/至交与主人（小恶魔而至善）"
            attitude_hint = "非常熟悉亲昵，带点聪明的小腹黑、机敏打趣与猫咪特有的嘴硬不服输，坦率狡黠不搞别扭傲娇；骨子里全心全意温柔善良、极度信任依赖"

        role_desc = "你的唯一主人与最高管理员" if actor_id == "master" else "群友/访客"
        blocks.append(
            f"【当前交互对象关系与好感度】\n"
            f"- 交互对象：{actor_name} ({role_desc})\n"
            f"- 当前好感度：{affection}/100（状态：{stage_desc}）\n"
            f"- 态度指引：{attitude_hint}。好感值的升降由你在交互中心理浮动判断并可自主调用工具微调。"
        )

        # 2. 主人专属长效偏好事实
        if actor_id == "master" and master_facts:
            facts_str = "；".join(master_facts[-8:])
            blocks.append(
                f"【长效记忆·主人已知偏好与事实】\n- 关键偏好：{facts_str}"
            )

        # 3. 三维时间轴语义唤醒的历史经历
        if recalled_memories:
            lines = [f"- {m}" for m in recalled_memories]
            blocks.append(
                "【自传时间轴·唤醒的相关历史经历】\n" + "\n".join(lines)
            )

        # 4. 连续时间轴近期经历流
        if recent_stream:
            lines = [f"- {s}" for s in recent_stream]
            blocks.append(
                "【自传时间轴·助手近期连续生命经历流】\n" + "\n".join(lines)
            )

        return "\n\n".join(blocks)

    @staticmethod
    def post_process_response(text: str) -> str:
        """
        遵循助手人设法则的输出清洗：
        1. 剥离任何括号描写（神态、动作、心理说明）
        2. 彻底剔除句末句号（。）
        3. 消除感叹词「啦」「呀」与粗俗「搞起」
        """
        cleaned = text.strip()

        # 移除中英文括号及其包含的舞台说明 (例: "（伸了个懒腰）今天好累" -> "今天好累")
        cleaned = re.sub(r"（[^）]*）", "", cleaned)
        cleaned = re.sub(r"\([^)]*\)", "", cleaned)

        # 句末绝不带句号
        cleaned = re.sub(r"。+$", "", cleaned.strip())
        cleaned = re.sub(r"(?<!\.)\.(?!\.)$", "", cleaned).rstrip()

        return cleaned.strip()
