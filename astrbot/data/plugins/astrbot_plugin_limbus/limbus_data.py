import difflib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

GAMEDATA_DIR = Path(__file__).parent.parent.parent / "plugin_data" / "astrbot_plugin_limbus" / "gamedata"
BASE_URL = "https://raw.githubusercontent.com/LocalizeLimbusCompany/LocalizeLimbusCompany/main/LLC_zh-CN/"

# 常见的条件标签中文化映射
CONDITION_TAGS = {
    "[WhenUse]": "[使用时]",
    "[OnSucceedAttack]": "[命中时]",
    "[OnSucceedAttackHead]": "[正面命中时]",
    "[OnSucceedAttackTail]": "[反面命中时]",
    "[WinDuel]": "[拼点胜利]",
    "[LoseDuel]": "[拼点失败]",
    "[OnSucceedDuel]": "[拼点胜利时]",
    "[BeforeAttack]": "[攻击前]",
    "[AfterAttack]": "[攻击后]",
    "[EndSkill]": "[技能结束时]",
    "[EndSkillAll]": "[技能完全结束时]",
    "[KillEnemy]": "[击杀目标时]",
    "[Critical]": "[暴击时]",
    "[CriticalOnSucceedAttack]": "[暴击命中时]",
}

SINNERS_NAME = [
    "李箱", "浮士德", "堂吉诃德", "良秀", "默尔索", "鸿璐",
    "希斯克利夫", "以实玛利", "罗佳", "辛克莱", "奥提斯", "格里高尔"
]

POPULAR_ALIASES = {
    "魔弹": 11107,
    "魔弹奥提斯": 11107,
    "w唐": 10302,
    "w公司堂吉诃德": 10302,
    "兔希": 10703,
    "兔子希斯克利夫": 10703,
    "握辛": 11003,
    "握爪辛克莱": 11003,
    "花箱": 10104,
    "蝶箱": 10113,
    "环箱": 10109,
    "环指李箱": 10109,
    "沉沦李箱": 10113,
    "剑契李箱": 10102,
    "水袋浮士德": 10202,
    "握浮": 10204,
    "船长玛丽": 10808,
    "船长以实玛利": 10808,
    "中指唐": 10306,
    "剑契默尔索": 10502,
    "黑云鸿璐": 10602,
    "黑派经理唐": 10315,
    "黑派唐": 10315,
    "黑派经理": 10315,
    "黑派品牌经理": 10315,
    "高级定制黑派经理": 10315,
    "高级定制唐": 10315,
    "黑兽未唐": 10313,
    "黑兽唐": 10313,
    "代行唐": 10314,
    "血鬼唐": 10310,
    "曼却唐": 10310,
}


