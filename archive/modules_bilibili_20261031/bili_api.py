# -*- coding: utf-8 -*-
"""B 站只读 / 评论 API 客户端（标准库实现，不依赖 bilibili_api）。"""

from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional

API_ROOT = "https://api.bilibili.com"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# B站 WBI 签名固定混淆表
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _mixin_key(orig: str) -> str:
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB if i < len(orig))[:32]


def _url_key(url: str) -> str:
    name = str(url or "").rsplit("/", 1)[-1]
    return name.split(".", 1)[0]


class BiliError(RuntimeError):
    def __init__(self, code: int, message: str, path: str = ""):
        super().__init__(f"B站接口错误 {code}: {message}" + (f" ({path})" if path else ""))
        self.code = code
        self.message = message
        self.path = path


def clean_html(text: Any) -> str:
    raw = html.unescape(str(text or ""))
    return re.sub(r"<[^>]+>", "", raw).strip()


def parse_duration(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    if not text:
        return 0
    if ":" not in text:
        try:
            return int(text)
        except ValueError:
            return 0
    total = 0
    for part in text.split(":"):
        try:
            total = total * 60 + int(part)
        except ValueError:
            return 0
    return total


class BiliClient:
    """最小可用的 B 站客户端：热门 / 排行 / 分区新视频 / 视频详情 / 评论。"""

    def __init__(
        self,
        sessdata: str = "",
        bili_jct: str = "",
        buvid3: str = "",
        dedeuserid: str = "",
        proxy: str = "",
        timeout: int = 20,
    ) -> None:
        self.sessdata = (sessdata or "").strip()
        self.bili_jct = (bili_jct or "").strip()
        self.buvid3 = (buvid3 or "").strip()
        self.dedeuserid = (dedeuserid or "").strip()
        self.timeout = timeout
        self.proxy = (proxy or "").strip()
        self._opener = self._build_opener()
        self._wbi_cache: Dict[str, Any] = {"keys": None, "ts": 0.0}

    def _build_opener(self) -> urllib.request.OpenerDirector:
        if self.proxy:
            handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
        else:
            handler = urllib.request.ProxyHandler({})
        return urllib.request.build_opener(handler)

    @property
    def logged_in(self) -> bool:
        return bool(self.sessdata and self.bili_jct and self.dedeuserid)

    @property
    def csrf(self) -> str:
        return self.bili_jct

    def _cookie(self) -> str:
        pairs = []
        if self.sessdata:
            pairs.append(f"SESSDATA={self.sessdata}")
        if self.bili_jct:
            pairs.append(f"bili_jct={self.bili_jct}")
        if self.buvid3:
            pairs.append(f"buvid3={self.buvid3}")
        if self.dedeuserid:
            pairs.append(f"DedeUserID={self.dedeuserid}")
        return "; ".join(pairs)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        allow_codes: Iterable[int] = (),
    ) -> Any:
        url = API_ROOT + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        body = None
        headers = {
            "User-Agent": DEFAULT_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        cookie = self._cookie()
        if cookie:
            headers["Cookie"] = cookie
        if method == "POST":
            payload = dict(data or {})
            if self.bili_jct and "csrf" not in payload:
                payload["csrf"] = self.bili_jct
            body = urllib.parse.urlencode(payload).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        last_error: Optional[Exception] = None
        for attempt in range(3):
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                return self._parse(raw, path, allow_codes)
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                try:
                    return self._parse(raw, path, allow_codes)
                except BiliError:
                    last_error = BiliError(exc.code, f"HTTP {exc.code}", path)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.2 * (attempt + 1))
        raise BiliError(-1, str(last_error or "网络请求失败"), path)

    @staticmethod
    def _parse(raw: str, path: str, allow_codes: Iterable[int] = ()) -> Any:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BiliError(-1, f"返回不是 JSON: {raw[:120]}", path) from exc
        code = int(payload.get("code", 0) or 0)
        allowed = {int(c) for c in allow_codes}
        if code != 0 and code not in allowed:
            raise BiliError(code, str(payload.get("message") or payload.get("msg") or ""), path)
        return payload.get("data")

    # ---- 账号 ----
    def nav(self) -> Dict[str, Any]:
        try:
            data = self._request("GET", "/x/web-interface/nav") or {}
        except BiliError as exc:
            if exc.code == -101:
                return {"is_login": False, "mid": 0, "uname": "", "level": 0}
            raise
        return {
            "is_login": bool(data.get("isLogin")),
            "mid": int(data.get("mid") or 0),
            "uname": str(data.get("uname") or ""),
            "level": int(data.get("level_info", {}).get("current_level") or 0),
        }

    # ---- 视频流 ----
    def popular(self, page: int = 1, page_size: int = 20) -> List[Dict[str, Any]]:
        data = self._request(
            "GET", "/x/web-interface/popular", {"pn": page, "ps": page_size}
        ) or {}
        return list(data.get("list") or [])

    def ranking(self, rid: int = 0) -> List[Dict[str, Any]]:
        data = self._request(
            "GET", "/x/web-interface/ranking/v2", {"rid": rid, "type": "all"}
        ) or {}
        return list(data.get("list") or [])

    def newlist(self, rid: int, page: int = 1, page_size: int = 20) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            "/x/web-interface/newlist",
            {"rid": rid, "pn": page, "ps": page_size},
        ) or {}
        return list(data.get("archives") or data.get("list") or [])

    # ---- WBI 搜索（按发布时间找最新视频） ----
    def _wbi_keys(self) -> tuple[str, str]:
        now = time.time()
        cached = self._wbi_cache.get("keys")
        if cached and now - float(self._wbi_cache.get("ts") or 0) < 3600:
            return cached  # type: ignore[return-value]
        data = self._request(
            "GET", "/x/web-interface/nav", allow_codes=(-101,)
        ) or {}
        wbi = data.get("wbi_img") if isinstance(data, dict) else None
        img_key = _url_key((wbi or {}).get("img_url", ""))
        sub_key = _url_key((wbi or {}).get("sub_url", ""))
        if not img_key or not sub_key:
            raise BiliError(-1, "无法获取 WBI 密钥", "/x/web-interface/nav")
        self._wbi_cache = {"keys": (img_key, sub_key), "ts": now}
        return img_key, sub_key

    def _signed_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        img_key, sub_key = self._wbi_keys()
        mixin = _mixin_key(img_key + sub_key)
        payload = {k: v for k, v in params.items() if v is not None}
        payload["wts"] = int(time.time())
        filtered: Dict[str, str] = {}
        for key, value in sorted(payload.items()):
            text = "".join(ch for ch in str(value) if ch not in "!'()*")
            filtered[key] = text
        query = urllib.parse.urlencode(filtered)
        filtered["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
        return filtered

    def search_videos(
        self,
        keyword: str,
        page: int = 1,
        page_size: int = 20,
        order: str = "pubdate",
    ) -> List[Dict[str, Any]]:
        params = self._signed_params({
            "search_type": "video",
            "keyword": keyword,
            "order": order,
            "page": page,
            "page_size": page_size,
        })
        data = self._request("GET", "/x/web-interface/wbi/search/type", params) or {}
        return list(data.get("result") or [])

    def view(self, bvid: str) -> Dict[str, Any]:
        return self._request("GET", "/x/web-interface/view", {"bvid": bvid}) or {}

    def tags(self, bvid: str) -> List[str]:
        try:
            data = self._request("GET", "/x/tag/archive/tags", {"bvid": bvid}) or []
        except BiliError:
            return []
        names = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("tag_name"):
                names.append(str(item["tag_name"]))
        return names

    # ---- 评论 ----
    def replies(self, aid: int, page: int = 1, sort: int = 2) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            "/x/v2/reply",
            {"type": 1, "oid": aid, "sort": sort, "ps": 20, "pn": page},
        ) or {}
        return list(data.get("replies") or [])

    def sub_replies(self, aid: int, root: int, page: int = 1) -> List[Dict[str, Any]]:
        data = self._request(
            "GET",
            "/x/v2/reply/reply",
            {"type": 1, "oid": aid, "root": root, "ps": 20, "pn": page},
        ) or {}
        return list(data.get("replies") or [])

    def add_comment(
        self,
        aid: int,
        message: str,
        root: int = 0,
        parent: int = 0,
    ) -> Dict[str, Any]:
        if not self.logged_in:
            raise BiliError(-101, "未配置 SESSDATA / bili_jct / DedeUserID")
        payload = {"oid": aid, "type": 1, "message": message, "plat": 1}
        if root:
            payload["root"] = root
            payload["parent"] = parent or root
        data = self._request("POST", "/x/v2/reply/add", data=payload) or {}
        return data if isinstance(data, dict) else {}

    def like_comment(self, aid: int, rpid: int, action: int = 1) -> bool:
        if not self.logged_in:
            return False
        self._request(
            "POST",
            "/x/v2/reply/action",
            data={"oid": aid, "type": 1, "rpid": rpid, "action": action},
        )
        return True


