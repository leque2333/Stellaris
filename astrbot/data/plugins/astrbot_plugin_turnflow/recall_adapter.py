from typing import Any, Iterable, List

from astrbot.api import logger


class RecallAdapter:
    def __init__(self, config=None):
        config = config or {}
        self.enabled = bool(config.get("enable_humanoid_recall", True))

    async def recall_messages(self, event, message_ids: Iterable[Any]) -> List[Any]:
        if not self.enabled:
            return []
        bot = getattr(event, "bot", None)
        if bot is None:
            logger.warning("[消息防抖动] 当前平台不支持 OneBot 撤回：缺少 bot")
            return []

        recalled = []
        for message_id in message_ids:
            if message_id is None:
                continue
            try:
                await bot.call_action("delete_msg", message_id=message_id)
                recalled.append(message_id)
            except Exception as exc:
                logger.warning(f"[消息防抖动] 撤回消息失败 message_id={message_id}: {exc}")
        return recalled
