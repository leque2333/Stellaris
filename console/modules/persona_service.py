# -*- coding: utf-8 -*-
"""
Stellaris Console - Persona Service
角色设定卡、规则设定、好感度进阶与温差正则引擎

人格卡拆成四个可替换字段：
- name: AI 名称
- persona: 人格基底
- dialogue_examples: 对话范例
- other: 其他设定
存储仍然是 data/persona.md，方便直接替换文件。
"""

import os
import json
import re
from .common import (
    PERSONA_PATH,
    PERSONA_RULES_PATH,
    ensure_persona_file,
)
from agent_core.persona_identity import parse_persona_name

DEFAULT_PERSONA_NAME = "助手"

DEFAULT_PERSONA_FIELDS = {
    "name": DEFAULT_PERSONA_NAME,
    "persona": (
        "你是一位清爽、理智、幽默且富有同理心的全功能陪伴型与助手型 AI。\n"
        "你拥有极高的敏锐度与执行力，说话简洁有力，擅长深入分析问题，"
        "并在日常交流中给予真实温度。"
    ),
    "dialogue_examples": "",
    "other": "",
}

# 旧版默认人格的 ID 仅用于兼容已有前端配置；显示名不再绑定具体角色。
DEFAULT_PERSONA_ID = "default_core"

DEFAULT_RULES = {
    "affection_tiers": [
        {
            "id": "tier_cold",
            "min_score": 0,
            "max_score": 39,
            "stage_name": "初识与疏离（克制边界）",
            "attitude_hint": "对不熟的人克制客气，简短得体，保持分寸感与独立边界，不主动展开漫无目的的闲聊",
        },
        {
            "id": "tier_warm",
            "min_score": 40,
            "max_score": 79,
            "stage_name": "熟悉与朋友（自然随性）",
            "attitude_hint": "自然随和，亲切温和，真诚待人，带有日常朋友般的从容与陪伴感",
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
            ),
        },
    ],
    "speech_regex_rules": [
        {
            "id": "rule_strip_action_brackets_zh",
            "name": "中文神态动作描写括号过滤",
            "pattern": "（[^）]*）",
            "replacement": "",
            "description": "剥离中文全角括号内的舞台神态描写、心理活动或非台词说明",
            "is_active": True,
        },
        {
            "id": "rule_strip_action_brackets_en",
            "name": "英文动作描写括号过滤",
            "pattern": r"\([^\)]*[一-龥][^\)]*\)",
            "replacement": "",
            "description": "剥离包含中文字符的半角英文括号内动作或神态描写",
            "is_active": True,
        },
        {
            "id": "rule_strip_trailing_periods",
            "name": "句末句号彻底剔除",
            "pattern": r"。+$|(?<!\.)\.(?!\.)$",
            "replacement": "",
            "description": "去除台词末尾的句号，保持随性自然的真人呼吸感",
            "is_active": True,
        },
    ],
}

_STANDARD_HEADINGS = {
    "人格设定": "persona",
    "对话范例": "dialogue_examples",
    "对话示例": "dialogue_examples",
    "其他": "other",
}


def _clean_text(value) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _normalize_profile(data: dict | None) -> dict:
    data = data if isinstance(data, dict) else {}
    name = re.sub(r"\s+", " ", _clean_text(data.get("name"))).strip()
    fields = {
        "name": name or DEFAULT_PERSONA_NAME,
        "persona": _clean_text(data.get("persona")),
        "dialogue_examples": _clean_text(data.get("dialogue_examples")),
        "other": _clean_text(data.get("other")),
    }
    if not fields["persona"] and data.get("system_prompt"):
        return split_persona_text(str(data.get("system_prompt")))
    if not fields["persona"]:
        fields["persona"] = DEFAULT_PERSONA_FIELDS["persona"]
    return fields