def normalize_video(item: Dict[str, Any]) -> Dict[str, Any]:
    """把 popular / ranking / newlist 的不同字段压成统一结构。"""
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    stat = item.get("stat") if isinstance(item.get("stat"), dict) else {}
    bvid = str(item.get("bvid") or item.get("bv_id") or "").strip()
    aid = int(item.get("aid") or item.get("id") or 0)
    title = clean_html(item.get("title"))
    desc = clean_html(item.get("desc") or item.get("description"))
    author = str(owner.get("name") or item.get("author") or item.get("up_name") or "").strip()
    mid = int(owner.get("mid") or item.get("mid") or item.get("uid") or 0)
    duration = parse_duration(item.get("duration") or item.get("length"))
    tags = item.get("tags")
    if not isinstance(tags, list):
        raw_tags = item.get("tag") or item.get("tags") or ""
        tags = [part.strip() for part in str(raw_tags).split(",") if part.strip()]
    return {
        "bvid": bvid,
        "aid": aid,
        "title": title,
        "desc": desc,
        "author": author,
        "mid": mid,
        "duration": duration,
        "pubdate": int(item.get("pubdate") or 0),
        "view": int(stat.get("view") or item.get("play") or 0),
        "tags": [str(t) for t in tags if t],
        "url": f"https://www.bilibili.com/video/{bvid}" if bvid else "",
    }


