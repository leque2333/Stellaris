# -*- coding: utf-8 -*-
"""
Stellaris Core - 数据模型与契约规范
定义时间轴生命流事件 (TimelineEvent)、用户画像 (UserProfile) 及多维召回结果
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


def get_cst_now_str(ts: Optional[float] = None) -> str:
    """格式化当前东八区时间字符串"""
    t = ts if ts is not None else time.time()
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class TimelineEvent:
    """
    智能体连续自传式时间轴事件节点 (Episodic Timeline Node)
    记录任意模块（B站、QQ、Telegram、PC反重力控制等）产生的生命经历切片
    """
    event_id: str                          # 唯一事件标识 (如 evt_1730000000000_a1b2)
    timestamp: float                       # 精确时间戳 (POSIX 时间)
    source_module: str                     # 来源模块: 'bilibili_patrol' | 'qq_chat' | 'pc_bridge' | 'telegram' | 'reflection'
    actor_id: str                          # 实体归一化身份: 'master' | 'self' | 'user_xxxx'
    actor_name: str                        # 实体展示名称 (如 '主人' / '用户')
    narrative: str                         # 当前角色第一人称视角的经历描述 (如 "我在B站看到了视频...")
    time_str: str = ""                     # 可读时间串 (如 "2026-10-26 16:30:00")
    emotional_impact: int = 0              # 情绪/好感度影响 (-3 到 +3)
    importance: float = 1.0                # 重要度系数 (1.0 到 5.0)
    prev_event_id: Optional[str] = None    # 时序前驱事件ID (构建拟人因果前后链)
    causal_relation: Optional[str] = None  # 因果关系描述
    metadata: Dict[str, Any] = field(default_factory=dict)  # 原始特异元数据 (bvid, exit_code, cmd 等)
    embedding: Optional[bytes] = None      # 768 维 Float32 向量字节流
    detail_id: str = ""                    # 关联 L3 memory_details（完整一轮原文）
    person_id: str = ""                    # 统一人物 ID（绑定档案时=archive_id）
    # ---- 插件/时间线聚合标签 (v2 schema, 可选, 老数据为 None) ----
    plugin_id: Optional[str] = None        # 产生该事件的插件标识 (如 qq / bilibili / host_control)
    tag_primary: Optional[str] = None      # 一级标签: 插件展示名 (QQ / B站 / 宿主机)
    tag_secondary: Optional[str] = None    # 二级标签: 由插件性质决定 (群聊名 / UP主 / 任务名)
    segment_id: Optional[str] = None       # 所属会话段 (复杂事件聚合)
    started_at: Optional[float] = None     # 会话段起始时间 (复杂事件)
    ended_at: Optional[float] = None       # 会话段结束时间 (复杂事件)
    visibility: str = "private"            # private / master / public

    def __post_init__(self):
        if not self.time_str:
            self.time_str = get_cst_now_str(self.timestamp)

    def to_dict(self, include_embedding: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        if not include_embedding:
            d.pop("embedding", None)
        return d


@dataclass
class UserProfile:
    """
    用户长效画像与好感度状态
    """
    user_id: str                           # 实体标识 (如 'master' 或 '<你的QQ>')
    nickname: str = ""                     # 昵称
    role_type: str = "member"              # 角色: 'admin' | 'member'
    facts: List[str] = field(default_factory=list)  # 核心事实事实库
    affection: int = 50                    # 好感度 (0 - 100)
    affection_history: List[Dict[str, Any]] = field(default_factory=list) # 好感度波动流水
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecallResult:
    """
    时间轴记忆三维拟人召回结果
    综合评分 = α·相似度 + β·时间衰减近因分 + γ·重要度权重
    """
    event: TimelineEvent
    score: float                           # 综合评分 (0.0 ~ 1.0)
    similarity: float                      # 语义向量相似度 (余弦相似度)
    recency_score: float                   # 近因效应衰减分 (0.0 ~ 1.0)
    importance_score: float                # 重要度折算分 (0.0 ~ 1.0)


