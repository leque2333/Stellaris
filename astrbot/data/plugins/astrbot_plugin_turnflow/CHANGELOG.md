# Changelog / 更新日志

## 1.0.0 - 2026-09-20

### 中文

TurnFlow 首个独立发行版本，插件标识为 `astrbot_plugin_turnflow`。
版本从 1.0.0 重新计数；基于此前本地二开版本 2.9.0 整理，并非旧插件的降级安装包。

- 私聊动态消息收集，默认基础等待 2 秒、整轮收集上限 30 秒。
- 未完成轮次中断、部分输出尽力撤回，以及带轮次代际保护的历史回滚。
- 保留图文顺序、配置模型的图片转述、QQ 引用与合并转发读取、本地卡片 URL 提取。
- 包含流式、非流式及分段发送保护；主模型和备用模型路由仍由 AstrBot 负责。
- 不包含联网链接增强、网页抓取、本地聊天样本学习或额外 pip 依赖。
- 提供中英文文档、来源说明、离线回归测试与安装包构建工具；不包含截图或图标素材。

迁移前停用旧插件，重新配置新标识并重启 AstrBot，不要同时启用两份。
真实服务器上的模型超时回退与第三方发送插件组合仍需验收。

### English

The first independently distributed TurnFlow release uses the plugin ID
`astrbot_plugin_turnflow`. Versioning starts at 1.0.0, based on the previous
locally maintained 2.9.0 derivative; it is not a downgrade package.

- Dynamic collection for private chats, with a 2-second base delay and a
  30-second total collection limit by default.
- Interruption of unfinished turns, best-effort partial-output recall, and
  generation-guarded history rollback.
- Ordered text/images, configured-model image captions, QQ quotes and merged
  forwards, and local card URL extraction.
- Guards for streaming, non-streaming, and segmented delivery; AstrBot remains
  responsible for primary and fallback model routing.
- No web enrichment, website scraping, local chat-sample learning, or additional
  pip dependencies.
- Chinese and English documentation, source attribution, offline regression
  tests, and an archive builder, without bundled screenshots or logos.

Disable the previous plugin, configure the new ID, and restart AstrBot before
use. Do not enable both copies. Real-server timeout fallback and third-party
sending-plugin combinations still require acceptance testing.
