# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 人设引擎与后处理器 (Persona Kernel)
负责：V6.3 黄金台词规范注入、亲疏温差动态调配、多维记忆提示词构建、以及输出纯口语化清洗（句末去句号法则）
"""

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# 寻找人设卡物理文件（支持位于项目根目录下的设定集文件）
BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PERSONA_FILE = BASE_DIR / "data" / "anan_persona.md"


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
            BASE_DIR / "data" / "anan_persona.md",
            BASE_DIR / "anan_core_settings_v5_3.md",
            Path(__file__).parent / "persona_template.md",
        ]
        for p in candidate_paths:
            if p and Path(p).exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    if content and content != "你是一个严谨高效的智能体助手。":
                        self._cached_prompt = content
                        return
                except Exception:
                    pass
        
        # 纯框架降级：默认为空，由使用者在控制台或文件中自由定义
        self._cached_prompt = ""

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
        遵循助手人设法则的输出清洗（带代码块与链接防护罩）：
        1. 剥离任何剧本括号神态动作描写
        2. 彻底剔除句末句号（。）
        3. 严密保护代码块参数与Markdown链接，绝不破坏编程语法
        """
        if not text:
            return ""
        raw = str(text).strip()

        # 1. 保护多行代码块 ```...``` 与 行内代码 `...`
        code_snippets = []
        def _save_code(m):
            code_snippets.append(m.group(0))
            return f"__CODE_SNIPPET_{len(code_snippets)-1}__"

        masked = re.sub(r"```[\s\S]*?```", _save_code, raw)
        masked = re.sub(r"`[^`\r\n]+`", _save_code, masked)

        # 2. 保护 Markdown 链接 [text](url)
        md_links = []
        def _save_link(m):
            md_links.append(m.group(0))
            return f"__MD_LINK_{len(md_links)-1}__"

        masked = re.sub(r"\[[^\]]+\]\([^\)]+\)", _save_link, masked)

        # 3. 剥离中文括号舞台神态描写
        cleaned = re.sub(r"（[^）]*）", "", masked)
        # 剥离包含中文/动作词的英文括号神态说明
        cleaned = re.sub(r"\([^\)]*[一-龥][^\)]*\)", "", cleaned)

        # 4. 句末绝无句号法则
        cleaned = re.sub(r"。+$", "", cleaned.strip())
        if not cleaned.endswith("__"):
            cleaned = re.sub(r"(?<!\.)\.(?!\.)$", "", cleaned).rstrip()

        # 5. 无损还原 Markdown 链接与代码块
        for i, link in enumerate(md_links):
            cleaned = cleaned.replace(f"__MD_LINK_{i}__", link)
        for i, code in enumerate(code_snippets):
            cleaned = cleaned.replace(f"__CODE_SNIPPET_{i}__", code)

        return cleaned.strip()