def merge_video_detail(base: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    """用 view 接口补全标题/简介/UP/时长。"""
    if not isinstance(detail, dict):
        return base
    merged = dict(base)
    merged.update(normalize_video(detail))
    if not merged.get("tags"):
        merged["tags"] = list(base.get("tags") or [])
    pages = detail.get("pages") if isinstance(detail.get("pages"), list) else []
    if pages and isinstance(pages[0], dict):
        merged["cid"] = int(pages[0].get("cid") or 0)
        if not merged.get("duration"):
            merged["duration"] = int(pages[0].get("duration") or 0)
    if not merged.get("title"):
        merged["title"] = base.get("title", "")
    if not merged.get("desc"):
        merged["desc"] = base.get("desc", "")
    if not merged.get("author"):
        merged["author"] = base.get("author", "")
    if not merged.get("mid"):
        merged["mid"] = base.get("mid", 0)
    return merged


def filter_videos(
    videos: Iterable[Dict[str, Any]],
    *,
    seen: Iterable[str] = (),
    own_mid: int = 0,
    keywords: Iterable[str] = (),
    blocked: Iterable[str] = (),
    min_hits: int = 1,
    min_duration: int = 0,
    max_duration: int = 0,
) -> List[Dict[str, Any]]:
    seen_set = set(seen or [])
    keyword_list = [str(k).lower() for k in (keywords or []) if str(k).strip()]
    blocked_list = [str(k).lower() for k in (blocked or []) if str(k).strip()]
    result = []
    for video in videos:
        if not isinstance(video, dict):
            continue
        bvid = str(video.get("bvid") or "")
        if not bvid or bvid in seen_set:
            continue
        if own_mid and int(video.get("mid") or 0) == own_mid:
            continue
        duration = int(video.get("duration") or 0)
        if min_duration and duration and duration < min_duration:
            continue
        if max_duration and duration and duration > max_duration:
            continue
        corpus = " ".join(
            [
                str(video.get("title") or ""),
                str(video.get("desc") or ""),
                " ".join(str(t) for t in (video.get("tags") or [])),
            ]
        ).lower()
        if any(word in corpus for word in blocked_list):
            continue
        hits = [k for k in keyword_list if k in corpus]
        if keyword_list and len(hits) < min_hits:
            continue
        enriched = dict(video)
        enriched["keyword_hits"] = hits
        result.append(enriched)
    return result


def legacy_filter(
    video: Dict[str, Any],
    *,
    must_keywords: Iterable[str] = (),
    anime_keywords: Iterable[str] = (),
    extra_keywords: Iterable[str] = (),
) -> Optional[Dict[str, Any]]:
    """旧版三层筛选：必须命中「大肥鱼」+ 必须命中「动画/二次元」+ AI 词可选。"""
    corpus = " ".join(
        [
            str(video.get("title") or ""),
            str(video.get("desc") or ""),
            " ".join(str(t) for t in (video.get("tags") or [])),
        ]
    ).lower()
    must = [str(k).lower() for k in (must_keywords or []) if str(k).strip()]
    anime = [str(k).lower() for k in (anime_keywords or []) if str(k).strip()]
    extra = [str(k).lower() for k in (extra_keywords or []) if str(k).strip()]
    if must and not all(word in corpus for word in must):
        return None
    anime_hits = [word for word in anime if word in corpus]
    if anime and not anime_hits:
        return None
    extra_hits = [word for word in extra if word in corpus]
    enriched = dict(video)
    enriched["keyword_hits"] = must + anime_hits + extra_hits
    return enriched


def reply_view(item: Dict[str, Any]) -> Dict[str, Any]:
    member = item.get("member") if isinstance(item.get("member"), dict) else {}
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    return {
        "rpid": int(item.get("rpid") or 0),
        "mid": int(member.get("mid") or 0),
        "uname": str(member.get("uname") or ""),
        "message": clean_html(content.get("message")),
        "ctime": int(item.get("ctime") or 0),
    }
