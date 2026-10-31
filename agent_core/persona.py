# -*- coding: utf-8 -*-
"""
Stellaris Core - 人设引擎与后处理器 (Persona Kernel)
负责：基底人设卡装配、用户自定义好感度温差系统、多维记忆提示词构建、以及用户自定义口语正则清洗
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PersonaKernel")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEFAULT_PERSONA_FILE = DATA_DIR / "persona.md"
RULES_FILE = DATA_DIR / "persona_rules.json"

DEFAULT_RULES: Dict[str, Any] = {
    "affection_tiers": [
        {
            "id": "tier_cold",
            "min_score": 0,
            "max_score": 39,
            "stage_name": "初识与疏离（克制边界）",
            "attitude_hint": "对不熟的人克制客气，简短得体，保持分寸感与独立边界，不主动展开漫无目的的闲聊"
        },
        {
            "id": "tier_warm",
            "min_score": 40,
            "max_score": 79,
            "stage_name": "熟悉与朋友（自然随性）",
            "attitude_hint": "自然随和，亲切温和，真诚待人，带有日常朋友般的从容与陪伴感"
        },
        {
            "id": "tier_intimate",
            "min_score": 80,
            "max_score": 100,
            "stage_name": "极度信任与至交（亲近默契）",
            "attitude_hint": (
                "非常熟悉亲昵，机敏打趣，言语松弛自如，坦率真诚不别扭；"
                "面对好感度 80 以上的人，厌世度几乎完全消失（仅留 2% 微妙的漫不经心游离感），"
                "活力值显著提高，喜欢在言语中抛出机敏打趣与熟人间的小互动，"
                "被识破时一声轻快的“嗯哼～”，展现出极度信任与依赖的温热内核。"
            )
        }
    ],
    "speech_regex_rules": [
        {
            "id": "rule_strip_action_brackets_zh",
            "name": "中文神态动作描写括号过滤",
            "pattern": "（[^）]*）",
            "replacement": "",
            "description": "剥离中文全角括号内的舞台神态描写、心理活动或非台词说明",
            "is_active": True
        },
        {
            "id": "rule_strip_action_brackets_en",
            "name": "英文动作描写括号过滤",
            "pattern": r"\([^\)]*[一-龥][^\)]*\)",
            "replacement": "",
            "description": "剥离包含中文字符的半角英文括号内动作或神态描写",
            "is_active": True
        },
        {
            "id": "rule_strip_trailing_periods",
            "name": "句末句号彻底剔除",
            "pattern": r"。+$|(?<!\.)\.(?!\.)$",
            "replacement": "",
            "description": "去除台词末尾的句号，保持随性自然的真人呼吸感",
            "is_active": True
        }
    ]
}


class PersonaManager:
    """
    智能体核心人设与规则管理器
    """
    def __init__(self, persona_path: Optional[Path] = None, rules_path: Optional[Path] = None):
        self.persona_path = persona_path or DEFAULT_PERSONA_FILE
        self.rules_path = rules_path or RULES_FILE
        self._cached_prompt = ""
        self._rules: Dict[str, Any] = {}
        self._load_persona_prompt()
        self.load_rules()

    def _load_persona_prompt(self):
        """读取基底人设卡配置（优先指定人设文件，降级模板人设）"""
        candidate_paths = [
            self.persona_path,
            DATA_DIR / "persona.md",
            DATA_DIR / "anan_persona.md",
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

        self._cached_prompt = "# 人设：助手\n\n你是一个严谨高效的智能体助手。"

    def load_rules(self) -> Dict[str, Any]:
        """读取用户自定义的好感度温差与口语正则规则"""
        if self.rules_path.exists():
            try:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._rules = {
                            "affection_tiers": data.get("affection_tiers", DEFAULT_RULES["affection_tiers"]),
                            "speech_regex_rules": data.get("speech_regex_rules", DEFAULT_RULES["speech_regex_rules"]),
                        }
                        return self._rules
            except Exception as e:
                logger.warning(f"[PersonaManager] 读取规则文件 {self.rules_path} 异常: {e}，将采用默认规则")

        self._rules = json.loads(json.dumps(DEFAULT_RULES))
        self.save_rules(self._rules)
        return self._rules

    def save_rules(self, rules: Dict[str, Any]) -> bool:
        """保存用户自定义的好感度温差与口语正则规则"""
        try:
            self.rules_path.parent.mkdir(parents=True, exist_ok=True)
            tiers = rules.get("affection_tiers", self._rules.get("affection_tiers", DEFAULT_RULES["affection_tiers"]))
            regex_rules = rules.get("speech_regex_rules", self._rules.get("speech_regex_rules", DEFAULT_RULES["speech_regex_rules"]))

            clean_rules = {
                "affection_tiers": tiers,
                "speech_regex_rules": regex_rules,
            }
            with open(self.rules_path, "w", encoding="utf-8") as f:
                json.dump(clean_rules, f, ensure_ascii=False, indent=2)
            self._rules = clean_rules
            logger.info("[PersonaManager] 用户自定义温差与口语规则已成功持久化")
            return True
        except Exception as e:
            logger.error(f"[PersonaManager] 保存规则异常: {e}")
            return False

    def get_rules(self) -> Dict[str, Any]:
        """获取当前生效的自定义规则"""
        if not self._rules:
            self.load_rules()
        return self._rules

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
        knowledge: Optional[List[str]] = None,
        recent_stream: Optional[List[str]] = None,
        base_prompt: Optional[str] = None,
        relations: Optional[List[str]] = None,
    ) -> str:
        """
        组装完整的智能体认知上下文：基底人设 + 用户自定义亲疏好感温差 + 主人长效偏好 + 时间轴回忆
        base_prompt 可覆盖基底人设卡（档案里按段编辑的自我人设）
        """
        blocks = [self._cached_prompt if base_prompt is None else base_prompt]

        role_desc = "你的唯一主人与最高管理员" if actor_id == "master" else "群友/访客"

        if affection is None:
            # 第一次交流：还没有形成印象，不预设亲近
            blocks.append(
                f"【当前交互对象关系与好感度】\n"
                f"- 交互对象：{actor_name} ({role_desc})\n"
                f"- 当前好感度：未知（这是你们的第一次交流，你还没有形成印象）\n"
                f"- 态度指引：保持礼貌与分寸，先观察和了解对方，不要预设亲近或熟络。"
            )
        else:
            # 1. 匹配用户自定义好感度温差阶段
            tiers = self._rules.get("affection_tiers", DEFAULT_RULES["affection_tiers"])
            matched_tier = None
            for tier in tiers:
                min_s = tier.get("min_score", 0)
                max_s = tier.get("max_score", 100)
                if min_s <= affection <= max_s:
                    matched_tier = tier
                    break
            if not matched_tier and tiers:
                matched_tier = tiers[-1]

            stage_desc = matched_tier.get("stage_name", "日常朋友") if matched_tier else "日常朋友"
            attitude_hint = (matched_tier.get("attitude_hint", "自然随和沟通") if matched_tier else "自然随和沟通").strip()
            attitude_hint = attitude_hint.rstrip("。.")

            blocks.append(
                f"【当前交互对象关系与好感度】\n"
                f"- 交互对象：{actor_name} ({role_desc})\n"
                f"- 当前好感度：{affection}/100（状态：{stage_desc}）\n"
                f"- 态度指引：{attitude_hint}。"
            )

        # 2. 长效事实：主人显示主人偏好，其他交互对象显示其当前事实
        if master_facts:
            facts_str = "；".join(master_facts[-8:])
            title = (
                "长效记忆·主人已知偏好与事实"
                if actor_id == "master"
                else "长效记忆·当前交互对象的已知事实"
            )
            blocks.append(f"【{title}】\n- 关键事实：{facts_str}")

        # 3. 三维时间轴语义唤醒的历史经历
        if relations:
            lines = [f"- {r}" for r in relations[:12]]
            blocks.append("【关系图·当前有效关系】\n" + "\n".join(lines))

        # 4. 三维时间轴语义唤醒的历史经历
        if recalled_memories:
            lines = [f"- {m}" for m in recalled_memories]
            blocks.append(
                "【自传时间轴·唤醒的相关历史经历】\n" + "\n".join(lines)
            )

        # 4. 中枢统一知识库
        if knowledge:
            lines = [f"- {k}" for k in knowledge]
            blocks.append(
                "【知识库·外部资料】\n" + "\n".join(lines) +
                "\n回答时优先参考以上知识库内容；如果知识库与近期记忆冲突，以知识库事实为准。"
            )

        # 5. 连续时间轴近期经历流
        if recent_stream:
            lines = [f"- {s}" for s in recent_stream]
            blocks.append(
                "【自传时间轴·近期连续经历流】\n" + "\n".join(lines)
            )

        return "\n\n".join(blocks)

    def post_process_response(self, text: str) -> str:
        """
        遵循用户自定义口语规则的输出清洗（带代码块与链接防护罩）：
        1. 保护多行代码块与行内代码
        2. 保护 Markdown 超链接
        3. 按顺序执行用户启用的自定义口语正则规则
        4. 严密还原代码块与链接
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

        # 3. 按配置依次应用用户自定义的口语正则替换
        regex_rules = self._rules.get("speech_regex_rules", DEFAULT_RULES["speech_regex_rules"])
        cleaned = masked
        for rule in regex_rules:
            if not rule.get("is_active", True):
                continue
            pat = rule.get("pattern", "")
            rep = rule.get("replacement", "")
            if not pat:
                continue
            try:
                cleaned = re.sub(pat, rep, cleaned)
            except Exception as e:
                logger.warning(f"[PersonaManager] 执行正则规则 '{rule.get('name')}' 出错: {e}")

        # 4. 无损还原 Markdown 链接与代码块
        for i, link in enumerate(md_links):
            cleaned = cleaned.replace(f"__MD_LINK_{i}__", link)
        for i, code in enumerate(code_snippets):
            cleaned = cleaned.replace(f"__CODE_SNIPPET_{i}__", code)

        return cleaned.strip()


# 全局单例
persona_manager = PersonaManager()
