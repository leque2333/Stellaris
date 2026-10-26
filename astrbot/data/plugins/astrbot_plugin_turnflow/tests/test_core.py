import asyncio
import importlib
import json
import logging
import os
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path(os.environ.get(
    "TURNFLOW_TEST_PLUGIN", ROOT
))


class Plain:
    def __init__(self, text):
        self.text = text


class Image:
    def __init__(self, file=None, url=None):
        self.file = file
        self.url = url


class Star:
    def __init__(self, context):
        self.context = context


def decorator(*args, **kwargs):
    return lambda function: function


def load_plugin():
    package = types.ModuleType("regression_plugin")
    package.__path__ = [str(PLUGIN)]
    astrbot = types.ModuleType("astrbot")
    astrbot.__path__ = []
    api = types.ModuleType("astrbot.api")
    api.__path__ = []
    api.logger = logging.getLogger("continuous-regression")
    api.logger.addHandler(logging.NullHandler())
    api.AstrBotConfig = dict
    events = types.ModuleType("astrbot.api.event")
    events.AstrMessageEvent = object
    events.filter = types.SimpleNamespace(
        event_message_type=decorator,
        on_llm_request=decorator,
        on_llm_response=decorator,
        on_decorating_result=decorator,
        after_message_sent=decorator,
        EventMessageType=types.SimpleNamespace(PRIVATE_MESSAGE=1),
    )
    stars = types.ModuleType("astrbot.api.star")
    stars.Context = object
    stars.Star = Star
    stars.register = decorator
    components = types.ModuleType("astrbot.api.message_components")
    components.Plain = Plain
    components.Image = Image
    astrbot.api = api
    api.message_components = components
    modules = {
        "regression_plugin": package,
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": events,
        "astrbot.api.star": stars,
        "astrbot.api.message_components": components,
    }
    with patch.dict(sys.modules, modules):
        return importlib.import_module("regression_plugin.main")


main = load_plugin()


class Bot:
    def __init__(self):
        self.sent = []
        self.recalled = []
        self.fail_next = False

    async def send_private_msg(self, user_id=None, message=None, **kwargs):
        return await self.call_action(
            "send_private_msg", user_id=user_id, message=message
        )

    async def send_group_msg(self, group_id=None, message=None, **kwargs):
        return await self.call_action(
            "send_group_msg", group_id=group_id, message=message
        )

    async def call_action(self, action, **kwargs):
        if action == "delete_msg":
            self.recalled.append(kwargs["message_id"])
            return {}
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("platform send failure")
        self.sent.append((action, kwargs))
        return {"message_id": len(self.sent)}


class Event:
    def __init__(self, bot=None, uid="private:1"):
        self.bot = bot or Bot()
        self.unified_msg_origin = uid
        self.message_obj = types.SimpleNamespace(
            raw_message={"user_id": 1, "message_id": 1}, message=[Plain("hello")]
        )
        self.message_str = "hello"
        self.extras = {}
        self.stopped = False
        self.result = types.SimpleNamespace(chain=[Plain("reply")], async_stream=None)

    def set_extra(self, key, value):
        self.extras[key] = value

    def get_extra(self, key, default=None):
        return self.extras.get(key, default)

    def stop_event(self):
        self.stopped = True

    def is_stopped(self):
        return self.stopped

    def get_result(self):
        return self.result

    async def send(self, message, *args, **kwargs):
        return await self.bot.send_private_msg(user_id=1, message=message)

    async def send_streaming(self, generator, *args, **kwargs):
        async for message in generator:
            await self.send(message)


class CoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = types.SimpleNamespace(
            update_conversation=AsyncMock(),
            get_curr_conversation_id=AsyncMock(return_value=None),
        )
        context = types.SimpleNamespace(conversation_manager=self.manager)
        self.plugin = main.ContinuousMessagePlugin(context, {
            "enable_ordered_image_caption": False,
            "history_rollback_delays": [],
        })

    async def asyncTearDown(self):
        await self.plugin.terminate()
        await asyncio.sleep(0)

    def active(self, event=None):
        event = event or Event()
        session = self.plugin.turns.create_session(event.unified_msg_origin)
        session.add_item(1, "hello", [])
        self.plugin.turns.activate(session)
        session.delivery_state = "llm_request"
        session.history_snapshot = []
        session.history_conversation_id = "conversation"
        self.plugin._advance_history_generation(event, session)
        self.plugin._attach_turn_extras(event, session)
        self.plugin._install_event_capture(event, session)
        self.plugin._install_send_capture(event, session)
        return event, session

    async def test_nonstream_segments_commit_only_after_final_hook(self):
        event, session = self.active()
        await self.plugin.on_decorating_result(event)
        self.assertNotIn(session.turn_id, self.plugin._unsent_cleanup_tasks)
        await event.send("segment 1")
        self.assertIs(self.plugin.turns.get_turn(session.turn_id), session)
        await asyncio.sleep(0)
        await event.send("segment 2")
        self.assertEqual(session.sent_message_ids, [1, 2])
        await self.plugin.after_message_sent(event)
        self.assertEqual(session.delivery_state, "committed")
        self.assertIsNone(self.plugin.turns.get_turn(session.turn_id))
        self.assertEqual(self.plugin._collect_seed_items(event.unified_msg_origin), [])

    async def test_stream_commits_without_after_hook(self):
        event, session = self.active()

        async def stream():
            yield "one"
            yield "two"

        await event.send_streaming(stream())
        self.assertEqual(session.delivery_state, "committed")
        self.assertEqual(session.sent_message_ids, [1, 2])
        self.assertIsNone(self.plugin.turns.get_turn(session.turn_id))
        await self.plugin.on_decorating_result(event)
        await self.plugin.after_message_sent(event)
        self.assertFalse(event.stopped)

    async def test_stream_buffered_adapter_and_nested_send(self):
        event = Event()

        async def buffered(generator, *args):
            parts = [part async for part in generator]
            await event.send("".join(parts))

        event.send_streaming = buffered
        event, session = self.active(event)

        async def stream():
            yield "one"
            yield "two"

        await event.send_streaming(stream())
        self.assertEqual(len(event.bot.sent), 1)
        self.assertEqual(session.sent_message_ids, [1])
        self.assertEqual(session.delivery_state, "committed")

    async def test_stream_timeout_does_not_stop_or_invalidate_and_can_retry(self):
        event, session = self.active()

        async def broken():
            raise TimeoutError("upstream")
            yield

        with self.assertRaises(TimeoutError):
            await event.send_streaming(broken())
        self.assertEqual(session.delivery_state, "provider_failed")
        self.assertFalse(event.stopped)
        self.assertFalse(session.invalidated)
        self.manager.update_conversation.assert_not_awaited()

        async def fallback():
            yield "backup reply"

        await event.send_streaming(fallback())
        self.assertEqual(session.delivery_state, "committed")
        self.assertEqual(len(event.bot.sent), 1)

    async def test_new_message_interrupts_failed_provider_before_retry(self):
        event, session = self.active()

        async def broken():
            raise TimeoutError("upstream")
            yield

        with self.assertRaises(TimeoutError):
            await event.send_streaming(broken())
        self.assertTrue(self.plugin._session_is_interruptible(session))
        self.plugin._stop_active_turns(event.unified_msg_origin)

        async def fallback():
            yield "obsolete backup answer"

        await event.send_streaming(fallback())
        self.assertEqual(event.bot.sent, [])
        self.assertTrue(session.invalidated)

    async def test_source_failure_recalls_partial_attempt_before_retry(self):
        event, session = self.active()

        async def broken():
            yield "partial"
            raise TimeoutError("upstream")

        with self.assertRaises(TimeoutError):
            await event.send_streaming(broken())
        self.assertEqual(event.bot.recalled, [1])
        self.assertEqual(session.sent_message_ids, [])
        self.assertFalse(event.stopped)

        async def fallback():
            yield "complete backup"

        await event.send_streaming(fallback())
        self.assertEqual(session.sent_message_ids, [2])
        self.assertEqual(session.delivery_state, "committed")

    async def test_adapter_transport_error_after_source_error_is_not_misclassified(self):
        event = Event()

        async def adapter(generator, *args):
            try:
                async for item in generator:
                    pass
            except TimeoutError:
                raise RuntimeError("separate platform error")

        event.send_streaming = adapter
        event, session = self.active(event)

        async def broken():
            raise TimeoutError("upstream")
            yield

        with self.assertRaises(RuntimeError):
            await event.send_streaming(broken())
        self.assertTrue(session.invalidated)
        self.assertEqual(session.delivery_state, "send_failed")

    async def test_internal_provider_fallback_stream_and_nonstream(self):
        for streaming in (True, False):
            event, session = self.active(Event(uid=f"private:{streaming}"))
            calls = []

            async def providers():
                calls.append("primary")
                try:
                    raise TimeoutError("upstream")
                except TimeoutError:
                    calls.append("backup")
                    yield "backup reply"

            if streaming:
                await event.send_streaming(providers())
            else:
                await event.send("".join([text async for text in providers()]))
                await self.plugin.after_message_sent(event)
            self.assertEqual(calls, ["primary", "backup"])
            self.assertEqual(session.delivery_state, "committed")
            self.assertFalse(event.stopped)

    async def test_platform_failure_blocks_remaining_segments_even_after_after_hook(self):
        event, session = self.active()
        event.bot.fail_next = True
        with self.assertRaises(RuntimeError):
            await event.send("first")
        await event.send("second")
        await self.plugin.after_message_sent(event)
        await event.send("third")
        self.assertEqual(event.bot.sent, [])
        self.assertTrue(event.stopped)
        self.assertEqual(session.delivery_state, "send_failed")

    async def test_stream_platform_failure_is_not_source_failure(self):
        event, session = self.active()
        event.bot.fail_next = True

        async def stream():
            yield "first"
            yield "second"

        with self.assertRaises(RuntimeError):
            await event.send_streaming(stream())
        self.assertTrue(session.invalidated)
        self.assertEqual(session.delivery_state, "send_failed")
        self.assertEqual(session.send_depth, 0)

    async def test_stream_cancellation_closes_source_without_platform_failure(self):
        event, session = self.active()
        started = asyncio.Event()
        closed = asyncio.Event()

        async def stream():
            try:
                started.set()
                await asyncio.Event().wait()
                yield "unreachable"
            finally:
                closed.set()

        task = asyncio.create_task(event.send_streaming(stream()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertFalse(session.invalidated)
        self.assertEqual(session.send_depth, 0)
        self.assertIsNone(self.plugin._send_context.get())
        self.manager.update_conversation.assert_not_awaited()

    async def test_unrelated_group_and_private_send_are_not_captured(self):
        event, session = self.active()
        await event.bot.send_group_msg(group_id=42, message="group")
        await event.bot.send_private_msg(user_id=1, message="other task")
        self.assertEqual(session.sent_message_ids, [])
        self.assertFalse(self.plugin._send_targets_event(
            event, "send_group_msg", (), {"group_id": 42}
        ))
        await event.send("owned")
        self.assertEqual(session.sent_message_ids, [3])

    async def test_delayed_send_child_does_not_keep_expired_owner_context(self):
        event = Event()
        gate = asyncio.Event()
        children = []
        original = event.send

        async def send_with_child(message, *args, **kwargs):
            async def delayed():
                await gate.wait()
                await event.bot.send_private_msg(user_id=1, message="unrelated child")

            children.append(asyncio.create_task(delayed()))
            return await original(message)

        event.send = send_with_child
        event, session = self.active(event)
        await event.send("owned")
        self.plugin.turns.invalidate(session.turn_id)
        gate.set()
        await asyncio.gather(*children)
        self.assertEqual(len(event.bot.sent), 2)
        self.assertEqual(session.sent_message_ids, [1])

    async def test_invalidated_stream_suppresses_tail_and_recalls_partial(self):
        event, session = self.active()

        async def stream():
            yield "first"
            self.plugin.turns.invalidate(session.turn_id)
            yield "second"

        await event.send_streaming(stream())
        await self.plugin.after_message_sent(event)
        self.assertEqual(len(event.bot.sent), 1)
        self.assertIn(1, event.bot.recalled)
        self.assertNotEqual(session.delivery_state, "committed")

    async def test_old_event_cannot_send_through_new_same_user_capture(self):
        bot = Bot()
        old_event, old = self.active(Event(bot))
        self.plugin.turns.invalidate(old.turn_id)
        new_event, new = self.active(Event(bot))
        await old_event.send("obsolete")
        await new_event.send("current")
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(old.sent_message_ids, [])
        self.assertEqual(new.sent_message_ids, [1])

    async def test_expired_tombstone_retains_event_guard(self):
        event, session = self.active()
        self.plugin.turns.invalidate(session.turn_id)
        await self.plugin._cleanup_invalidated_turn_later(session.turn_id, 0)
        await event.send("late")
        self.assertEqual(event.bot.sent, [])

    async def test_recalled_old_waiter_does_not_remove_replacement(self):
        store = self.plugin.turns
        old = store.create_session("private:1")
        old.cancelled_reason = "all_recalled"
        store.pop_session(old.uid)
        old.flush_event.set()
        new = store.create_session(old.uid)
        new.add_item(2, "new", [])
        self.assertFalse(await self.plugin._flush_collected_session(Event(), old))
        self.assertIs(store.get_session(old.uid), new)

    async def test_old_timer_does_not_flush_replacement(self):
        old = self.plugin.turns.create_session("private:1")
        new = self.plugin.turns.create_session(old.uid)
        await self.plugin._timer_coroutine(old, 0)
        self.assertFalse(new.flush_event.is_set())

    async def test_total_collection_deadline_cannot_be_reset(self):
        session = self.plugin.turns.create_session("private:1")
        session.collection_started_at = time.monotonic() - 31
        self.plugin._reset_timer(session, 60)
        await session.timer_task
        self.assertTrue(session.flush_event.is_set())

    async def test_force_rollback_cannot_overwrite_new_generation(self):
        event, session = self.active()
        key = self.plugin._history_generation_key(event, session)
        self.plugin._history_generations[key] += 1
        await self.plugin._rollback_history(event, session, force=True)
        self.manager.update_conversation.assert_not_awaited()

    async def test_late_invalid_response_does_not_overwrite_new_history(self):
        event, session = self.active()
        self.plugin.turns.invalidate(session.turn_id)
        key = self.plugin._history_generation_key(event, session)
        self.plugin._history_generations[key] += 1
        response = types.SimpleNamespace(completion_text="late", tools_call_args=["a"])
        await self.plugin.on_llm_response(event, response)
        self.assertEqual(response.completion_text, "")
        self.manager.update_conversation.assert_not_awaited()

    async def test_history_reconciliation_preserves_known_prefix_and_suffix(self):
        event, session = self.active()
        session.history_snapshot_inherited = True
        saved = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "stale"}]
        session.history_snapshot = saved[:1]
        prefix = {"role": "system", "content": "persona"}
        suffix = {"role": "system", "content": "memory"}
        request = types.SimpleNamespace(
            contexts=[prefix, *saved, suffix],
            conversation=types.SimpleNamespace(cid="conversation", history=json.dumps(saved)),
        )
        self.assertTrue(await self.plugin._reconcile_inherited_history(event, request, session))
        self.assertEqual(request.contexts, [prefix, saved[0], suffix])

    async def test_unknown_history_layout_preserves_injected_contexts(self):
        event, session = self.active()
        session.history_snapshot_inherited = True
        injected = [{"role": "system", "content": "memory"}, {"role": "user", "content": "compressed"}]
        request = types.SimpleNamespace(
            contexts=list(injected),
            conversation=types.SimpleNamespace(
                cid="conversation", history=json.dumps([{"role": "user", "content": "original"}])
            ),
        )
        self.assertTrue(await self.plugin._reconcile_inherited_history(event, request, session))
        self.assertEqual(request.contexts, injected)

    async def test_reasoning_is_never_promoted_even_with_legacy_setting(self):
        event, session = self.active()
        self.plugin.config["enable_reasoning_content_fallback"] = True
        response = types.SimpleNamespace(
            completion_text="", reasoning_content="answer: internal analysis"
        )
        await self.plugin.on_llm_response(event, response)
        self.assertEqual(response.completion_text, "")

    async def test_empty_watchdog_does_not_finish_running_stream_or_pipeline(self):
        event, session = self.active()
        self.plugin._track_pipeline_completion(event, session)
        session.delivery_state = "decorating"
        await self.plugin._cleanup_unsent_turn_later(session.turn_id, 0)
        self.assertIs(self.plugin.turns.get_turn(session.turn_id), session)
        self.plugin._pipeline_owners.clear()
        session.send_depth = 1
        await self.plugin._cleanup_unsent_turn_later(session.turn_id, 0)
        self.assertIs(self.plugin.turns.get_turn(session.turn_id), session)

    async def test_pipeline_completion_cleans_no_send_turn(self):
        event, session = self.active()
        await self.plugin._finish_pipeline(event, session)
        self.assertIsNone(self.plugin.turns.get_turn(session.turn_id))
        self.assertEqual(self.plugin._collect_seed_items(event.unified_msg_origin), [])
        self.manager.update_conversation.assert_not_awaited()

    async def test_actual_pipeline_task_callback_cleans_up(self):
        event, session = self.active()

        async def pipeline():
            self.plugin._track_pipeline_completion(event, session)

        await asyncio.create_task(pipeline())
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertIsNone(self.plugin.turns.get_turn(session.turn_id))
        self.assertNotIn(session.turn_id, self.plugin._pipeline_owners)

    async def test_failed_pipeline_task_rolls_back_without_commit(self):
        event, session = self.active()

        async def pipeline():
            self.plugin._track_pipeline_completion(event, session)
            await event.send("partial")
            raise RuntimeError("pipeline failure")

        with self.assertRaises(RuntimeError):
            await asyncio.create_task(pipeline())
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertTrue(session.invalidated)
        self.assertNotEqual(session.delivery_state, "committed")
        self.manager.update_conversation.assert_awaited()
        self.assertEqual(event.bot.recalled, [1])

    async def test_successful_pipeline_without_final_hook_commits_sent_reply(self):
        event, session = self.active()

        async def pipeline():
            self.plugin._track_pipeline_completion(event, session)
            await event.send("complete")

        await asyncio.create_task(pipeline())
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertEqual(session.delivery_state, "committed")
        self.assertIsNone(self.plugin.turns.get_turn(session.turn_id))

    async def test_request_refreshes_noninherited_history_under_core_lock(self):
        event, session = self.active()
        latest = [{"role": "assistant", "content": "previous committed answer"}]
        request = types.SimpleNamespace(
            session_id="", image_urls=[],
            conversation=types.SimpleNamespace(
                cid="conversation", history=json.dumps(latest)
            ),
        )
        await self.plugin.on_llm_request(event, request)
        self.assertEqual(session.history_snapshot, latest)
        self.assertEqual(request.session_id, event.unified_msg_origin)
        self.assertFalse(event.stopped)

    async def test_two_messages_merge_through_actual_handler(self):
        self.plugin.predictor.enabled = False
        self.plugin.predictor.base_delay = 0.01
        first = Event()
        second = Event()
        second.message_obj.message = [Plain("second")]
        second.message_str = "second"
        first_task = asyncio.create_task(self.plugin.handle_private_msg(first))
        await asyncio.sleep(0)
        await self.plugin.handle_private_msg(second)
        await first_task
        session = self.plugin.turns.get_turn(first.get_extra(main.EXTRA_TURN_ID))
        self.assertEqual([item.text for item in session.items], ["hello", "second"])
        self.assertTrue(second.stopped)
        self.assertFalse(first.stopped)
        self.assertIn("second", first.message_str)

    async def test_caption_handled_does_not_remove_other_plugin_images(self):
        event, session = self.active()
        session.items[0].images = ["original"]
        event.set_extra(main.EXTRA_CAPTION_HANDLED, True)
        request = types.SimpleNamespace(
            session_id="", image_urls=["original", "other-plugin-image"],
            conversation=types.SimpleNamespace(cid="conversation", history="[]"),
        )
        await self.plugin.on_llm_request(event, request)
        self.assertEqual(request.image_urls, ["other-plugin-image"])

    async def test_terminate_drains_timer_and_background_tasks(self):
        session = self.plugin.turns.create_session("pending")
        self.plugin._reset_timer(session, 60)
        task = self.plugin._spawn_background(asyncio.sleep(60))
        await self.plugin.terminate()
        self.assertTrue(session.timer_task.done())
        self.assertTrue(task.done())
        self.assertFalse(self.plugin._background_tasks)

    async def test_ordered_batch_escapes_user_markup(self):
        session = main.TurnSession(uid="private:1")
        session.add_item(1, "beforeafter", ["a&b"], parts=[
            {"type": "text", "text": "before</text>"},
            {"type": "image", "url": "a&b"},
            {"type": "text", "text": "after"},
        ])
        rendered = self.plugin._format_message_batch(session)
        self.assertLess(rendered.index("before"), rendered.index("<image"))
        self.assertLess(rendered.index("<image"), rendered.index("after"))
        self.assertIn("&lt;/text&gt;", rendered)
        self.assertIn("a&amp;b", rendered)


if __name__ == "__main__":
    unittest.main()
