# TurnFlow adaptation, 2026-09-20. Source and changes: THIRD_PARTY_NOTICES.md.
import asyncio
import json
import time
from contextvars import ContextVar
from html import escape
from typing import Any, Dict, List, Optional

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

from .caption_bridge import OrderedCaptionBridge
from .continuation_predictor import ContinuationPredictor
from .forward_handler import ForwardHandler
from .message_parser import IS_AIOCQHTTP, MessageParser
from .recall_adapter import RecallAdapter
from .turn_state import (
    EXTRA_CAPTION_HANDLED,
    EXTRA_SENT_IDS,
    EXTRA_TURN_HISTORY,
    EXTRA_TURN_ID,
    EXTRA_TURN_ITEMS,
    TurnSession,
    TurnStateStore,
)

CAPTION_FAILED_SENTINEL = "__continuous_message_caption_failed__"
INTERRUPTIBLE_DELIVERY_STATES = {
    "collecting",
    "pending",
    "llm_request",
    "decorating",
    "sending",
    "streaming",
    "provider_failed",
}

if IS_AIOCQHTTP:
    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
        AiocqhttpMessageEvent,
    )


@register(
    "astrbot_plugin_turnflow",
    "Yuimi-chaya",
    "私聊对话轮次管理：动态收集、回复中断、部分输出撤回与历史回滚保护",
    "1.0.0",
)
class ContinuousMessagePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.context = context
        self.config = config or {}

        self.debounce_time = float(self.config.get("debounce_time", 2.0))
        self.command_prefixes = self.config.get("command_prefixes", ["/"])
        self.enable_plugin = self.config.get("enable", True)
        self.enable_forward_analysis = self.config.get("enable_forward_analysis", True)
        self.enable_typing_detection = self.config.get("enable_typing_detection", True)
        self.max_typing_wait = float(self.config.get("max_typing_wait", 60.0))
        self.max_collection_wait = max(
            self.debounce_time, float(self.config.get("max_collection_wait", 30.0))
        )
        self.enable_recall_filter = self.config.get("enable_recall_filter", True)
        self.history_rollback_delays = self.config.get(
            "history_rollback_delays", [0.2, 1.0, 3.0]
        )
        self.invalidated_turn_retention_seconds = max(
            10.0,
            float(self.config.get("invalidated_turn_retention_seconds", 180.0)),
        )

        reply_format = '<quoted_message sender="{sender_name}">{full_text}</quoted_message>'

        self.turns = TurnStateStore()
        self.sessions: Dict[str, TurnSession] = self.turns.sessions

        image_comp = None
        plain_comp = None
        try:
            from astrbot.api.message_components import Image, Plain

            image_comp = Image
            plain_comp = Plain
        except ImportError:
            try:
                from astrbot.api.message import Image, Plain

                image_comp = Image
                plain_comp = Plain
            except ImportError:
                logger.error("[消息防抖动] 严重: 组件导入失败")

        self.parser = MessageParser(
            image_component=image_comp,
            plain_component=plain_comp,
            plugin_config=self.config,
        )
        self.forward_handler = ForwardHandler(reply_format=reply_format)
        self.predictor = ContinuationPredictor(self.config)
        self.caption_bridge = OrderedCaptionBridge(context, self.config)
        self.recall_adapter = RecallAdapter(self.config)
        self._send_wrappers: Dict[str, List[dict]] = {}
        self._bot_dispatchers: Dict[tuple[int, str], dict] = {}
        self._event_wrappers: Dict[str, List[tuple[Any, str, Any]]] = {}
        self._history_generations: Dict[tuple[str, str], int] = {}
        self._caption_tasks: Dict[tuple[str, str, str], asyncio.Task] = {}
        self._unsent_cleanup_tasks: Dict[str, asyncio.Task] = {}
        self._send_context = ContextVar("continuous_message_send", default=None)
        self._background_tasks: set[asyncio.Task] = set()
        self._pipeline_owners: Dict[str, asyncio.Task] = {}
        self._terminated = False

        logger.info(
            f"[TurnFlow] v1.0.0 加载 | 防抖: {self.debounce_time}s "
            f"| 动态防抖: {self.predictor.enabled} | 有序图片转述: {self.caption_bridge.enabled} "
            f"| 拟人化撤回: {self.recall_adapter.enabled}"
        )

    async def terminate(self):
        self._terminated = True
        self._pipeline_owners.clear()
        pending_tasks = set(self._background_tasks)
        pending_tasks.update(self._caption_tasks.values())
        pending_tasks.update(self._unsent_cleanup_tasks.values())
        for session in (
            list(self.sessions.values())
            + list(self.turns.pending_turns.values())
            + list(self.turns.active_turns.values())
        ):
            pending_tasks.update(
                task for task in (session.timer_task, session.cleanup_task) if task
            )
        for session in list(self.sessions.values()):
            if session.timer_task:
                session.timer_task.cancel()
        for session in list(self.turns.pending_turns.values()) + list(self.turns.active_turns.values()):
            if session.timer_task:
                session.timer_task.cancel()
            if session.cleanup_task:
                session.cleanup_task.cancel()
        for turn_id in list(self._event_wrappers.keys()):
            self._restore_event_capture(turn_id)
        for turn_id in list(self._send_wrappers.keys()):
            self._restore_send_capture(turn_id)
        for task in list(self._unsent_cleanup_tasks.values()):
            if not task.done():
                task.cancel()
        self._unsent_cleanup_tasks.clear()
        self._cancel_caption_tasks_all()
        tasks = list(pending_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for key, dispatcher_info in list(self._bot_dispatchers.items()):
            try:
                setattr(
                    dispatcher_info["bot"],
                    dispatcher_info["method_name"],
                    dispatcher_info["original"],
                )
            except Exception:
                pass
            self._bot_dispatchers.pop(key, None)

    def _spawn_background(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)

        def completed(finished):
            self._background_tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.warning(
                    f"[消息防抖动] 后台清理失败: {finished.exception()}"
                )

        task.add_done_callback(completed)
        return task

    def _track_pipeline_completion(self, event, session: TurnSession) -> None:
        owner = asyncio.current_task()
        if owner is None or session.turn_id in self._pipeline_owners:
            return
        self._pipeline_owners[session.turn_id] = owner

        def completed(task):
            if self._pipeline_owners.pop(session.turn_id, None) is not task:
                return
            if not self._terminated:
                failed = task.cancelled() or task.exception() is not None
                self._spawn_background(self._finish_pipeline(event, session, failed))

        owner.add_done_callback(completed)

    async def _finish_pipeline(
        self, event, session: TurnSession, failed: bool = False
    ) -> None:
        if self.turns.get_turn(session.turn_id) is not session:
            return
        if failed or session.delivery_state == "provider_failed":
            self.turns.invalidate(session.turn_id)
            self._cancel_unsent_cleanup(session.turn_id)
            await self._recall_failed_attempt(event, session, 0)
        if session.invalidated:
            await self._rollback_history(event, session)
            self._restore_send_capture(session.turn_id)
            self._schedule_invalidated_cleanup(session)
            return
        if session.send_succeeded:
            self._commit_turn(event, session)
            return
        session.delivery_state = "finished_without_commit"
        self._cancel_unsent_cleanup(session.turn_id)
        self._restore_event_capture(session.turn_id)
        self._restore_send_capture(session.turn_id)
        self.turns.finish(session.turn_id)

    async def _timer_coroutine(self, session: TurnSession, duration: float):
        try:
            await asyncio.sleep(duration)
            if self.turns.get_session(session.uid) is session:
                session.flush_event.set()
        except asyncio.CancelledError:
            pass

    def _reset_timer(self, session: TurnSession, duration: float) -> None:
        if session.timer_task:
            session.timer_task.cancel()
        remaining = self.max_collection_wait - (
            time.monotonic() - session.collection_started_at
        )
        duration = max(0.0, min(duration, remaining))
        session.timer_task = asyncio.create_task(self._timer_coroutine(session, duration))

    def _calc_delay(self, session: TurnSession) -> float:
        tail_text = session.items[-1].text if session.items else ""
        has_image = bool(session.items[-1].images) if session.items else False
        return self.predictor.delay(tail_text, has_image=has_image, item_count=len(session.items))

    def _caption_probe_request(self, event: AstrMessageEvent, session: TurnSession):
        req = type("ContinuousMessageCaptionProbe", (), {})()
        req.session_id = event.unified_msg_origin
        req.image_urls = session.all_images()
        req.extra_user_content_parts = []
        return req

    @staticmethod
    def _caption_task_key(session: TurnSession, item, image_url: str) -> tuple[str, str, str]:
        return (session.turn_id, str(item.item_id), str(image_url))

    def _cancel_caption_tasks_all(self) -> None:
        for task in list(self._caption_tasks.values()):
            if not task.done():
                task.cancel()
        self._caption_tasks.clear()

    def _cancel_caption_tasks_for_session(self, session: TurnSession) -> None:
        prefix = session.turn_id
        for key, task in list(self._caption_tasks.items()):
            if key[0] != prefix:
                continue
            if not task.done():
                task.cancel()
            self._caption_tasks.pop(key, None)

    def _cancel_caption_tasks_for_item(self, session: TurnSession, item) -> None:
        for image_url in list(item.images):
            key = self._caption_task_key(session, item, image_url)
            task = self._caption_tasks.pop(key, None)
            if task and not task.done():
                task.cancel()

    def _find_items_by_message_id(self, session: TurnSession, message_id: Any) -> List:
        return [
            item for item in session.items
            if str(item.message_id) == str(message_id)
        ]

    def _start_caption_tasks_for_item(
        self, event: AstrMessageEvent, session: TurnSession, item
    ) -> None:
        if not item.images:
            return
        req = self._caption_probe_request(event, session)
        if not self.caption_bridge.get_caption_plan(event, req):
            return
        for image_url in item.images:
            if item.image_captions.get(image_url) is not None:
                continue
            key = self._caption_task_key(session, item, image_url)
            existing = self._caption_tasks.get(key)
            if existing and not existing.done():
                continue
            task = asyncio.create_task(
                self._caption_image_task(event, session, item, image_url)
            )
            self._caption_tasks[key] = task

    def _start_caption_tasks_for_session(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> None:
        for item in session.items:
            self._start_caption_tasks_for_item(event, session, item)

    async def _caption_image_task(
        self, event: AstrMessageEvent, session: TurnSession, item, image_url: str
    ) -> None:
        key = self._caption_task_key(session, item, image_url)
        req = self._caption_probe_request(event, session)
        try:
            caption = await self.caption_bridge.caption_image_for_request(
                event,
                req,
                image_url,
                should_continue=lambda: (
                    image_url in item.images
                    and item in session.items
                    and self._session_accepts_caption(session)
                ),
            )
            if caption is None:
                return
            if not (
                image_url in item.images
                and item in session.items
                and self._session_accepts_caption(session)
            ):
                return
            item.image_captions[image_url] = caption
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if (
                image_url in item.images
                and item in session.items
                and self._session_accepts_caption(session)
            ):
                item.image_captions[image_url] = CAPTION_FAILED_SENTINEL
            detail = self.caption_bridge.format_exception(exc)
            logger.warning(f"[消息防抖动] 预转述图片失败 | turn_id={session.turn_id} | {detail}")
        finally:
            self._caption_tasks.pop(key, None)

    def _session_accepts_caption(self, session: TurnSession) -> bool:
        if session.cancelled_reason == "all_recalled" or session.invalidated:
            return False
        if self.turns.get_session(session.uid) is session:
            return True
        return self._session_is_current_valid(session)

    async def _wait_prestarted_captions(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> Optional[bool]:
        self._start_caption_tasks_for_session(event, session)
        while True:
            if not self._session_accepts_caption(session):
                self._cancel_caption_tasks_for_session(session)
                return None
            pending = []
            for item in session.items:
                for image_url in item.images:
                    if item.image_captions.get(image_url) is not None:
                        continue
                    task = self._caption_tasks.get(
                        self._caption_task_key(session, item, image_url)
                    )
                    if task and not task.done():
                        pending.append(task)
            if not pending:
                return True
            done, _ = await asyncio.wait(pending, timeout=0.2)
            if not done:
                continue
            for task in done:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _collect_seed_items(self, uid: str) -> List:
        seed_items = []
        seen = set()
        for session in self.turns.sessions_for_uid(uid):
            if not self._session_is_interruptible(session):
                continue
            for item in session.seedable_items():
                key = (str(item.message_id), item.text, tuple(item.images))
                if key in seen:
                    continue
                seen.add(key)
                seed_items.append(item)
        return seed_items

    @staticmethod
    def _clone_history_snapshot(snapshot: Optional[List[dict]]) -> Optional[List[dict]]:
        if snapshot is None:
            return None
        return json.loads(json.dumps(snapshot, ensure_ascii=False))

    def _inherit_history_baseline(
        self,
        target: TurnSession,
        sources: List[TurnSession],
    ) -> bool:
        candidates = [
            session
            for session in sources
            if session.history_snapshot is not None
            and session.history_conversation_id
        ]
        if not candidates:
            return False
        source = min(candidates, key=lambda session: session.created_at)
        target.history_snapshot = self._clone_history_snapshot(source.history_snapshot)
        target.history_conversation_id = source.history_conversation_id
        target.history_generation = source.history_generation
        target.history_snapshot_inherited = True
        target.predecessor_turn_ids = [session.turn_id for session in sources]
        return True

    def _collect_seed_items_excluding_message(self, uid: str, message_id: Any) -> tuple[List, bool]:
        seed_items = []
        seen = set()
        found = False
        for session in self.turns.sessions_for_uid(uid):
            if not self._session_is_interruptible(session):
                continue
            for item in session.seedable_items():
                if str(item.message_id) == str(message_id):
                    found = True
                    continue
                key = (str(item.message_id), item.text, tuple(item.images))
                if key in seen:
                    continue
                seen.add(key)
                seed_items.append(item)
        return seed_items, found

    def _session_is_current_valid(self, session: TurnSession) -> bool:
        return (
            bool(session)
            and not session.invalidated
            and self.turns.is_valid(session.turn_id)
            and self.turns.get_turn(session.turn_id) is session
        )

    def _session_is_interruptible(self, session: TurnSession) -> bool:
        return (
            self._session_is_current_valid(session)
            and session.delivery_state in INTERRUPTIBLE_DELIVERY_STATES
        )

    def _stop_active_turns(self, uid: str, exclude_event: Optional[AstrMessageEvent] = None) -> List[str]:
        sessions_to_recall = [
            session for session in self.turns.sessions_for_uid(uid)
            if self._session_is_interruptible(session)
        ]
        if not sessions_to_recall:
            return []
        invalidated = []
        for session in sessions_to_recall:
            if session.turn_id in self.turns.invalid_turns:
                continue
            self.turns.invalidate(session.turn_id)
            invalidated.append(session.turn_id)
        if not invalidated:
            return []
        for session in sessions_to_recall:
            self._cancel_caption_tasks_for_session(session)
        try:
            from astrbot.core.utils.active_event_registry import active_event_registry

            active_event_registry.stop_all(uid, exclude=exclude_event)
        except Exception as exc:
            logger.debug(f"[消息防抖动] active_event_registry 不可用，已降级为本地失效标记: {exc}")
        logger.info(f"[消息防抖动] 已标记旧轮次失效 | uid={uid} | turns={invalidated}")
        for session in sessions_to_recall:
            self._cancel_unsent_cleanup(session.turn_id)
        if exclude_event is not None:
            for session in sessions_to_recall:
                self._spawn_background(self._recall_invalidated_turn(exclude_event, session))
        return invalidated

    async def _recall_invalidated_turn(self, event: AstrMessageEvent, session: TurnSession) -> None:
        await asyncio.sleep(0)
        if session.sent_message_ids:
            await self.recall_adapter.recall_messages(event, session.sent_message_ids)
        await self._rollback_history(event, session)
        self._schedule_rollback_retries(event, session)
        self._schedule_invalidated_cleanup(session)

    def _schedule_invalidated_cleanup(self, session: TurnSession) -> None:
        if session.cleanup_task and not session.cleanup_task.done():
            return
        session.cleanup_task = self._spawn_background(
            self._cleanup_invalidated_turn_later(
                session.turn_id,
                self.invalidated_turn_retention_seconds,
            )
        )

    async def _cleanup_invalidated_turn_later(self, turn_id: str, delay: float) -> None:
        await asyncio.sleep(max(delay, 0.0))
        session = self.turns.get_turn(turn_id)
        if session and session.invalidated:
            # Keep the per-event invalidation guard for late segmented sends.
            self._event_wrappers.pop(turn_id, None)
            self._restore_send_capture(turn_id)
            self.turns.finish(turn_id)

    def _cancel_unsent_cleanup(self, turn_id: str) -> None:
        task = self._unsent_cleanup_tasks.pop(turn_id, None)
        if task and not task.done():
            task.cancel()

    def _schedule_unsent_cleanup(
        self,
        session: TurnSession,
        delay: float = 8.0,
    ) -> None:
        turn_id = session.turn_id
        task = self._unsent_cleanup_tasks.get(turn_id)
        if task and not task.done():
            return
        self._unsent_cleanup_tasks[turn_id] = asyncio.create_task(
            self._cleanup_unsent_turn_later(turn_id, delay)
        )

    async def _cleanup_unsent_turn_later(self, turn_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(max(delay, 0.0))
            session = self.turns.get_turn(turn_id)
            if not session:
                return
            if not self._session_is_current_valid(session):
                return
            if session.sent_message_ids or session.send_depth:
                return
            owner = self._pipeline_owners.get(turn_id)
            if owner is not None and not owner.done():
                return
            if session.delivery_state not in {"llm_request", "decorating"}:
                return
            session.delivery_state = "empty_result"
            self._restore_event_capture(turn_id)
            self._restore_send_capture(turn_id)
            self.turns.finish(turn_id)
            logger.info(
                f"[消息防抖动] 本轮未进入发送阶段，已清理空结果轮次 | turn_id={turn_id}"
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._unsent_cleanup_tasks.pop(turn_id, None)

    @staticmethod
    def _result_has_sendable_content(result: Any) -> bool:
        if result is None:
            return False
        chain = getattr(result, "chain", None)
        if chain is None:
            return True
        for component in list(chain or []):
            if isinstance(component, str):
                if component.strip():
                    return True
                continue
            if isinstance(component, dict):
                comp_type = str(component.get("type", "")).lower()
                data = component.get("data") or {}
                text = str(data.get("text", component.get("text", ""))).strip()
                if text:
                    return True
                if comp_type and comp_type not in {"reply", "at", "plain", "text"}:
                    return True
                continue
            comp_type = type(component).__name__.lower()
            text = str(getattr(component, "text", "") or "").strip()
            if text:
                return True
            if comp_type not in {"reply", "at", "plain", "text"}:
                return True
        return False

    def _format_message_batch(self, session: TurnSession) -> str:
        lines = ['<message_batch source="continuous_message">']
        for item in session.items:
            attrs = f'index="{item.index}" kind="{item.kind}"'
            item_lines = [f"  <item {attrs}>"]
            parts = item.parts or (
                ([{"type": "text", "text": item.text}] if item.text else [])
                + [{"type": "image", "url": url} for url in item.images]
            )
            img_idx = 0
            for part in parts:
                if part.get("type") == "text":
                    item_lines.append(f"    <text>{escape(part.get('text', ''))}</text>")
                elif part.get("type") == "image":
                    img_idx += 1
                    item_lines.append(
                        f'    <image index="{img_idx}">{escape(part.get("url", ""))}</image>'
                    )
            item_lines.append("  </item>")
            lines.extend(item_lines)
        lines.append("</message_batch>")
        return "\n".join(lines)

    async def _snapshot_history(self, event: AstrMessageEvent, session: TurnSession) -> None:
        if session.history_snapshot is not None and session.history_conversation_id:
            return
        try:
            provider_request = event.get_extra("provider_request")
            conversation = getattr(provider_request, "conversation", None) if provider_request else None
            if conversation:
                cid = getattr(conversation, "cid", None)
                if cid:
                    session.history_conversation_id = cid
                if session.history_snapshot is None:
                    session.history_snapshot = json.loads(getattr(conversation, "history", "[]") or "[]")
                if session.history_conversation_id:
                    self._record_history_generation(event, session)
                return
            if session.history_snapshot is not None:
                return
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return
            cid = await conv_mgr.get_curr_conversation_id(event.unified_msg_origin)
            if not cid:
                session.history_snapshot = []
                return
            conversation = await conv_mgr.get_conversation(event.unified_msg_origin, cid)
            if not conversation:
                return
            session.history_conversation_id = cid
            session.history_snapshot = json.loads(getattr(conversation, "history", "[]") or "[]")
            self._record_history_generation(event, session)
        except Exception as exc:
            logger.debug(f"[消息防抖动] 历史快照失败，后续将跳过历史回滚: {exc}")

    async def _reconcile_inherited_history(
        self,
        event: AstrMessageEvent,
        req: Any,
        session: TurnSession,
    ) -> bool:
        if not session.history_snapshot_inherited or session.history_reconciled:
            return True
        if not self._session_is_current_valid(session):
            return False
        snapshot = self._clone_history_snapshot(session.history_snapshot)
        cid = session.history_conversation_id
        if snapshot is None or not cid:
            session.history_reconciled = True
            return True

        conversation = getattr(req, "conversation", None)
        request_cid = getattr(conversation, "cid", None) if conversation else None
        if request_cid and str(request_cid) != str(cid):
            logger.warning(
                f"[消息防抖动] 继承历史会话ID不一致，已阻断请求 | "
                f"turn_id={session.turn_id} | inherited_cid={cid} | request_cid={request_cid}"
            )
            return False

        try:
            request_contexts = list(getattr(req, "contexts", []) or [])
            persisted_history: List[dict] = []
            if conversation is not None:
                raw_history = getattr(conversation, "history", "[]") or "[]"
                if isinstance(raw_history, str):
                    persisted_history = json.loads(raw_history)
                elif isinstance(raw_history, list):
                    persisted_history = self._clone_history_snapshot(raw_history) or []

            contexts_before_history: List[dict] = []
            contexts_after_history: List[dict] = []
            history_segment_found = False
            if persisted_history:
                segment_size = len(persisted_history)
                for start in range(len(request_contexts) - segment_size + 1):
                    if request_contexts[start:start + segment_size] == persisted_history:
                        contexts_before_history = request_contexts[:start]
                        contexts_after_history = request_contexts[start + segment_size:]
                        history_segment_found = True
                        break
            else:
                contexts_after_history = request_contexts
                history_segment_found = True
            if persisted_history and request_contexts and not history_segment_found:
                logger.warning(
                    f"[消息防抖动] 请求上下文中未找到持久历史区段，"
                    f"保留请求上下文，避免删除其他插件内容 | turn_id={session.turn_id}"
                )

            if conversation is not None:
                conversation.history = json.dumps(snapshot, ensure_ascii=False)
            if hasattr(req, "contexts") and (history_segment_found or not request_contexts):
                req.contexts = (
                    contexts_before_history
                    + (self._clone_history_snapshot(snapshot) or [])
                    + contexts_after_history
                )
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if conv_mgr:
                await conv_mgr.update_conversation(
                    event.unified_msg_origin,
                    cid,
                    history=snapshot,
                )
            session.history_reconciled = True
            logger.info(
                f"[消息防抖动] 已在新请求前恢复继承历史基线 | "
                f"turn_id={session.turn_id} | predecessors={session.predecessor_turn_ids}"
            )
            return True
        except Exception as exc:
            logger.warning(
                f"[消息防抖动] 恢复继承历史基线失败，已阻断请求 | "
                f"turn_id={session.turn_id} | {exc}"
            )
            return False

    def _history_generation_key(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> Optional[tuple[str, str]]:
        if not session.history_conversation_id:
            return None
        return (event.unified_msg_origin, str(session.history_conversation_id))

    def _record_history_generation(self, event: AstrMessageEvent, session: TurnSession) -> None:
        key = self._history_generation_key(event, session)
        if not key:
            return
        session.history_generation = self._history_generations.get(key, 0)

    def _advance_history_generation(self, event: AstrMessageEvent, session: TurnSession) -> None:
        key = self._history_generation_key(event, session)
        if not key:
            return
        current = self._history_generations.get(key, 0)
        next_generation = max(current, session.history_generation) + 1
        self._history_generations[key] = next_generation
        session.history_generation = next_generation

    def _history_generation_unchanged(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> bool:
        key = self._history_generation_key(event, session)
        if not key:
            return False
        return self._history_generations.get(key, 0) == session.history_generation

    async def _rollback_history(
        self, event: AstrMessageEvent, session: TurnSession, force: bool = False
    ) -> None:
        if session.history_snapshot is None or not session.history_conversation_id:
            return
        # Even immediate cleanup must never overwrite a newer turn.
        if not self._history_generation_unchanged(event, session):
            logger.info(
                f"[消息防抖动] 跳过旧轮次历史回滚，已有更新轮次完成 | turn_id={session.turn_id}"
            )
            return
        try:
            conv_mgr = getattr(self.context, "conversation_manager", None)
            if not conv_mgr:
                return
            await conv_mgr.update_conversation(
                event.unified_msg_origin,
                session.history_conversation_id,
                history=session.history_snapshot,
            )
            logger.info(f"[消息防抖动] 已回滚失效轮次历史 | turn_id={session.turn_id}")
        except Exception as exc:
            logger.warning(f"[消息防抖动] 历史回滚失败 | turn_id={session.turn_id} | {exc}")

    def _schedule_rollback_retries(self, event: AstrMessageEvent, session: TurnSession) -> None:
        for raw_delay in self.history_rollback_delays:
            try:
                delay = float(raw_delay)
            except (TypeError, ValueError):
                continue
            self._spawn_background(self._rollback_history_later(event, session, delay))

    async def _rollback_history_later(
        self, event: AstrMessageEvent, session: TurnSession, delay: float
    ) -> None:
        await asyncio.sleep(max(delay, 0.0))
        if session.invalidated or not self._session_is_current_valid(session):
            await self._rollback_history(event, session)

    def _attach_turn_extras(self, event: AstrMessageEvent, session: TurnSession) -> None:
        items = session.items_as_dicts()
        event.set_extra(EXTRA_TURN_ID, session.turn_id)
        event.set_extra(EXTRA_TURN_ITEMS, items)
        event.set_extra(EXTRA_TURN_HISTORY, session.history_snapshot)
        event.set_extra(EXTRA_SENT_IDS, session.sent_message_ids)

    @staticmethod
    def _event_raw_message(event: AstrMessageEvent) -> Dict[str, Any]:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        return raw if isinstance(raw, dict) else {}

    def _send_targets_event(
        self,
        event: AstrMessageEvent,
        action_name: str,
        args: tuple,
        kwargs: Dict[str, Any],
    ) -> bool:
        raw = self._event_raw_message(event)
        expected_user_id = raw.get("user_id")
        expected_group_id = raw.get("group_id")
        target_user_id = kwargs.get("user_id")
        target_group_id = kwargs.get("group_id")
        if target_user_id is None and args and action_name == "send_private_msg":
            target_user_id = args[0]
        if target_group_id is None and args and action_name == "send_group_msg":
            target_group_id = args[0]
        if action_name == "send_group_msg" or target_group_id is not None:
            return (
                expected_group_id is not None
                and target_group_id is not None
                and str(target_group_id) == str(expected_group_id)
            )
        return (
            action_name in {"send_private_msg", "send_msg"}
            and expected_group_id is None
            and target_user_id is not None
            and expected_user_id is not None
            and str(target_user_id) == str(expected_user_id)
        )

    def _install_send_capture(self, event: AstrMessageEvent, session: TurnSession) -> None:
        bot = getattr(event, "bot", None)
        if bot is None:
            return
        if session.turn_id in self._send_wrappers:
            return
        registrations: List[dict] = []
        for name in ("send_private_msg", "send_group_msg"):
            registration = self._register_bot_send_capture(bot, name, event, session)
            if registration:
                registrations.append(registration)
        registration = self._register_bot_send_capture(bot, "call_action", event, session)
        if registration:
            registrations.append(registration)
        if not registrations:
            return
        self._send_wrappers[session.turn_id] = registrations

    def _register_bot_send_capture(
        self,
        bot: Any,
        method_name: str,
        event: AstrMessageEvent,
        session: TurnSession,
    ) -> Optional[dict]:
        original = getattr(bot, method_name, None)
        if not original:
            return None
        key = (id(bot), method_name)
        dispatcher_info = self._bot_dispatchers.get(key)
        if dispatcher_info is None:
            dispatcher_info = {
                "bot": bot,
                "method_name": method_name,
                "original": original,
                "registrations": [],
                "dispatcher": None,
            }

            async def dispatcher(*args, **kwargs):
                return await self._dispatch_bot_send(dispatcher_info, *args, **kwargs)

            dispatcher_info["dispatcher"] = dispatcher
            self._bot_dispatchers[key] = dispatcher_info
            setattr(bot, method_name, dispatcher)

        registration = {
            "turn_id": session.turn_id,
            "event": event,
            "session": session,
            "method_name": method_name,
        }
        dispatcher_info["registrations"].append(registration)
        return registration

    async def _dispatch_bot_send(self, dispatcher_info: dict, *args, **kwargs):
        method_name = dispatcher_info["method_name"]
        original = dispatcher_info["original"]
        owner = self._send_context.get()
        if owner is None or not owner["active"]:
            return await original(*args, **kwargs)
        owner_event, owner_session = owner["event"], owner["session"]
        registrations = list(dispatcher_info["registrations"])
        action_name = method_name
        target_args = args
        target_kwargs = dict(kwargs)

        if method_name == "call_action":
            action_name = str(args[0] if args else kwargs.get("action"))
            if not action_name.startswith("send_"):
                return await original(*args, **kwargs)
            target_args = args[1:]
            params = target_kwargs.get("params")
            if isinstance(params, dict):
                target_kwargs.update(params)

        matched = [
            registration for registration in registrations
            if registration["event"] is owner_event
            and registration["session"] is owner_session
            and self._send_targets_event(
                registration["event"], action_name, target_args, target_kwargs
            )
        ]
        if not matched:
            return await original(*args, **kwargs)

        valid_matched = [
            registration for registration in matched
            if self._session_is_current_valid(registration["session"])
        ]
        if valid_matched:
            registration = valid_matched[-1]
            session = registration["session"]
            event = registration["event"]
            before_count = len(session.sent_message_ids)
            session.delivery_state = "sending"
            try:
                result = await original(*args, **kwargs)
            except Exception as exc:
                await self._handle_send_failure(event, session, exc)
                raise
            self._collect_message_ids(result, session.sent_message_ids)
            await self._recall_new_ids_if_invalid(event, session, before_count)
            return result

        registration = matched[-1]
        registration["event"].stop_event()
        return None

    async def _recall_new_ids_if_invalid(
        self, event: AstrMessageEvent, session: TurnSession, before_count: int
    ) -> None:
        if self._session_is_current_valid(session):
            return
        new_ids = session.sent_message_ids[before_count:]
        if new_ids:
            await self.recall_adapter.recall_messages(event, new_ids)
        await self._rollback_history(event, session)
        self._schedule_rollback_retries(event, session)

    async def _handle_send_failure(
        self, event: AstrMessageEvent, session: TurnSession, exc: BaseException
    ) -> None:
        already_failed = session.delivery_state == "send_failed"
        session.delivery_state = "send_failed"
        self._cancel_unsent_cleanup(session.turn_id)
        self.turns.invalidate(session.turn_id)
        event.stop_event()
        # The core can continue its segmented-send loop after this exception.
        # Keep event guards installed so the remaining segments are suppressed.
        self._restore_send_capture(session.turn_id)
        if already_failed:
            return
        await self._rollback_history(event, session, force=True)
        self._schedule_rollback_retries(event, session)
        self._schedule_invalidated_cleanup(session)
        logger.warning(f"[消息防抖动] 本轮发送失败，已回滚历史 | turn_id={session.turn_id} | {exc}")

    @staticmethod
    def _is_source_exception(exc: BaseException, sources: List[BaseException]) -> bool:
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            if any(exc is source for source in sources):
                return True
            exc = exc.__cause__
        return False

    async def _recall_failed_attempt(
        self, event, session: TurnSession, start: int
    ) -> None:
        message_ids = session.sent_message_ids[start:]
        if not message_ids:
            return
        recalled = await self.recall_adapter.recall_messages(event, message_ids)
        if recalled:
            session.sent_message_ids[:] = [
                message_id for message_id in session.sent_message_ids
                if message_id not in recalled
            ]
        if len(recalled) < len(message_ids):
            logger.warning(
                f"[消息防抖动] 上游失败后部分分段未能撤回 | turn_id={session.turn_id}"
            )

    def _install_event_capture(self, event: AstrMessageEvent, session: TurnSession) -> None:
        if session.turn_id in self._event_wrappers:
            return
        wrappers: List[tuple[Any, str, Any]] = []
        original_send = getattr(event, "send", None)
        original_send_streaming = getattr(event, "send_streaming", None)

        if original_send:
            async def send_wrapped(message, *args, **kwargs):
                if not self._session_is_current_valid(session):
                    event.stop_event()
                    return None
                self._cancel_unsent_cleanup(session.turn_id)
                before_count = len(session.sent_message_ids)
                session.delivery_state = "sending"
                session.send_depth += 1
                lease = {"event": event, "session": session, "active": True}
                token = self._send_context.set(lease)
                try:
                    result = await original_send(message, *args, **kwargs)
                except Exception as exc:
                    await self._handle_send_failure(event, session, exc)
                    raise
                finally:
                    lease["active"] = False
                    self._send_context.reset(token)
                    session.send_depth -= 1
                session.send_succeeded = True
                self._collect_message_ids(result, session.sent_message_ids)
                await self._recall_new_ids_if_invalid(event, session, before_count)
                return result

            wrappers.append((event, "send", original_send))
            setattr(event, "send", send_wrapped)

        if original_send_streaming:
            async def send_streaming_wrapped(generator, *args, **kwargs):
                if not self._session_is_current_valid(session):
                    event.stop_event()
                    return None
                self._cancel_unsent_cleanup(session.turn_id)
                session.delivery_state = "streaming"
                session.send_depth += 1
                source_errors: List[BaseException] = []

                async def guarded_generator():
                    try:
                        async for chunk in generator:
                            if not self._session_is_current_valid(session):
                                event.stop_event()
                                break
                            yield chunk
                    except Exception as exc:
                        source_errors.append(exc)
                        raise
                    finally:
                        close = getattr(generator, "aclose", None)
                        if close is not None:
                            await close()

                lease = {"event": event, "session": session, "active": True}
                token = self._send_context.set(lease)
                guarded = guarded_generator()
                try:
                    before_count = len(session.sent_message_ids)
                    try:
                        result = await original_send_streaming(
                            guarded, *args, **kwargs
                        )
                    except Exception as exc:
                        if self._is_source_exception(exc, source_errors):
                            if not session.invalidated:
                                session.delivery_state = "provider_failed"
                            await self._recall_failed_attempt(event, session, before_count)
                            logger.warning(
                                f"[消息防抖动] 上游流异常，保留框架重试/备用模型处理 | "
                                f"turn_id={session.turn_id} | {type(exc).__name__}: {exc}"
                            )
                        else:
                            await self._handle_send_failure(event, session, exc)
                        raise
                    self._collect_message_ids(result, session.sent_message_ids)
                    await self._recall_new_ids_if_invalid(event, session, before_count)
                    if source_errors:
                        if not session.invalidated:
                            session.delivery_state = "provider_failed"
                        await self._recall_failed_attempt(event, session, before_count)
                    elif self._session_is_current_valid(session):
                        self._commit_turn(event, session)
                    return result
                finally:
                    lease["active"] = False
                    self._send_context.reset(token)
                    session.send_depth -= 1
                    await guarded.aclose()

            wrappers.append((event, "send_streaming", original_send_streaming))
            setattr(event, "send_streaming", send_streaming_wrapped)

        self._event_wrappers[session.turn_id] = wrappers

    def _commit_turn(self, event, session: TurnSession) -> None:
        if not self._session_is_current_valid(session):
            return
        self._cancel_unsent_cleanup(session.turn_id)
        session.delivery_state = "committed"
        event.set_extra("continuous_message_committed", True)
        self._advance_history_generation(event, session)
        self._restore_send_capture(session.turn_id)
        self._restore_event_capture(session.turn_id)
        self._pipeline_owners.pop(session.turn_id, None)
        self.turns.finish(session.turn_id)

    def _restore_event_capture(self, turn_id: str) -> None:
        wrappers = self._event_wrappers.pop(turn_id, [])
        for obj, name, original in wrappers:
            try:
                setattr(obj, name, original)
            except Exception:
                pass

    def _restore_send_capture(self, turn_id: str) -> None:
        registrations = self._send_wrappers.pop(turn_id, [])
        touched_keys = set()
        for registration in registrations:
            bot = getattr(registration.get("event"), "bot", None)
            method_name = registration.get("method_name")
            if bot is not None and method_name:
                touched_keys.add((id(bot), method_name))
        for key in touched_keys:
            dispatcher_info = self._bot_dispatchers.get(key)
            if not dispatcher_info:
                continue
            dispatcher_info["registrations"] = [
                registration for registration in dispatcher_info["registrations"]
                if registration.get("turn_id") != turn_id
            ]
            if dispatcher_info["registrations"]:
                continue
            try:
                setattr(
                    dispatcher_info["bot"],
                    dispatcher_info["method_name"],
                    dispatcher_info["original"],
                )
            except Exception:
                pass
            self._bot_dispatchers.pop(key, None)

    @staticmethod
    def _collect_message_ids(result: Any, target: List[Any]) -> None:
        if isinstance(result, dict):
            for key in ("message_id", "msg_id"):
                if result.get(key) is not None and result[key] not in target:
                    target.append(result[key])
            data = result.get("data")
            if isinstance(data, dict):
                for key in ("message_id", "msg_id"):
                    if data.get(key) is not None and data[key] not in target:
                        target.append(data[key])
        elif isinstance(result, list):
            for item in result:
                ContinuousMessagePlugin._collect_message_ids(item, target)

    async def _append_forward_or_reply(self, event: AstrMessageEvent, raw_text: str, current_urls: List[str]):
        if not (self.enable_forward_analysis and IS_AIOCQHTTP and isinstance(event, AiocqhttpMessageEvent)):
            return raw_text, current_urls, bool(current_urls)
        forward_text = ""
        forward_images: List[str] = []
        forward_id = await self.forward_handler.detect_forward_message(event)
        if forward_id:
            try:
                forward_text, forward_images = await self.forward_handler.extract_forward_content(event, forward_id)
                if forward_text or forward_images:
                    logger.info(f"[消息防抖动] 检测到合并转发 | 文本: {len(forward_text)}字 | 图片: {len(forward_images)}张")
            except Exception as exc:
                logger.error(f"[消息防抖动] 提取合并转发失败: {exc}")
        else:
            reply_text, reply_images = await self.forward_handler.extract_reply_content(event)
            if reply_text or reply_images:
                forward_text = reply_text
                forward_images = reply_images
        if forward_text:
            raw_text = forward_text + ("\n" + raw_text if raw_text else "")
        if forward_images:
            current_urls.extend(forward_images)
        return raw_text, current_urls, bool(current_urls)

    async def _handle_recall_event(self, event: AstrMessageEvent) -> None:
        recalled_mid = self.parser.get_recalled_message_id(event)
        uid = event.unified_msg_origin
        session = self.turns.get_session(uid)
        if recalled_mid is not None and session:
            removed_items = self._find_items_by_message_id(session, recalled_mid)
            for item in removed_items:
                self._cancel_caption_tasks_for_item(session, item)
            if session.remove_message(recalled_mid):
                logger.info(f"[消息防抖动] 已过滤撤回消息 | message_id={recalled_mid} | 剩余 {len(session.items)} 条")
                if not session.items:
                    session.cancelled_reason = "all_recalled"
                    if session.timer_task:
                        session.timer_task.cancel()
                    self._cancel_caption_tasks_for_session(session)
                    if self.turns.get_session(uid) is session:
                        self.turns.pop_session(uid)
                    session.flush_event.set()
            else:
                logger.debug(f"[消息防抖动] 收到撤回通知但未找到待合并消息 | message_id={recalled_mid}")
            event.stop_event()
            return

        seed_items, matched_active = self._collect_seed_items_excluding_message(uid, recalled_mid)
        if recalled_mid is not None and matched_active and seed_items:
            source_sessions = [
                source
                for source in self.turns.sessions_for_uid(uid)
                if self._session_is_interruptible(source)
            ]
            new_session = self.turns.create_session(uid)
            self._inherit_history_baseline(new_session, source_sessions)
            self._stop_active_turns(uid, exclude_event=event)
            new_session.seed_from(seed_items)
            self._start_caption_tasks_for_session(event, new_session)
            delay = self._calc_delay(new_session)
            self._reset_timer(new_session, delay)
            logger.info(
                f"[消息防抖动] 活跃轮次消息被撤回，继承剩余用户消息重新防抖 | "
                f"message_id={recalled_mid} | inherited={len(seed_items)} | delay={delay}s"
            )
            await self._flush_collected_session(event, new_session)
            return

        if recalled_mid is not None and matched_active:
            invalidated = self._stop_active_turns(uid, exclude_event=event)
            if invalidated:
                logger.info(
                    f"[消息防抖动] 活跃轮次消息已全部撤回，静默中断 | message_id={recalled_mid}"
                )
        elif recalled_mid is not None:
            logger.debug(
                f"[消息防抖动] 收到不属于当前防抖轮次的撤回通知，已静默忽略 | message_id={recalled_mid}"
            )
        event.stop_event()

    async def _flush_collected_session(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> bool:
        await session.flush_event.wait()

        current = self.turns.pop_session(session.uid, expected=session)
        if current is not session:
            if session.timer_task:
                session.timer_task.cancel()
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            return False
        if session.timer_task:
            session.timer_task.cancel()

        if session.cancelled_reason == "all_recalled" or not session.has_content():
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            logger.info(
                f"[消息防抖动] 本轮消息已全部撤回，静默结束 | turn_id={session.turn_id}"
            )
            return False

        self.turns.mark_pending(session)
        merged_text = self._format_message_batch(session)
        all_images = session.all_images()

        if not merged_text and not all_images:
            self._cancel_caption_tasks_for_session(session)
            self.turns.finish(session.turn_id)
            event.stop_event()
            return False

        if not self._session_is_current_valid(session):
            self._cancel_caption_tasks_for_session(session)
            event.stop_event()
            return False

        if session.all_images():
            caption_ready = await self._wait_prestarted_captions(event, session)
            if caption_ready is None:
                self._schedule_invalidated_cleanup(session)
                event.stop_event()
                return False
            caption_text = await self._caption_before_core_image_flow(event, session)
            if caption_text is None:
                self._schedule_invalidated_cleanup(session)
                event.stop_event()
                return False
            if caption_text:
                merged_text = f"{merged_text}\n{caption_text}" if merged_text else caption_text
                original_images = set(session.all_images())
                all_images = [url for url in all_images if url not in original_images]
                event.set_extra(EXTRA_CAPTION_HANDLED, True)

        if not self._session_is_current_valid(session):
            event.stop_event()
            return False

        self._attach_turn_extras(event, session)
        await self._snapshot_history(event, session)

        self.turns.activate(session)
        self._attach_turn_extras(event, session)
        self.parser.reconstruct_event(event, merged_text, all_images)
        logger.info(
            f"[消息防抖动] 结算触发 | turn_id={session.turn_id} | items={len(session.items)} "
            f"| 图片={len(all_images)}"
        )
        return True

    async def _caption_before_core_image_flow(
        self, event: AstrMessageEvent, session: TurnSession
    ) -> Optional[str]:
        req = self._caption_probe_request(event, session)
        try:
            return await self.caption_bridge.build_ordered_caption_text(
                event,
                req,
                session.items_as_dicts(),
                should_continue=lambda: self._session_is_current_valid(session),
            )
        except Exception as exc:
            logger.warning(f"[消息防抖动] 结算阶段图片转述失败，降级为保留原图: {exc}")
            return ""

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=50)
    async def handle_private_msg(self, event: AstrMessageEvent):
        if not self.enable_plugin or self.debounce_time <= 0:
            return

        uid = event.unified_msg_origin

        if self.enable_typing_detection and self.parser.is_typing_event(event):
            raw = event.message_obj.raw_message
            status_text = raw.get("status_text", "")
            is_typing = "正在输入" in status_text
            session = self.turns.get_session(uid)
            if session:
                session.is_typing = is_typing
                duration = self.max_typing_wait if is_typing else self._calc_delay(session)
                self._reset_timer(session, duration)
                logger.debug(f"[消息防抖动] 输入状态更新 | is_typing={is_typing} | timer={duration}s | uid={uid}")
            event.stop_event()
            return

        if self.enable_recall_filter and self.parser.is_recall_event(event):
            await self._handle_recall_event(event)
            return

        parts = self.parser.parse_message_parts(event.message_obj)
        raw_text = "".join(part["text"] for part in parts if part["type"] == "text")
        current_urls = [
            part["url"] for part in parts if part["type"] == "image" and part.get("url")
        ]
        has_image = bool(current_urls)
        if not raw_text:
            raw_text = (event.message_str or "").strip()
            if raw_text:
                parts.insert(0, {"type": "text", "text": raw_text})
        original_text = raw_text
        original_image_count = len(current_urls)
        raw_text, current_urls, has_image = await self._append_forward_or_reply(event, raw_text, current_urls)
        if raw_text != original_text:
            prefix = raw_text[:-(len(original_text) + 1)] if original_text else raw_text
            parts.insert(0, {"type": "text", "text": prefix})
        parts.extend(
            {"type": "image", "url": url}
            for url in current_urls[original_image_count:]
        )

        if self.parser.is_command(raw_text, self.command_prefixes):
            session = self.turns.get_session(uid)
            if session:
                if session.timer_task:
                    session.timer_task.cancel()
                session.flush_event.set()
            self._stop_active_turns(uid, exclude_event=event)
            return

        if not raw_text and not has_image:
            return

        message_id = self.parser.get_message_id(event)
        session = self.turns.get_session(uid)
        if session and session.cancelled_reason == "all_recalled":
            self.turns.pop_session(uid)
            session = None
        if session:
            item = session.add_item(message_id, raw_text, current_urls, parts=parts)
            self._start_caption_tasks_for_item(event, session, item)
            delay = self._calc_delay(session)
            self._reset_timer(session, delay)
            logger.debug(
                f"[消息防抖动] 追加消息 | turn_id={session.turn_id} | message_id={message_id} "
                f"| items={len(session.items)} | delay={delay}s | 图片={len(current_urls)}"
            )
            event.stop_event()
            return

        source_sessions = [
            source
            for source in self.turns.sessions_for_uid(uid)
            if self._session_is_interruptible(source)
        ]
        session = self.turns.create_session(uid)
        seed_items = self._collect_seed_items(uid)
        self._inherit_history_baseline(session, source_sessions)
        self._stop_active_turns(uid, exclude_event=event)
        if seed_items:
            session.seed_from(seed_items)
            self._start_caption_tasks_for_session(event, session)
            logger.info(
                f"[消息防抖动] 新轮次继承旧用户消息 | inherited={len(seed_items)} | uid={uid}"
            )
        item = session.add_item(message_id, raw_text, current_urls, parts=parts)
        self._start_caption_tasks_for_item(event, session, item)
        delay = self._calc_delay(session)
        self._reset_timer(session, delay)
        logger.info(f"[消息防抖动] 开始收集 | turn_id={session.turn_id} | delay={delay}s | uid={uid}")

        await self._flush_collected_session(event, session)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not self.turns.is_valid(turn_id) or not session:
            event.stop_event()
            logger.info(f"[消息防抖动] 已阻断失效 LLM 请求 | turn_id={turn_id}")
            return
        session.delivery_state = "llm_request"
        self._cancel_unsent_cleanup(turn_id)
        self._track_pipeline_completion(event, session)
        if not await self._reconcile_inherited_history(event, req, session):
            self.turns.invalidate(turn_id)
            self._schedule_invalidated_cleanup(session)
            event.stop_event()
            return
        if not session.history_snapshot_inherited:
            # The core session lock is held here; pre-debounce snapshots can
            # predate the previous turn's final history write.
            session.history_snapshot = None
            session.history_conversation_id = None
            event.set_extra("provider_request", req)
        await self._snapshot_history(event, session)
        self._advance_history_generation(event, session)
        self._install_event_capture(event, session)
        self._install_send_capture(event, session)
        if not getattr(req, "session_id", ""):
            req.session_id = event.unified_msg_origin
        if not self.turns.is_valid(turn_id):
            event.stop_event()
            return
        if event.get_extra(EXTRA_CAPTION_HANDLED, False):
            original_images = set(session.all_images())
            req.image_urls = [
                url for url in (req.image_urls or []) if url not in original_images
            ]
        else:
            await self.caption_bridge.apply(
                event,
                req,
                event.get_extra(EXTRA_TURN_ITEMS, []),
                should_continue=lambda: self._session_is_current_valid(session),
            )
        if not self.turns.is_valid(turn_id):
            req.image_urls = []
            event.stop_event()

    @filter.on_llm_response(priority=100000)
    async def on_llm_response(self, event: AstrMessageEvent, resp) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not session:
            return
        if not self._session_is_current_valid(session):
            session.delivery_state = "invalidated_response"
            try:
                result_chain = getattr(resp, "result_chain", None)
                if result_chain is not None and hasattr(result_chain, "chain"):
                    result_chain.chain = []
                resp.completion_text = ""
                for attr in ("tools_call_args", "tools_call_name", "tools_call_ids"):
                    if hasattr(resp, attr):
                        setattr(resp, attr, [])
            except Exception as exc:
                logger.debug(
                    f"[消息防抖动] 清空迟到失效响应时发生兼容性异常 | "
                    f"turn_id={turn_id} | {exc}"
                )
            event.stop_event()
            await self._rollback_history(event, session)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
            self._restore_send_capture(turn_id)
            logger.info(
                f"[消息防抖动] 已阻断迟到的失效 LLM 响应并安排返回后回滚 | "
                f"turn_id={turn_id}"
            )
            return

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        if not session and event.get_extra("continuous_message_committed", False):
            return
        if self.turns.is_valid(turn_id) and session:
            session.delivery_state = "decorating"
            self._install_event_capture(event, session)
            self._install_send_capture(event, session)
            cleanup_delay = None
            try:
                result = event.get_result()
                if not getattr(result, "async_stream", None) and not self._result_has_sendable_content(result):
                    cleanup_delay = 0.5
            except Exception:
                pass
            if cleanup_delay is not None:
                self._schedule_unsent_cleanup(session, delay=cleanup_delay)
            return
        self._restore_send_capture(turn_id)
        self._cancel_unsent_cleanup(turn_id)
        result = event.get_result()
        if result is not None:
            result.chain = []
        if session:
            await self._rollback_history(event, session)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
        event.stop_event()
        logger.info(f"[消息防抖动] 已清空失效轮次发送前结果 | turn_id={turn_id}")

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        turn_id = event.get_extra(EXTRA_TURN_ID)
        if not turn_id:
            return
        session = self.turns.get_turn(turn_id)
        sent_ids = list(event.get_extra(EXTRA_SENT_IDS, []) or [])
        if session:
            for msg_id in sent_ids:
                if msg_id not in session.sent_message_ids:
                    session.sent_message_ids.append(msg_id)
        if session and session.delivery_state == "send_failed":
            self._cancel_unsent_cleanup(turn_id)
            self._restore_send_capture(turn_id)
            await self._rollback_history(event, session, force=True)
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
            return
        if session and self._session_is_current_valid(session):
            self._cancel_unsent_cleanup(turn_id)
            if session.send_depth:
                return
            if session.delivery_state == "provider_failed":
                return
            self._commit_turn(event, session)
            return
        if session:
            self._cancel_unsent_cleanup(turn_id)
            self._restore_send_capture(turn_id)
            recalled = await self.recall_adapter.recall_messages(event, session.sent_message_ids)
            if not recalled and session.sent_message_ids:
                logger.warning(f"[消息防抖动] 失效轮次有已发消息但未能撤回 | turn_id={turn_id}")
            await self._rollback_history(event, session, force=session.delivery_state == "send_failed")
            self._schedule_rollback_retries(event, session)
            self._schedule_invalidated_cleanup(session)
