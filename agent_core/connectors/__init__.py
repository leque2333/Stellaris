# -*- coding: utf-8 -*-
"""连接器：只读扫描外部模块，自动登记平台上下文"""

from .base import Connector, ConnectorReport, DiscoveredContext
from .discovery import DiscoveryService, run_forever
from .matcher import match_contexts, normalize_terms
from .spec_connector import SpecConnector, spec_data_dirs

__all__ = [
    "Connector",
    "ConnectorReport",
    "DiscoveredContext",
    "DiscoveryService",
    "SpecConnector",
    "match_contexts",
    "normalize_terms",
    "run_forever",
    "spec_data_dirs",
]