def _remove_affection_from_text(text: str) -> str:
    """好感度统一由「好感度系统」管理，人格设定里不再保留。"""
    lines = []
    skip_level = 0
    for line in (text or "").splitlines():
        heading = re.match(r"^(#{1,6})\s*(.+)$", line)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2)
            if "好感度" in title:
                skip_level = level
                continue
            if skip_level and level <= skip_level:
                skip_level = 0
        if skip_level:
            continue
        if "好感度温度梯度" in line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _strip_first_title(text: str) -> str:
    lines = (text or "").splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def split_persona_text(text: str) -> dict:
    """把旧版人设卡拆成 名称 / 人格设定 / 对话范例 / 其他。"""
    raw = _clean_text(text)
    name = parse_persona_name(raw) or DEFAULT_PERSONA_NAME
    body = _remove_affection_from_text(_strip_first_title(raw))
    fields = {
        "name": name,
        "persona": "",
        "dialogue_examples": "",
        "other": "",
    }
    if not body:
        return fields

    # 新格式：## 人格设定 / ## 对话范例 / ## 其他
    standard = list(re.finditer(
        r"(?m)^##\s*(人格设定|对话范例|对话示例|其他)\s*$", body
    ))
    if standard:
        prefix = body[:standard[0].start()].strip()
        for idx, match in enumerate(standard):
            end = standard[idx + 1].start() if idx + 1 < len(standard) else len(body)
            content = body[match.end():end].strip()
            key = _STANDARD_HEADINGS.get(match.group(1))
            if not key:
                continue
            if key == "persona" and prefix:
                content = f"{prefix}\n\n{content}".strip()
            fields[key] = (fields[key] + "\n\n" + content).strip()
        return fields

    # 旧格式：按原有 ## 标题归类
    headings = list(re.finditer(r"(?m)^##\s+([^\n]+)$", body))
    if not headings:
        fields["persona"] = body.strip()
        return fields

    prefix = body[:headings[0].start()].strip()
    persona_parts = [prefix] if prefix else []
    dialogue_parts = []
    other_parts = []
    for idx, match in enumerate(headings):
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(body)
        title = match.group(1).strip()
        content = body[match.end():end].strip()
        block = f"## {title}\n{content}".strip()
        if any(word in title for word in ("对白", "对话范例", "对话示例", "经典情境")):
            dialogue_parts.append(block)
        elif any(word in title for word in ("称呼", "群聊", "其他", "补充")):
            other_parts.append(block)
        else:
            persona_parts.append(block)
    fields["persona"] = "\n\n".join(part for part in persona_parts if part).strip()
    fields["dialogue_examples"] = "\n\n".join(dialogue_parts).strip()
    fields["other"] = "\n\n".join(other_parts).strip()
    return fields


def compose_persona_text(fields: dict) -> str:
    fields = _normalize_profile(fields)
    parts = [f"# 人设：{fields['name']}"]
    parts.append("## 人格设定\n" + fields["persona"].strip())
    if fields["dialogue_examples"]:
        parts.append("## 对话范例\n" + fields["dialogue_examples"].strip())
    if fields["other"]:
        parts.append("## 其他\n" + fields["other"].strip())
    return "\n\n".join(parts).strip() + "\n"


def get_persona_profile() -> dict:
    ensure_persona_file()
    if not os.path.exists(PERSONA_PATH):
        return dict(DEFAULT_PERSONA_FIELDS)

    try:
        with open(PERSONA_PATH, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return dict(DEFAULT_PERSONA_FIELDS)

    fields = split_persona_text(content)
    # 首次读取旧格式时自动迁移：去掉人格里的好感度设定，并统一为四段结构
    if "好感度温度梯度" in content or "## 人格设定" not in content:
        save_persona_profile(fields)
    return fields


def save_persona_profile(fields: dict) -> dict:
    fields = _normalize_profile(fields)
    text = compose_persona_text(fields)
    os.makedirs(os.path.dirname(PERSONA_PATH), exist_ok=True)
    with open(PERSONA_PATH, "w", encoding="utf-8") as f:
        f.write(text)

    try:
        from agent_core.persona_identity import clear_persona_cache
        clear_persona_cache()
    except Exception:
        pass

    return {
        "success": True,
        "message": "人格设定已保存生效",
        "persona": fields,
        "system_prompt": text,
    }


def get_current_persona():
    fields = get_persona_profile()
    return {
        **fields,
        "system_prompt": compose_persona_text(fields),
        "source": "hub" if os.path.exists(PERSONA_PATH) else "default",
    }


def save_persona(prompt: str):
    """兼容旧接口：整段 prompt 保存时自动拆分。"""
    return save_persona_profile(split_persona_text(prompt))


def clean_persona_reply(text: str) -> str:
    rules = get_persona_rules()
    regex_rules = (
        rules.get("speech_regex_rules")
        or rules.get("speech_regex")
        or []
    )
    cleaned = text or ""
    for rule in regex_rules:
        if rule.get("is_active") is False:
            continue
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        replacement = rule.get("replacement", "")
        if rule.get("action") == "strip":
            replacement = ""
        try:
            cleaned = re.sub(pattern, replacement, cleaned)
        except Exception:
            pass
    return cleaned.strip()


def get_preset_personas():
    return [
        {"id": DEFAULT_PERSONA_ID, "name": "默认助手", "desc": "理智敏锐、幽默温暖的全功能人格"},
        {"id": "engineer_pro", "name": "极客工程师", "desc": "专注于高性能系统架构与高可靠代码交付"},
        {"id": "life_companion", "name": "知心挚友", "desc": "倾听陪伴、善解人意的情感支持人格"},
    ]


def get_persona_rules():
    if os.path.exists(PERSONA_RULES_PATH):
        try:
            with open(PERSONA_RULES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_RULES


def save_persona_rules(rules: dict):
    os.makedirs(os.path.dirname(PERSONA_RULES_PATH), exist_ok=True)
    with open(PERSONA_RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    return {"success": True, "message": "人设温差与正则规则已保存"}
