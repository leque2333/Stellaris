# Source and Attribution / 来源与署名

## TurnFlow

TurnFlow is an independently maintained derivative of
[aliveriver/astrbot_plugin_continuous_message](https://github.com/aliveriver/astrbot_plugin_continuous_message).
The original plugin author is **aliveriver**. Original authors retain their
rights in their respective contributions. TurnFlow maintenance and subsequent
modifications are by **Yuimi-chaya**.

TurnFlow 是上述项目的独立维护衍生版本，原插件作者为 **aliveriver**。
原作者保留其相应贡献的权利；TurnFlow 的维护和后续修改由 **Yuimi-chaya** 负责。
独立仓库及插件名称不表示原始代码由本项目从零编写，也不表示上游认可或维护本版本。

## License / 许可证

This distribution retains the upstream GNU Affero General Public License,
version 3, in [LICENSE](LICENSE). Its inherited README previously contained an
inconsistent MIT label; TurnFlow does not rely on that label as permission to
relicense the code. No noncommercial-only restriction is added.

本发行版保留上游 [LICENSE](LICENSE) 中的 GNU AGPL 第 3 版许可证原文。
继承的旧 README 曾有与 LICENSE 不一致的 MIT 标注，本项目不以该标注作为重新许可的依据，
也不附加“仅限非商用”限制。

## Modification Notice / 修改说明

**2026-09-20, TurnFlow 1.0.0.** This distribution includes changes to the original
work. The starting point is a locally maintained derivative whose previous
version was 2.9.0; that number is not presented as an upstream release.

Changes from the original plugin include turn invalidation, partial-output
recall, generation-guarded history rollback, ordered text/image handling,
streaming and segmented-send guards, and separation of provider errors from
platform delivery errors. Online link enrichment, bundled website scrapers,
and local chat-sample learning have been removed.

The independent release changes plugin identity and version numbering,
replaces user documentation, removes bundled screenshots and logos, and
publishes regression tests and a release builder. Internal compatibility
identifiers and some diagnostic labels still use the previous plugin name.

**2026-09-20，TurnFlow 1.0.0。** 本发行版包含对原作品的修改，起点为此前版本号
为 2.9.0 的本地二开版本，该编号不代表上游发行版本。相较原插件，修改涉及轮次失效、
部分回复撤回、带代际保护的历史回滚、图文顺序、流式与分段发送保护，以及模型异常与
平台发送异常的区分。联网增强、网页抓取和本地聊天样本学习已移除。
独立发行调整了插件标识与版本编号，重写文档，移除截图及图标，并公开测试与构建工具。

## Historical Reference / 历史参考

The inherited documentation credited
[Zhalslar/astrbot_plugin_parser](https://github.com/Zhalslar/astrbot_plugin_parser)
as a reference for earlier link-parsing work. That historical acknowledgement is
retained here. The bundled web-scraping modules are no longer distributed;
local QQ card URL extraction remains.

旧文档曾将上述项目列为链接解析工作的参考来源，此处保留该历史致谢。
当前发行版不再包含网页抓取模块，仅保留本地 QQ 卡片原链接提取。
