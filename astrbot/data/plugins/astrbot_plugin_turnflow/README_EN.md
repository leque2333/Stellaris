# TurnFlow

English | [简体中文](README.md)

**Message debouncing + dynamic reply recall for AstrBot private chats.**

When you send consecutive messages, TurnFlow waits briefly and combines them before requesting a reply. If you add or correct something before the bot finishes replying, it blocks stale output, attempts to recall already delivered portions where supported, and processes the updated input.

**Private chats only. A fully delivered reply is not automatically recalled when the next message arrives.**

## Message Debouncing: Combine Consecutive Input

People often split a thought across several messages. TurnFlow briefly waits after receiving a message and collects subsequent text and images, instead of triggering a separate answer for every fragment.

For example, send these messages within the collection window:

```text
You: Help me write a leave-request email
You: I need tomorrow off
You: Keep it brief
```

The model receives these as one input, rather than answering three incomplete requests separately. Relative text and image order is preserved. Platforms with typing notifications can also let collection wait while you are typing.

The default base delay is **2 seconds**. Dynamic debouncing adjusts it using built-in continuation rules; it is not a fixed 2-second wait for every message. Total collection is capped at **30 seconds** by default from the first message, excluding captioning and model generation. Messages starting with configured command prefixes bypass merging.

## Dynamic Recall: Stop Replies That Are No Longer Relevant

Debouncing waits before an answer starts. Dynamic recall handles an answer that has already started when the user adds new information. It goes beyond removing recalled user messages from the pending input: it also handles the bot's unfinished reply.

For example, while the bot is drafting an email requesting tomorrow off, you add "Make that the day after tomorrow." As long as the old reply has not finished, the plugin will:

1. Block further output from the old reply and process the original input together with your correction.
2. Attempt platform recall for captured portions already delivered. Nothing needs recalling if no output has been sent.
3. Attempt to roll back history associated with the invalidated reply so stale results do not influence later answers. Rollback guards protect newer conversation history.

### Which Messages Are Affected

| Situation | What TurnFlow does |
| --- | --- |
| Send more messages during collection | Continues collecting and requests a reply after collection ends. |
| Add a message during generation or unfinished delivery | Interrupts stale output and processes the original input with the addition. |
| An unfinished reply has already delivered some content | Attempts to recall captured portions if the platform and permissions allow it; the same rule applies to streaming and segmented output. |
| Recall a message while it is being collected | Removes it from the pending input; no empty request is sent if everything was recalled. |
| Send a message after the previous reply has finished | Handles the new message without recalling the completed reply. |

**Dynamic recall does not edit completed replies or clean up chat logs.** If recall is unsupported or fails, delivered portions may remain visible, but the plugin still blocks further stale output. Disabling `enable_humanoid_recall` disables recall operations, not reply interruption.

Interruption primarily prevents stale results from reaching the conversation. It does not guarantee that the model service stops computing or billing, and cannot undo what someone has already read. See "Output and Platform Boundaries" below for history-protection and third-party compatibility limits.

## Installation

A working AstrBot installation with a message platform and chat model configured is required. The plugin declares no additional pip dependencies.

Install from this repository address in AstrBot's plugin management page:

```text
https://github.com/Yuimi-chaya/astrbot_plugin_turnflow
```

Alternatively, place the source in `data/plugins/astrbot_plugin_turnflow/`, with `main.py` and `metadata.yaml` directly inside that directory, then restart or reload the plugin. Do not enable another debounce or reply-interruption plugin at the same time.

### Migrating From the Previous Plugin

TurnFlow has a new plugin ID, `astrbot_plugin_turnflow`. Its independent version sequence starts at `1.0.0`; this is not a downgrade of the previous plugin.

1. Back up the old plugin configuration and let active conversations finish.
2. Disable `astrbot_plugin_continuous_message` and any derivative copies.
3. Install TurnFlow and reapply the settings you need in the management panel. Configuration does not automatically migrate to the new ID.
4. Restart AstrBot to avoid leftover send wrappers from the previous instance, then run the acceptance checks below in a fresh private chat.

There is no need to delete chat history, cookies, sample files, or shared Python packages. Legacy web-enrichment and local-sample-learning settings have no effect in this plugin.

## Configuration

Edit plugin settings in the AstrBot management panel. See [_conf_schema.json](_conf_schema.json) for all fields and defaults.

