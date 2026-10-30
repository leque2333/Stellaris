# -*- coding: utf-8 -*-
"""B 站刷视频 / 评论 / 回复主逻辑。只通过中枢 /v1 生成内容。"""

from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .bili_api import (
    BiliClient,
    BiliError,
    filter_videos,
    legacy_filter,
    merge_video_detail,
    normalize_video,
    reply_view,
)
from .hub_client import HubClient, HubError
from .state import StateStore


def _now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Patrol:
    def __init__(
        self,
        config: Dict[str, Any],
        bili: BiliClient,
        hub: HubClient,
        state: StateStore,
    ) -> None:
        self.config = config
        self.bili = bili
        self.hub = hub
        self.state = state
        self.bcfg: Dict[str, Any] = config.get("bilibili") or {}

    def log(self, message: str) -> None:
        print(f"[{_now_text()}] {message}", flush=True)

    # ---- 时段 / 配额 ----
    def active_session(self, now: Optional[datetime] = None) -> Tuple[str, bool]:
        now = now or datetime.now()
        windows = self.bcfg.get("active_hours") or []
        if not windows:
            return f"{now.strftime('%Y-%m-%d')}:all", True
        hour = now.hour + now.minute / 60.0
        for window in windows:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                continue
            start, end = float(window[0]), float(window[1])
            inside = (start <= hour < end) if start <= end else (hour >= start or hour < end)
            if inside:
                return f"{now.strftime('%Y-%m-%d')}:{window[0]}-{window[1]}", True
        return "", False

    def quota(self, session_key: str) -> Tuple[bool, str]:
        return self.state.can_comment(
            session_key,
            daily_limit=int(self.bcfg.get("daily_comment_limit", 5)),
            session_limit=int(self.bcfg.get("session_comment_limit", 2)),
            min_interval_seconds=int(self.bcfg.get("min_comment_interval_seconds", 600)),
        )

    # ---- 候选视频 ----
    def collect_candidates(self) -> List[Dict[str, Any]]:
        mode = str(self.bcfg.get("filter_mode") or "legacy").lower()
        if mode == "legacy":
            rows = self._legacy_candidates()
            if rows or not self.bcfg.get("fallback_generic", False):
                return rows
        return self._generic_candidates()

    def _legacy_candidates(self) -> List[Dict[str, Any]]:
        """旧版三层筛选：大肥鱼 + 动画/二次元 + AI 词可选。"""
        keywords = [str(k) for k in (self.bcfg.get("keywords") or []) if str(k).strip()]
        if not keywords:
            keywords = ["大肥鱼 动画", "大肥鱼 二次元", "大肥鱼 AI", "大肥鱼"]
        if not self.bcfg.get("search_all_keywords", True):
            keywords = [keywords[int(time.time()) % len(keywords)]]
        limit = max(1, int(self.bcfg.get("max_search_keywords", 4)))
        must = self.bcfg.get("must_keywords") or ["大肥鱼"]
        anime = self.bcfg.get("anime_keywords") or ["动画", "二次元"]
        extra = self.bcfg.get("extra_keywords") or ["gemini", "claude", "gpt", "ai", "deepseek"]
        blocked = [str(k).lower() for k in (self.bcfg.get("blocked_keywords") or [])]
        seen = set(self.state.seen_bvids())
        own_mid = int(self.bcfg.get("bot_uid") or 0)
        min_duration = int(self.bcfg.get("min_duration_seconds", 0))
        max_duration = int(self.bcfg.get("max_duration_seconds", 0))
        rows: List[Dict[str, Any]] = []

        for keyword in keywords[:limit]:
            try:
                items = self.bili.search_videos(keyword, order="pubdate", page=1)
            except BiliError as exc:
                self.log(f"搜索「{keyword}」失败: {exc}")
                continue
            for item in items:
                video = normalize_video(item)
                bvid = video.get("bvid") or ""
                if not bvid or bvid in seen:
                    continue
                if own_mid and int(video.get("mid") or 0) == own_mid:
                    continue
                duration = int(video.get("duration") or 0)
                if min_duration and duration and duration < min_duration:
                    continue
                if max_duration and duration and duration > max_duration:
                    continue
                if not video.get("tags"):
                    video["tags"] = self.bili.tags(bvid)
                corpus = " ".join(
                    [str(video.get("title") or ""), str(video.get("desc") or ""),
                     " ".join(str(t) for t in (video.get("tags") or []))]
                ).lower()
                if any(word in corpus for word in blocked):
                    continue
                matched = legacy_filter(
                    video,
                    must_keywords=must,
                    anime_keywords=anime,
                    extra_keywords=extra,
                )
                if not matched:
                    continue
                rows.append(matched)
                if len(rows) >= 6:
                    return rows
        return rows

    def _generic_candidates(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        pages = max(1, int(self.bcfg.get("scan_pages", 1)))
        for page in range(1, pages + 1):
            try:
                rows.extend(normalize_video(item) for item in self.bili.popular(page=page))
            except BiliError as exc:
                self.log(f"热门读取失败: {exc}")
        try:
            if self.bcfg.get("enable_ranking", False):
                rows.extend(normalize_video(item) for item in self.bili.ranking())
        except BiliError as exc:
            self.log(f"排行读取失败: {exc}")
        if self.bcfg.get("enable_search", True):
            for keyword in list(self.bcfg.get("keywords") or [])[:2]:
                try:
                    rows.extend(
                        normalize_video(item)
                        for item in self.bili.search_videos(str(keyword), order="pubdate")
                    )
                except BiliError as exc:
                    self.log(f"搜索「{keyword}」失败: {exc}")
        for rid in self.bcfg.get("rids") or []:
            try:
                rows.extend(normalize_video(item) for item in self.bili.newlist(int(rid)))
            except (BiliError, ValueError) as exc:
                self.log(f"分区 {rid} 读取失败: {exc}")

        unique: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            if row.get("bvid") and row["bvid"] not in unique:
                unique[row["bvid"]] = row
        filtered = filter_videos(
            unique.values(),
            seen=self.state.seen_bvids(),
            own_mid=int(self.bcfg.get("bot_uid") or 0),
            keywords=self.bcfg.get("keywords") or [],
            blocked=self.bcfg.get("blocked_keywords") or [],
            min_hits=int(self.bcfg.get("min_keyword_hits", 1)),
            min_duration=int(self.bcfg.get("min_duration_seconds", 0)),
            max_duration=int(self.bcfg.get("max_duration_seconds", 0)),
        )
        random.shuffle(filtered)
        return filtered[:12]

    # ---- 评估 + 写评论 ----
    def evaluate(self, video: Dict[str, Any]) -> Dict[str, Any]:
        max_len = int(self.bcfg.get("comment_max_len", 100))
        tags = ", ".join(video.get("tags") or []) or "无"
        prompt = (
            "你正在逛 B 站，看到一个视频。请判断它是否值得你留一条评论，"
            "如果值得，就直接写出这条评论。\n\n"
            f"标题：{video.get('title') or ''}\n"
            f"UP主：{video.get('author') or ''}\n"
            f"时长：{video.get('duration') or 0} 秒\n"
            f"播放：{video.get('view') or 0}\n"
            f"标签：{tags}\n"
            f"简介：{(video.get('desc') or '')[:600]}\n\n"
            "只输出 JSON，不要 markdown：\n"
            '{"score": 0.0, "reason": "不超过20字", "comment": "评论正文"}\n'
            f"score 取 0~1；comment 不超过 {max_len} 字，要像真人随手评论，"
            "有具体细节，不写总结、不营销、不提 AI、不堆表情；"
            "不想评论时 comment 留空字符串。"
        )
        messages = [
            {"role": "system", "content": "你是内容筛选器，只输出严格 JSON。"},
            {"role": "user", "content": prompt},
        ]
        user_id = str(self.bcfg.get("master_uid") or self.bcfg.get("bot_uid") or "bilibili_patrol")
        try:
            result = self.hub.chat_json(
                messages,
                user_id=user_id,
                user_name=str(self.bcfg.get("master_name") or "B站巡检"),
                channel="bilibili:patrol",
                temperature=0.8,
                max_tokens=800,
            )
        except HubError as exc:
            self.log(f"中枢评估失败: {exc}")
            return {"score": 0.0, "comment": "", "reason": str(exc)}
        comment = str(result.get("comment") or "").strip()
        comment = re.sub(r"\s+", " ", comment).strip().strip('"“”')
        if comment.upper() == "SKIP":
            comment = ""
        if len(comment) > max_len:
            comment = comment[:max_len].rstrip()
        try:
            score = float(result.get("score") or 0)
        except (TypeError, ValueError):
            score = 1.0 if comment else 0.0
        return {
            "score": max(0.0, min(1.0, score)),
            "comment": comment,
            "reason": str(result.get("reason") or ""),
        }

    def patrol_once(self, dry_run: bool = False) -> Dict[str, Any]:
        session_key, active = self.active_session()
        if not active:
            return {"ok": False, "reason": "当前不在活跃时段"}
        allowed, reason = self.quota(session_key)
        if not allowed:
            return {"ok": False, "reason": reason}
        if not self.bcfg.get("auto_comment", True):
            return {"ok": False, "reason": "auto_comment 已关闭"}

        candidates = self.collect_candidates()
        if not candidates:
            return {"ok": False, "reason": "没有新的候选视频"}
        self.log(f"候选 {len(candidates)} 个，开始评估")

        for candidate in candidates[:5]:
            bvid = candidate.get("bvid") or ""
            if not bvid:
                continue
            try:
                detail = self.bili.view(bvid)
                video = merge_video_detail(candidate, detail)
                if not video.get("tags"):
                    video["tags"] = self.bili.tags(bvid)
            except BiliError as exc:
                self.log(f"{bvid} 详情读取失败: {exc}")
                self.state.mark_seen(bvid)
                continue

            self.log(f"评估《{video.get('title') or bvid}》 / {video.get('author') or ''}")
            result = self.evaluate(video)
            self.log(
                f"score={result['score']:.2f} reason={result['reason'][:60]} "
                f"comment={result['comment'][:40]}"
            )
            if result["score"] < float(self.bcfg.get("min_score", 0.55)) or not result["comment"]:
                self.state.mark_seen(bvid)
                continue
            if dry_run:
                self.log(f"[dry-run] 会评论 {bvid}: {result['comment']}")
                return {"ok": True, "dry_run": True, "video": video, "comment": result["comment"]}

            try:
                posted = self.bili.add_comment(int(video["aid"]), result["comment"])
            except BiliError as exc:
                self.log(f"发表评论失败: {exc}")
                self.state.mark_seen(bvid)
                break
            rpid = int(posted.get("rpid") or posted.get("rpid_str") or 0)
            self.state.mark_seen(bvid)
            self.state.record_comment(session_key)
            if rpid:
                self.state.add_tracked({
                    "bvid": bvid,
                    "aid": int(video["aid"]),
                    "rpid": rpid,
                    "title": video.get("title") or "",
                    "author": video.get("author") or "",
                    "comment": result["comment"],
                    "created_at": time.time(),
                })
            self.log(f"已评论 {bvid} rpid={rpid}: {result['comment']}")
            return {"ok": True, "video": video, "comment": result["comment"], "rpid": rpid}
        return {"ok": False, "reason": "本轮没有值得评论的视频"}

    # ---- 回复 ----
    def generate_reply(self, tracked: Dict[str, Any], reply: Dict[str, Any]) -> str:
        max_len = int(self.bcfg.get("max_reply_len", 80))
        prompt = (
            "有人在 B 站回复了你的评论。按你的人格，写一条自然的回复。\n\n"
            f"视频：《{tracked.get('title') or ''}》\n"
            f"你的原评论：{tracked.get('comment') or ''}\n"
            f"对方昵称：{reply.get('uname') or ''}\n"
            f"对方回复：{reply.get('message') or ''}\n\n"
            f"只输出回复正文，不超过 {max_len} 字；不写分析、不堆表情、不提 AI；"
            "不想回就输出 SKIP。"
        )
        messages = [
            {"role": "system", "content": "你正在 B 站评论区回复别人，只输出回复本身。"},
            {"role": "user", "content": prompt},
        ]
        text = self.hub.chat(
            messages,
            user_id=str(reply.get("mid") or ""),
            user_name=str(reply.get("uname") or ""),
            channel=f"bilibili:video:{tracked.get('bvid') or ''}",
            temperature=0.85,
            max_tokens=200,
        )
        text = re.sub(r"\s+", " ", text).strip().strip('"“”')
        if text.upper() == "SKIP":
            return ""
        if len(text) > max_len:
            text = text[:max_len].rstrip()
        return text

    def check_replies(self, dry_run: bool = False) -> Dict[str, Any]:
        if not self.bcfg.get("auto_reply", True):
            return {"ok": False, "reason": "auto_reply 已关闭"}
        if not self.bili.logged_in:
            return {"ok": False, "reason": "未配置 B 站登录凭据"}
        tracked_rows = self.state.tracked_comments()
        if not tracked_rows:
            return {"ok": False, "reason": "还没有追踪中的评论"}
        processed = set(self.state.processed_replies())
        bot_uid = int(self.bcfg.get("bot_uid") or 0)
        master_uid = int(self.bcfg.get("master_uid") or 0)
        max_age = int(self.bcfg.get("reply_track_days", 7)) * 86400
        now = time.time()
        handled = 0
        for tracked in tracked_rows[-20:]:
            if now - float(tracked.get("created_at") or 0) > max_age:
                continue
            aid = int(tracked.get("aid") or 0)
            root = int(tracked.get("rpid") or 0)
            if not aid or not root:
                continue
            try:
                replies = self.bili.sub_replies(aid, root)
            except BiliError as exc:
                self.log(f"读取回复失败 {tracked.get('bvid')}: {exc}")
                continue
            for item in replies:
                reply = reply_view(item)
                rpid = reply["rpid"]
                if not rpid or rpid in processed:
                    continue
                if bot_uid and reply["mid"] == bot_uid:
                    continue
                if master_uid and reply["mid"] == master_uid and not self.bcfg.get("reply_to_master", True):
                    continue
                if not reply["message"]:
                    continue
                text = self.generate_reply(tracked, reply)
                if not text:
                    self.state.mark_reply_processed(rpid)
                    continue
                if dry_run:
                    self.log(f"[dry-run] 会回复 {reply['uname']}: {text}")
                    handled += 1
                    continue
                try:
                    self.bili.add_comment(aid, text, root=root, parent=rpid)
                except BiliError as exc:
                    self.log(f"回复失败 rpid={rpid}: {exc}")
                    continue
                self.state.mark_reply_processed(rpid)
                self.log(f"已回复 {reply['uname']} rpid={rpid}: {text}")
                handled += 1
                time.sleep(2)
        return {"ok": True, "handled": handled}

    # ---- 循环 ----
    def run_forever(self, dry_run: bool = False) -> None:
        self.log("B 站独立模块启动；按 Ctrl+C 停止")
        while True:
            try:
                self.check_replies(dry_run=dry_run)
                result = self.patrol_once(dry_run=dry_run)
                self.log(f"本轮结果: {json.dumps(result, ensure_ascii=False)[:300]}")
            except KeyboardInterrupt:
                self.log("收到停止信号")
                break
            except Exception as exc:  # noqa: BLE001
                self.log(f"循环异常: {exc}")
            interval = max(60, int(self.bcfg.get("poll_interval_seconds", 300)))
            time.sleep(interval + random.randint(-20, 40))
