import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GAMEDATA_DIR = Path(__file__).parent.parent.parent / "plugin_data" / "astrbot_plugin_mrfz" / "gamedata"
BASE_URL = "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/master/zh_CN/gamedata/excel/"


class OperatorDataManager:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or GAMEDATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.char_table_path = self.data_dir / "character_table.json"
        self.skill_table_path = self.data_dir / "skill_table.json"
        self.item_table_path = self.data_dir / "item_table.json"

        self._characters: Optional[Dict[str, Any]] = None
        self._skills: Optional[Dict[str, Any]] = None
        self._items: Optional[Dict[str, Any]] = None
        self._name_to_char_id: Dict[str, str] = {}

    def ensure_data(self) -> bool:
        """检查并加载本地数据，如不存在则尝试自动下载"""
        import urllib.request

        for filename, path in [
            ("character_table.json", self.char_table_path),
            ("skill_table.json", self.skill_table_path),
            ("item_table.json", self.item_table_path),
        ]:
            if not path.exists():
                try:
                    logger.info(f"正在下载明日方舟官方数据包 {filename} ...")
                    urllib.request.urlretrieve(BASE_URL + filename, path)
                except Exception as e:
                    logger.error(f"下载 {filename} 失败: {e}")
                    return False

        if self._characters is None:
            try:
                with open(self.char_table_path, "r", encoding="utf-8") as f:
                    self._characters = json.load(f)
                with open(self.skill_table_path, "r", encoding="utf-8") as f:
                    self._skills = json.load(f)
                with open(self.item_table_path, "r", encoding="utf-8") as f:
                    self._items = json.load(f).get("items", {})

                self._name_to_char_id = {
                    char.get("name"): cid
                    for cid, char in self._characters.items()
                    if char.get("name")
                }
            except Exception as e:
                logger.error(f"加载明日方舟数据失败: {e}")
                return False

        return True

    @staticmethod
    def _format_description(desc: str, blackboard: List[Dict[str, Any]]) -> str:
        bb = {item["key"]: item.get("value") for item in blackboard if "key" in item}

        def repl(m: re.Match) -> str:
            raw = m.group(1)
            fmt = None
            if ":" in raw:
                k, fmt = raw.split(":", 1)
            else:
                k = raw
            val = bb.get(k, m.group(0))
            if fmt and isinstance(val, (int, float)):
                if fmt == "0%":
                    return f"{val * 100:.0f}%"
                elif fmt == "0.0":
                    return f"{val:.1f}"
            if isinstance(val, float) and val.is_integer():
                return str(int(val))
            return str(val)

        text = re.sub(r"\{([^}]+)\}", repl, desc)
        text = re.sub(r"<[^>]+>", "", text)
        return text.replace("\n", " ").strip()

    def query_operator(
        self,
        operator_name: str,
        skill_index: Optional[int] = None,
        mastery: Optional[int] = None,
    ) -> str:
        if not self.ensure_data():
            return "本地干员数据库初始化失败，请稍后重试。"

        name = operator_name.strip()
        char_id = self._name_to_char_id.get(name)

        if not char_id:
            # 尝试简单模糊匹配
            import difflib

            matches = difflib.get_close_matches(
                name, self._name_to_char_id.keys(), n=3, cutoff=0.6
            )
            sugg_text = f"（本地相近干员参考：{', '.join(matches)}）" if matches else ""
            return (
                f"【本地数据库未检索到干员「{name}」】\n"
                f"重要提示：该干员可能是明日方舟最新版本/新卡池刚刚实装的全新干员（本地解包库可能有短暂滞后），绝不要断定是用户在编造或钓鱼！\n"
                f"请立即调用联网搜索工具（如 you-search，检索关键词例如：'明日方舟 {name} 技能' 或 'PRTS {name}'）获取该干员的最新技能倍率与定位，并为用户准确解答！"
                f"{sugg_text}"
            )

        char = self._characters[char_id]
        skills_list = char.get("skills", [])
        prof_map = {
            "WARRIOR": "近卫",
            "SNIPER": "狙击",
            "TANK": "重装",
            "MEDIC": "医疗",
            "SUPPORT": "辅助",
            "CASTER": "术师",
            "SPECIAL": "特种",
            "PIONEER": "先锋",
        }
        prof = prof_map.get(char.get("profession"), char.get("profession"))
        rarity_raw = str(char.get("rarity", "6"))
        if "TIER_" in rarity_raw:
            rarity = int(rarity_raw.replace("TIER_", ""))
        else:
            try:
                rarity = int(rarity_raw) + 1
            except ValueError:
                rarity = 6

        lines = [f"【{name}】（{rarity}★ {prof}）"]

        # 如果指定了特定技能（1, 2, 3）
        target_skills = []
        if skill_index is not None and 1 <= skill_index <= len(skills_list):
            target_skills = [(skill_index - 1, skills_list[skill_index - 1])]
        else:
            target_skills = list(enumerate(skills_list))

        for idx, sk_info in target_skills:
            sk_id = sk_info.get("skillId")
            sk_obj = self._skills.get(sk_id, {})
            lvls = sk_obj.get("levels", [])
            if not lvls:
                continue

            sk_name = lvls[0].get("name", f"技能{idx+1}")
            lines.append(f"\n▶ 技能{idx+1}：【{sk_name}】")

            # 默认取专三（Lv10）或最高等级
            target_lvl_idx = (
                (6 + mastery)
                if (mastery and 1 <= mastery <= 3 and len(lvls) >= (7 + mastery))
                else (len(lvls) - 1)
            )
            lvl_data = lvls[min(target_lvl_idx, len(lvls) - 1)]
            level_label = (
                f"专三 (Lv{target_lvl_idx+1})"
                if target_lvl_idx == 9
                else (
                    f"专{target_lvl_idx-6} (Lv{target_lvl_idx+1})"
                    if target_lvl_idx >= 7
                    else f"Lv{target_lvl_idx+1}"
                )
            )

            sp_data = lvl_data.get("spData", {})
            sp_type_map = {
                "INCREASE_WITH_TIME": "自动回复",
                "INCREASE_WHEN_ATTACK": "攻击回复",
                "INCREASE_WHEN_TAKEN_DAMAGE": "受击回复",
            }
            sp_type = sp_type_map.get(
                sp_data.get("spType"), sp_data.get("spType", "")
            )
            init_sp = sp_data.get("initSp", 0)
            sp_cost = sp_data.get("spCost", 0)
            duration = lvl_data.get("duration", 0)

            dur_str = f"持续 {duration:.0f}秒" if duration > 0 else "瞬发/持续生效"
            lines.append(
                f"- [{level_label}] 技力消耗: {sp_cost} (初始 {init_sp}) | 回复: {sp_type} | {dur_str}"
            )

            desc = self._format_description(
                lvl_data.get("description", ""), lvl_data.get("blackboard", [])
            )
            lines.append(f"- 技能效果: {desc}")

            # 专精材料
            costs = sk_info.get("levelUpCostCond", [])
            # costs[0]=专一, costs[1]=专二, costs[2]=专三
            if costs:
                if (
                    mastery
                    and 1 <= mastery <= len(costs)
                    or target_lvl_idx >= 7
                    and len(costs) >= 3
                ):
                    m_idx = (mastery - 1) if (mastery and 1 <= mastery <= 3) else 2
                    m_cost = costs[m_idx].get("levelUpCost", [])
                    cost_items = []
                    for c in m_cost:
                        item_name = self._items.get(c.get("id"), {}).get(
                            "name", c.get("id")
                        )
                        cost_items.append(f"{item_name}×{c.get('count')}")
                    lines.append(f"- 专{m_idx+1}材料: {', '.join(cost_items)}")

        return "\n".join(lines)


operator_data_manager = OperatorDataManager()
