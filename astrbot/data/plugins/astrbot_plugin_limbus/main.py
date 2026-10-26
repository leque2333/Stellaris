from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from .limbus_data import limbus_data_manager


@register(
    "astrbot_plugin_limbus",
    "Admin",
    "边狱公司人格与技能数据查询",
    "1.0.0",
)
class LimbusPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        # 预热并加载数据
        limbus_data_manager.ensure_data()

    @filter.llm_tool(name="query_limbus_personality_data")
    async def query_limbus_personality_data(
        self,
        event: AstrMessageEvent,
        personality_name: str,
        skill_index: int = 0,
    ) -> str:
        """查询边狱公司（Limbus Company / 边狱巴士 / 边狱公司）中罪人人格的具体技能数据、硬币数量、使用时条件、硬币命中/拼点效果以及战斗/支援被动。
        当对话或用户提到边狱公司人格（如“魔弹奥提斯”、“W唐”、“环指李箱”、“兔希”、“握辛”等）、技能数值或机制时，必须调用此工具获取精准数据。

        Args:
            personality_name (string): 人格名称或常见简称，例如“魔弹奥提斯”、“W唐”、“环指李箱”、“LCB浮士德”。
            skill_index (number): 技能序号（1、2、3），传 0 则查询该人格所有技能与被动。
        """
        sk_idx = skill_index if (isinstance(skill_index, int) and 1 <= skill_index <= 5) else None
        return limbus_data_manager.query_personality_data(personality_name, skill_index=sk_idx)

    @filter.command("lcb")
    async def cmd_lcb(self, event: AstrMessageEvent, name: str, skill: int = 0):
        """查询边狱公司（Limbus Company）人格技能与被动数据。用法：/lcb <人格名/别称> [技能序号1-3]"""
        sk_idx = skill if (isinstance(skill, int) and 1 <= skill <= 5) else None
        res = limbus_data_manager.query_personality_data(name, skill_index=sk_idx)
        yield event.plain_result(res)
