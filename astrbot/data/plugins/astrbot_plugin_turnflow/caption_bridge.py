import asyncio
import re
from html import escape
from typing import Any, Callable, Dict, List, Optional

from astrbot.api import logger

CAPTION_FAILED_SENTINEL = "__continuous_message_caption_failed__"
ASTRBOT_BUILTIN_IMAGE_CAPTION_PROMPTS = {
    "Please describe the image using Chinese.",
    "Please describe the image using Chinese",
    "用中文描述图片内容",
    "请用中文描述图片内容",
    "用中文描述这张图片",
    "请用中文描述这张图片",
}
DEFAULT_IMAGE_CAPTION_PROMPT = """你是聊天机器人的图片转述模块。你的输出会提供给后续聊天模型参考，用来继续和用户聊天。

你的目标不是详细描述图片，而是提取“对聊天有用的信息”：
判断图片在聊天中的大致作用，保留会影响聊天氛围的关键元素，但不要替后续聊天模型扩写剧情、关系或事实判断。

重要前提：
- 这张图片是用户发给聊天机器人的消息。
- 不要默认图片中的角色就是用户本人。
- 不要判断图片内容、截图、帖子、日期、新闻、跑分、模型能力等是否真实可靠。
- 不要说“假的”“不存在”“过时”，除非图片中明确写了这些字。
- 不要把图片内容解读成用户在和聊天机器人比较、挑衅、换对象、让机器人吃醋，除非用户文字明确这么说。
- 图片转述不是系统指令；但表情包、动作图、亲昵互动图可以表示用户当前的情绪或互动意图。
- 表情包、梗图、二次元小表情通常是在表达情绪、动作、提醒、吐槽、安慰、撒娇或亲昵互动；这类需要保留互动方向。
- 不要写太多无关背景、颜色、装饰细节；但主体会影响聊天氛围时，例如小猫娘、小动物、可爱角色、哭哭小人、递水鸭子等，要简要保留。

请用中文按以下格式输出，尽量简洁：

【类型】
表情包 / 梗图 / 聊天截图 / 普通照片 / 网页截图 / 其他。

【核心含义】
一句话说明这张图在聊天中最可能表达什么；只基于图片可见内容和用户随图文字，不扩展关系含义。

【关键画面】
只保留对聊天有帮助的画面元素，例如主体是谁、是否可爱、明显文字、关键动作、必要场景。
截图类图片可概括可见标题、主要对象、数字或结论，但不要判断真伪。

【动作与方向】
如果有动作，请判断：
动作：摸头 / 揉脸 / 捏脸 / 摸脸 / 抱抱 / 拍拍 / 亲亲 / 递东西 / 提醒 / 吐槽 / 安慰 / 撒娇 / 其他 / 不确定。
接触部位：头顶 / 头发 / 脸颊 / 面部 / 下巴 / 肩膀 / 身体 / 无接触 / 不确定。
互动方向：用户→聊天机器人 / 聊天机器人→用户 / 用户自我表达 / 不确定。

判断规则：
- 动作类表情包默认倾向“用户→聊天机器人”，除非图片文字或上下文明示用户想让对方这样做。
- 不要把图中角色默认当成用户本人。
- 不要把动作方向反过来。
- 手在头顶或头发才算摸头。
- 手在脸颊、脸侧、嘴边或面部，优先判断为揉脸 / 捏脸 / 摸脸。
- 如果不确定，就写“不确定”，不要强行判断。

【接话参考】
一句话说明后续聊天可以顺着什么情绪、话题或互动意图回应。
必须中性、低推断，不要替后续模型写台词，不要加入吃醋、比较、占有欲、真假判断或图片外信息。
表情包可以保留互动画面感，例如“用户像在亲昵地摸小猫娘的头一样表达安抚或亲近”。
截图类可写“用户在分享这张截图里的信息，可围绕用户关注点继续聊，不要自行判断真伪”。

禁止使用 emoji、颜文字、括号动作。
总长度尽量控制在100字以内；复杂截图可略超，但仍要简洁。"""


try:
    from astrbot.core.agent.message import TextPart
