# -*- coding: utf-8 -*-
"""中枢 /v1 客户端：模块身份由请求头声明，不直连模型。"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class HubError(RuntimeError):
    pass


def _header_value(value: Any) -> str:
    """HTTP 头只保证 ASCII；中文用 URL 编码，中枢侧解码。"""
    text = str(value or "")
    if not text:
        return ""
    try:
        text.encode("ascii")
        return text
    except UnicodeEncodeError:
        return urllib.parse.quote(text, safe="")


class HubClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        host_id: str = "bilibili",
        host_name: str = "B站",
        host_uid: str = "",
        timeout: int = 120,
    ) -> None:
        self.base_url = (base_url or "http://127.0.0.1:18895/v1").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or "gemini-3.8-flash"
        self.host_id = host_id or "bilibili"
        self.host_name = host_name or "B站"
        self.host_uid = str(host_uid or "")
        self.timeout = int(timeout or 120)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        user_id: str = "",
        user_name: str = "",
        channel: str = "",
        temperature: float = 0.8,
        max_tokens: int = 500,
    ) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
            "X-Host-Id": self.host_id,
            "X-Host-Name": _header_value(self.host_name),
            "X-Host-UID": self.host_uid,
            "X-Platform": "bilibili",
        }
        if user_id:
            headers["X-User-Id"] = str(user_id)
        if user_name:
            headers["X-User-Name"] = _header_value(user_name)
        if channel:
            headers["X-Channel"] = channel
        headers = {k: v for k, v in headers.items() if v}

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Optional[Exception] = None
        for attempt in range(3):
            request = urllib.request.Request(
                self._url(), data=body, headers=headers, method="POST"
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if data.get("error"):
                    raise HubError(str(data.get("error")))
                choices = data.get("choices") or []
                if not choices:
                    raise HubError("中枢没有返回 choices")
                message = choices[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    content = "".join(
                        str(part.get("text") or "")
                        for part in content
                        if isinstance(part, dict)
                    )
                return str(content or "").strip()
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                last_error = HubError(f"中枢 HTTP {exc.code}: {raw[:200]}")
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        raise HubError(str(last_error or "中枢请求失败"))

    def chat_json(self, messages: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]:
        raw = self.chat(messages, **kwargs)
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # 模型偶尔会截断 JSON 或夹带引号；按字段兜底提取
        result: Dict[str, Any] = {}
        score_match = re.search(r'"score"\s*:\s*([0-9]+(?:\.[0-9]+)?)', text)
        if score_match:
            try:
                result["score"] = float(score_match.group(1))
            except ValueError:
                pass
        for key in ("reason", "comment"):
            match = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', text, re.S)
            if match:
                try:
                    result[key] = json.loads(f'"{match.group(1)}"')
                except json.JSONDecodeError:
                    result[key] = match.group(1).replace('\\"', '"')
        if result:
            return result

        # 完全不是 JSON：把正文当评论
        if text.startswith("{"):
            text = text.strip("{}").strip()
        return {"comment": text}
