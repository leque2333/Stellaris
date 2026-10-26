import asyncio
import re
import time
from pathlib import Path
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain

EASTER_EGG_325_TEXT = (
    "所以325是什么意思，难道是出自明日方舟up主魔法ZC目录2024年3月11日的直播，"
    "彼时由龙哥哥今天又鸽了主办的明日方舟集成战略民间赛事仙术杯第五届正在如火如荼地展开中，"
    "本届仙术杯采取团队赛的形式，ZC 代表冠军厨小队的第二位出战，使用科学分队焰影苇草开但由于"
    "临场过于紧张运气Maybe和肉鸽基础不够扎实等原因在第三层关底利刃所指暴毙，局内结算325分？"
)

_ocr_instance = None
_last_325_trigger_time: dict[str, float] = {}


def get_ocr():
    global _ocr_instance
    if _ocr_instance is None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            _ocr_instance = RapidOCR()
            logger.info("[EasterEgg325] RapidOCR 实例初始化成功")
        except Exception as e:
            logger.error(f"[EasterEgg325] RapidOCR 初始化失败: {e}")
    return _ocr_instance


def check_text_for_325(text: str) -> bool:
    if not text:
        return False
    if "难道是出自明日方舟up主魔法ZC目录" in text:
        return False
    # 标准化全角数字
    t = text.replace("３", "3").replace("２", "2").replace("５", "5")
    patterns = [
        r"3\s*2\s*5",
        r"3[^\d\w\u4e00-\u9fa5]?2[^\d\w\u4e00-\u9fa5]?5",
        r"三\s*二\s*五",
        r"三百二十五",
    ]
    for p in patterns:
        if re.search(p, t):
            return True
    return False


def _sync_ocr_file(file_path: str) -> bool:
    try:
        ocr = get_ocr()
        if not ocr:
            return False
        results, _ = ocr(file_path)
        if not results:
            return False
        for item in results:
            line_text = item[1]
            if check_text_for_325(line_text):
                logger.info(f"[EasterEgg325] OCR 识别到 325 文本: {line_text}")
                return True
    except Exception as e:
        logger.debug(f"[EasterEgg325] OCR 识别单张图片异常: {e}")
    return False


async def check_image_for_325(img: Image) -> bool:
    try:
        if hasattr(img, "convert_to_file_path"):
            path = await img.convert_to_file_path()
            if path and Path(path).exists():
                return await asyncio.to_thread(_sync_ocr_file, str(path))
    except Exception as e:
        logger.debug(f"[EasterEgg325] 获取图片文件失败: {e}")
    return False


async def handle_325_easter_egg(event: AstrMessageEvent) -> bool:
    """检查消息（文本或图片）是否触发 325 仙术杯彩蛋。

    若触发，立即发送彩蛋并返回 True，调用方应调用 event.stop_event() 拦截后续流程（不入记忆库）。
    """
    sender_id = str(event.get_sender_id())
    self_id = str(event.get_self_id() or "").strip()
    if sender_id in {self_id, "1000000002"}:
        return False

    session_id = str(event.get_group_id() or sender_id)

    # 1. 文本检查
    text = (event.message_str or "").strip()
    triggered = check_text_for_325(text)

    # 1.1 raw_message 辅助文本检查
    if not triggered:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", "")
        if isinstance(raw, str) and check_text_for_325(raw):
            triggered = True

    # 2. 图片 OCR 检查
    if not triggered:
        try:
            for msg in event.get_messages():
                if isinstance(msg, Image):
                    if await check_image_for_325(msg):
                        triggered = True
                        break
        except Exception as e:
            logger.debug(f"[EasterEgg325] 遍历消息组件 OCR 异常: {e}")

    if triggered:
        now = time.time()
        last_time = _last_325_trigger_time.get(session_id, 0.0)
        # 3 秒防刷频率控制：3 秒内若再次命中，只消费事件拦截不重复刷屏
        if now - last_time >= 3.0:
            _last_325_trigger_time[session_id] = now
            logger.info(
                f"[EasterEgg325] 会话 {session_id} 触发 325 仙术杯彩蛋！立即直接回复并跳过记忆库。"
            )
            await event.send(MessageChain().message(EASTER_EGG_325_TEXT))
        else:
            logger.info(f"[EasterEgg325] 会话 {session_id} 3 秒内重复触发 325，静默拦截。")
        return True

    return False