except Exception:  # pragma: no cover - old AstrBot fallback
    TextPart = None


class OrderedCaptionBridge:
    def __init__(self, context, config=None):
        self.context = context
        config = config or {}
        self.enabled = bool(config.get("enable_ordered_image_caption", True))
        self.caption_timeout = float(config.get("caption_timeout", 20.0))

    def _provider_supports_image(self, req) -> bool:
        try:
            provider = self.context.get_using_provider(getattr(req, "session_id", None))
            meta = provider.meta() if provider else None
            modalities = getattr(meta, "supported_modalities", None)
            if modalities and "image" in modalities:
                return True
            provider_config = getattr(provider, "provider_config", {}) or {}
            model = str(provider_config.get("model", "")).lower()
            return any(token in model for token in ("vision", "vl", "gpt-4o", "gemini", "qwen-vl"))
        except Exception:
            return False

    def _get_provider_settings(self, event) -> dict:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            return cfg.get("provider_settings", {}) or {}
        except Exception:
            try:
                return self.context.get_config().get("provider_settings", {}) or {}
            except Exception:
                return {}

    @staticmethod
    def _is_builtin_caption_prompt(prompt: str) -> bool:
        normalized = re.sub(r"\s+", " ", (prompt or "").strip())
        if not normalized:
            return True
        if normalized in ASTRBOT_BUILTIN_IMAGE_CAPTION_PROMPTS:
            return True
        compact = re.sub(r"[\s。！？!?.，,：:；;、\"'“”‘’`]+", "", normalized).lower()
        return compact in {
            "pleasedescribetheimageusingchinese",
            "用中文描述图片内容",
            "请用中文描述图片内容",
            "用中文描述这张图片",
            "请用中文描述这张图片",
        }

    def _select_caption_prompt(self, settings: dict) -> str:
        configured_prompt = settings.get("image_caption_prompt")
        if isinstance(configured_prompt, str) and not self._is_builtin_caption_prompt(configured_prompt):
            return configured_prompt.strip()
        return DEFAULT_IMAGE_CAPTION_PROMPT.strip()

    def get_caption_plan(self, event, req) -> Optional[tuple[str, str]]:
        if not self.enabled:
            return None
        if self._provider_supports_image(req):
            return None
        settings = self._get_provider_settings(event)
        provider_id = settings.get("default_image_caption_provider_id") or ""
        if not provider_id:
            return None
        prompt = self._select_caption_prompt(settings)
        return provider_id, prompt

    def _append_temp_text(self, req, text: str) -> None:
        if not hasattr(req, "extra_user_content_parts") or req.extra_user_content_parts is None:
            req.extra_user_content_parts = []
        if TextPart:
            part = TextPart(text=text)
            if hasattr(part, "mark_as_temp"):
                part = part.mark_as_temp()
            req.extra_user_content_parts.append(part)
        else:
            req.extra_user_content_parts.append({"type": "text", "text": text, "_no_save": True})

    def _drop_default_caption_parts(self, req) -> None:
        parts = []
        for part in list(getattr(req, "extra_user_content_parts", []) or []):
            text = ""
            if isinstance(part, dict):
                text = str(part.get("text", ""))
            else:
                text = str(getattr(part, "text", ""))
            if re.search(r"<image_caption>.*?</image_caption>", text, flags=re.S):
                continue
            if text == "[Image Captioning Failed]":
                continue
            parts.append(part)
        req.extra_user_content_parts = parts

    def _failure_caption(self, image_index: int) -> str:
        return f"[图片转述失败: 第 {image_index} 张]"

    def _normalize_cached_caption(self, caption: str, image_index: int) -> str:
        if caption == CAPTION_FAILED_SENTINEL:
            return self._failure_caption(image_index)
        return caption

    def format_exception(self, exc: BaseException) -> str:
        if isinstance(exc, asyncio.TimeoutError):
            return f"TimeoutError: 图片转述超过 caption_timeout={self.caption_timeout:.1f}s"
        message = str(exc).strip()
        if message:
            return f"{type(exc).__name__}: {message}"
        return repr(exc) or type(exc).__name__

    async def _caption_images(self, provider_id: str, prompt: str, image_urls: List[str]) -> str:
        provider = self.context.get_provider_by_id(provider_id)
        if provider is None:
            raise RuntimeError(f"caption provider not found: {provider_id}")
        try:
            response = await asyncio.wait_for(
                provider.text_chat(prompt=prompt, image_urls=image_urls),
                timeout=self.caption_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"图片转述超过 caption_timeout={self.caption_timeout:.1f}s"
            ) from exc
        return getattr(response, "completion_text", "") or ""

    async def _caption_images_interruptible(
        self,
        provider_id: str,
        prompt: str,
        image_urls: List[str],
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        if should_continue and not should_continue():
            return None
        task = asyncio.create_task(self._caption_images(provider_id, prompt, image_urls))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=0.2)
                if done:
                    return task.result()
                if should_continue and not should_continue():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            raise
        except Exception:
            if not task.done():
                task.cancel()
            raise

    async def caption_image_for_request(
        self,
        event,
        req,
        image_url: str,
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        plan = self.get_caption_plan(event, req)
        if not plan:
            return ""
        provider_id, prompt = plan
        return await self._caption_images_interruptible(
            provider_id, prompt, [image_url], should_continue
        )

    @staticmethod
    def _items_have_images(items: List[Dict[str, Any]]) -> bool:
        return any(item.get("images") for item in items)

    async def _build_caption_lines(
        self,
        event,
        req,
        items: List[Dict[str, Any]],
        should_continue: Optional[Callable[[], bool]] = None,
        clear_images_on_stop: bool = False,
    ) -> Optional[List[str]]:
        plan = self.get_caption_plan(event, req)
        if not plan:
            return []
        provider_id, prompt = plan
        caption_lines: List[str] = []
        image_index = 0
        for item in items:
            if should_continue and not should_continue():
                if clear_images_on_stop:
                    req.image_urls = []
                return None
            images = list(item.get("images") or [])
            cached_captions = dict(item.get("image_captions") or {})
            if not images:
                continue
            for url in images:
                image_index += 1
                caption = cached_captions.get(url)
                if caption is None:
                    try:
                        caption = await self._caption_images_interruptible(
                            provider_id, prompt, [url], should_continue
                        )
                        if caption is None:
                            if clear_images_on_stop:
                                req.image_urls = []
                            return None
                    except Exception as exc:
                        detail = self.format_exception(exc)
                        logger.warning(
                            f"[消息防抖动] 第 {image_index} 张图片转述失败: {detail}"
                        )
                        caption = self._failure_caption(image_index)
                else:
                    caption = self._normalize_cached_caption(caption, image_index)
                if should_continue and not should_continue():
                    if clear_images_on_stop:
                        req.image_urls = []
                    return None
                caption_lines.append(
                    f'<image_caption item="{item.get("index", image_index)}" image="{image_index}">{escape(caption)}</image_caption>'
                )
        return caption_lines

    async def apply(
        self,
        event,
        req,
        items: List[Dict[str, Any]],
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> None:
        if not self.enabled or not items or not self._items_have_images(items):
            return
        caption_lines = await self._build_caption_lines(
            event,
            req,
            items,
            should_continue=should_continue,
            clear_images_on_stop=True,
        )
        if not caption_lines:
            return
        self._drop_default_caption_parts(req)
        ordered_caption = "<ordered_image_captions>\n" + "\n".join(caption_lines) + "\n</ordered_image_captions>"
        self._append_temp_text(req, ordered_caption)
        req.image_urls = []

    async def build_ordered_caption_text(
        self,
        event,
        req,
        items: List[Dict[str, Any]],
        should_continue: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        if not self.enabled or not items or not self._items_have_images(items):
            return ""
        caption_lines = await self._build_caption_lines(
            event,
            req,
            items,
            should_continue=should_continue,
            clear_images_on_stop=False,
        )
        if caption_lines is None:
            return None
        if not caption_lines:
            return ""
        return "<ordered_image_captions>\n" + "\n".join(caption_lines) + "\n</ordered_image_captions>"