| Setting | Default | Purpose |
| --- | --- | --- |
| `enable` | `true` | Enables private-chat message debouncing and dynamic recall handling. |
| `debounce_time` | `2.0` seconds | Base collection delay. |
| `enable_dynamic_debounce` | `true` | Uses built-in continuation rules, not a model predictor or learned chat samples. |
| `min_debounce_time` / `max_debounce_time` | `1.2` / `8.0` seconds | Bounds the dynamic delay. |
| `max_collection_wait` | `30.0` seconds | Collection limit measured from the first message; excludes captioning and generation. The effective limit is never below the base delay. |
| `enable_typing_detection` | `true` | Uses platform typing notifications, subject to the total collection limit. |
| `enable_recall_filter` | `true` | Handles user recall notifications. |
| `enable_humanoid_recall` | `true` | Attempts to recall partial output from unfinished replies. Disabling this does not disable turn interruption. |
| `enable_ordered_image_caption` | `true` | Inserts captions at image positions when the chat model is not natively multimodal and a caption model is configured. |
| `caption_timeout` | `20.0` seconds | Per-image caption timeout, separate from the chat model timeout. |
| `command_prefixes` | `["/"]` | Messages beginning with these prefixes bypass merging. |
| `invalidated_turn_retention_seconds` | `180.0` seconds | Keeps invalidated-turn guards for late results; size this for possible late arrivals. |

Start with the defaults. Increase the base delay if follow-up messages are consistently missed; check typing detection and dynamic delay settings if collection feels too slow.

`merge_separator`, `forward_prefix`, `recall_grace_seconds`, and `enable_reasoning_content_fallback` are hidden legacy compatibility fields and no longer affect behavior.

## Output and Platform Boundaries

- The implementation handles non-streaming, segmented non-streaming, streaming, and buffered streaming output. Actual behavior depends on AstrBot, the adapter, and other sending plugins; validate your deployed combination.
- AstrBot owns the primary model, fallback order, streaming, and segmentation settings. Upstream stream errors propagate to the core instead of being immediately treated as platform delivery failures. TurnFlow does not implement its own fallback-model router.
- QQ quotes, merged forwards, and recall depend on the relevant aiocqhttp / OneBot v11 capabilities. Typing detection requires notifications from adapters such as NapCat. These features are not guaranteed on other platforms.
- Ordered captioning uses AstrBot's configured image-caption model. Its default caption prompt is in Chinese; a non-default custom prompt is preserved. Chat fallback models are not substituted for the caption model.
- Plain links remain text, and QQ card URLs are extracted locally. There is no target-page scraping or local chat-history learning. Platform message retrieval and image-model calls may still use the network.
- No visible answer is inferred or extracted from `reasoning_content`.
- History rollback is guarded by turn generations. Unrecognized context transformations from other plugins are preserved, so cleanup cannot be guaranteed for every third-party context format.
- Independent bot sends outside the current event's send-call context are not captured or automatically recalled.

## Acceptance Checks

Repeat these checks in the streaming, non-streaming, and segmented modes you actually use:

1. Send two consecutive messages and check that they produce one reply turn.
2. Recall a pending message and immediately resend it; check that the old timer does not consume the new input.
3. Add a message during generation or after partial delivery; check that stale output stops and, where supported, partial output is recalled.
4. Send a message after a complete reply and check that the previous reply stays visible.
5. Trigger a primary-model timeout in a test environment. Check that AstrBot calls the configured fallback model without misclassifying the timeout as a platform send failure.

Automated tests use AstrBot API stubs, not live platform end-to-end testing. The first release has not completed real-server fallback-model acceptance or verification of every third-party sending-plugin combination.

When reporting a problem, include AstrBot, adapter and plugin versions, delivery mode, minimal reproduction steps, and sanitized logs. Do not submit API keys, account credentials, or complete private-chat records.

## Development

Run the offline regression suite from the repository root with Python 3.11:

```console
python -B -m unittest discover -s tests -v
```

Build an installable archive:

```console
python -B tools/build_release.py --version 1.0.0
```

The output is `dist/astrbot_plugin_turnflow-1.0.0.zip`. The builder checks source files, archive paths, and regression tests against extracted code, and refuses to overwrite an existing artifact. Install archives exclude tests, build tools, caches, and private development notes; the public repository retains the complete tests and tools.

## License

Licensed under [GNU AGPLv3](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance, original-author attribution, and modification notices. This project is independently maintained and does not represent the upstream project.
