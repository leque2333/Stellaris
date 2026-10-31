"""AstrBot 适配器：AstrBot 的配置/插件扫描，不属于通用连接核心。"""

from .component_scanner import ComponentScanner, default_plugin_roots
from .host_resolver import HostResolver

__all__ = ["HostResolver", "ComponentScanner", "default_plugin_roots"]