class LimbusDataManager:
    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or GAMEDATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self._personalities: Optional[List[Dict[str, Any]]] = None
        self._skills: Optional[Dict[str, Any]] = None
        self._passives: Optional[Dict[str, Any]] = None
        self._keywords: Optional[Dict[str, str]] = None
        self._p_id_map: Dict[int, Dict[str, Any]] = {}
        self._p_name_index: Dict[str, int] = {}

    def ensure_data(self) -> bool:
        if self._personalities is not None:
            return True

        import urllib.request

        needed_files = (
            ["Personalities.json", "Passives.json", "BattleKeywords.json", "Skills.json"]
            + [f"Skills_personality-{i:02d}.json" for i in range(1, 13)]
            + ["Skills_personality-x1p1c1.json"]
        )

        for filename in needed_files:
            p = self.data_dir / filename
            if not p.exists():
                try:
                    logger.info(f"正在下载边狱公司数据 {filename} ...")
                    urllib.request.urlretrieve(BASE_URL + filename, p)
                except Exception as e:
                    logger.error(f"下载 {filename} 失败: {e}")

        try:
            # 加载关键字表
            kw_path = self.data_dir / "BattleKeywords.json"
            self._keywords = {}
            if kw_path.exists():
                with open(kw_path, "r", encoding="utf-8") as f:
                    for item in json.load(f).get("dataList", []):
                        kid = item.get("id")
                        kname = item.get("name")
                        if kid and kname:
                            self._keywords[kid] = kname

            # 加载技能表
            self._skills = {}
            for f in self.data_dir.iterdir():
                if f.name.startswith("Skills_personality") or f.name == "Skills.json":
                    try:
                        with open(f, "r", encoding="utf-8") as fp:
                            for sk in json.load(fp).get("dataList", []):
                                self._skills[str(sk.get("id"))] = sk
                    except Exception as e:
                        logger.warning(f"读取技能文件 {f.name} 失败: {e}")

            # 加载被动表
            self._passives = {}
            passive_path = self.data_dir / "Passives.json"
            if passive_path.exists():
                with open(passive_path, "r", encoding="utf-8") as f:
                    for pv in json.load(f).get("dataList", []):
                        self._passives[str(pv.get("id"))] = pv

            # 加载人格表
            p_path = self.data_dir / "Personalities.json"
            with open(p_path, "r", encoding="utf-8") as f:
                self._personalities = json.load(f).get("dataList", [])

            self._p_id_map = {}
            self._p_name_index = {}
            for item in self._personalities:
                pid = item.get("id")
                if not pid:
                    continue
                self._p_id_map[pid] = item
                title = item.get("title", "").replace("\n", " ").strip()
                name = item.get("name", "").strip()
                full_name = f"{title} {name}".strip()
                compact = re.sub(r"[\s::·_]+", "", full_name).lower()
                self._p_name_index[full_name] = pid
                self._p_name_index[compact] = pid

            return True
        except Exception as e:
            logger.error(f"加载边狱公司数据异常: {e}", exc_info=True)
            return False

    def _clean_text(self, text: str) -> str:
        if not text:
            return ""
        # 替换条件标签
        for tag, rep in CONDITION_TAGS.items():
            text = text.replace(tag, rep)
        # 替换关键字 [Combustion] -> 烧伤
        if self._keywords:
            for kid, kname in self._keywords.items():
                pattern = f"[{kid}]"
                if pattern in text:
                    text = text.replace(pattern, f"{kname}")
        # 去除 HTML 标签 <style="highlight"> 等
        text = re.sub(r"<[^>]+>", "", text)
        return text.replace("\n", " ").strip()

    def find_personality(self, query: str) -> Tuple[Optional[Dict[str, Any]], Optional[List[str]]]:
        self.ensure_data()
        q = query.strip().lower()

        # 1. 检查别名表
        if q in POPULAR_ALIASES:
            pid = POPULAR_ALIASES[q]
            if pid in self._p_id_map:
                return self._p_id_map[pid], None

        # 2. 精确或压缩匹配
        q_compact = re.sub(r"[\s::·_]+", "", q)
        if q_compact in self._p_name_index:
            return self._p_id_map[self._p_name_index[q_compact]], None

        # 3. 包含匹配
        matched_pids = []
        for key, pid in self._p_name_index.items():
            if q_compact in key:
                if pid not in matched_pids:
                    matched_pids.append(pid)

        if len(matched_pids) == 1:
            return self._p_id_map[matched_pids[0]], None

        # 4. 模糊匹配候选
        candidates = list(self._p_name_index.keys())
        close_matches = difflib.get_close_matches(q_compact, candidates, n=4, cutoff=0.4)
        unique_names = []
        for cm in close_matches:
            pid = self._p_name_index.get(cm)
            if pid and pid in self._p_id_map:
                p_item = self._p_id_map[pid]
                title = p_item.get("title", "").replace("\n", " ")
                fn = f"{title} {p_item.get('name')}".strip()
                if fn not in unique_names:
                    unique_names.append(fn)

        if matched_pids:
            for pid in matched_pids[:4]:
                p_item = self._p_id_map[pid]
                title = p_item.get("title", "").replace("\n", " ")
                fn = f"{title} {p_item.get('name')}".strip()
                if fn not in unique_names:
                    unique_names.append(fn)

        return None, unique_names

    def query_personality_data(self, query: str, skill_index: Optional[int] = None) -> str:
        if not self.ensure_data():
            return "边狱公司数据加载失败，请检查网络或稍后重试。"

        p_item, suggestions = self.find_personality(query)
        if not p_item:
            sugg_text = ""
            if suggestions:
                sugg_text = f"\n（本地相近人格参考：{', '.join(suggestions)}）"
            return (
                f"【本地数据库未检索到「{query}」】\n"
                f"重要提示：该人格可能是边狱公司近期新版本/新赛季刚刚实装的最新角色（本地离线解包库存在一定延迟），绝不要断定是用户在编造或钓鱼！\n"
                f"请立即调用联网搜索工具（如 you-search，检索关键词例如：'边狱公司 {query} 技能' 或 '灰机wiki {query}'）获取该角色最新的技能、硬币效果与被动，并为用户准确解答！"
                f"{sugg_text}"
            )

        pid = p_item.get("id")
        title = p_item.get("title", "").replace("\n", " ").strip()
        name = p_item.get("name", "").strip()

        lines = [f"【{title} {name}】（ID: {pid}）"]

        # 技能 1-5 (1:S1, 2:S2, 3:S3, 4:守备, 5:特殊/强化)
        skill_numbers = [skill_index] if (skill_index and 1 <= skill_index <= 5) else [1, 2, 3, 4, 5]

        for sk_no in skill_numbers:
            sk_id = f"{pid}{sk_no:02d}"
            if sk_id not in self._skills:
                continue

            sk_obj = self._skills[sk_id]
            lvls = sk_obj.get("levelList", [])
            if not lvls:
                continue

            # 取最高同步等级 (同期化 IV)
            top = lvls[-1]
            sk_name = top.get("name", f"技能{sk_no}")
            coins = top.get("coinlist", [])
            c_cnt = len(coins)

            type_label = f"技能{sk_no}" if sk_no <= 3 else ("守备技能" if sk_no == 4 else f"强化/特殊技能{sk_no}")
            lines.append(f"\n▶ {type_label}：【{sk_name}】 ({c_cnt}硬币)")

            desc = self._clean_text(top.get("desc", ""))
            if desc:
                lines.append(f"- 技能描述: {desc}")

            for i, c in enumerate(coins):
                coindescs = c.get("coindescs", [])
                descs = [self._clean_text(cd.get("desc", "")) for cd in coindescs if cd.get("desc")]
                if descs:
                    lines.append(f"- [硬币{i+1}] {'; '.join(descs)}")

        # 被动技能（仅在查询全部技能时附带显示）
        if skill_index is None:
            p_combat = self._passives.get(f"{pid}01")
            if p_combat:
                lines.append(f"\n▶ 战斗被动：【{p_combat.get('name')}】")
                lines.append(f"- {self._clean_text(p_combat.get('desc', ''))}")

            p_supp = self._passives.get(f"{pid}02")
            if p_supp:
                lines.append(f"\n▶ 支援被动：【{p_supp.get('name')}】")
                lines.append(f"- {self._clean_text(p_supp.get('desc', ''))}")

        return "\n".join(lines)


limbus_data_manager = LimbusDataManager()
