# -*- coding: utf-8 -*-
"""
助手智能体核心 (Agent Core) - 全合规双池算力矩阵与调度中枢 (Compute Providers & Dual-Pool Dispatcher)
支持：
1. 官方 IDE / 本地开发者环境桥接 (IDE Bridge / Local Toolchain)
2. 官方直接 API (Official BYOK: DeepSeek, Google, OpenAI, Anthropic, 国内合规大模型)
3. 本地私有离线引擎 (Local Engine: Ollama, vLLM, LM Studio)
4. 双槽位调度：前台发声池 (Persona Slot) + 幕后工兵池 (Logic Worker Slot)
"""

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComputeProviders")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "data" / "compute_providers.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "active_slots": {
        "persona_slot": "ide_bridge",
        "logic_slot": "deepseek_official"
    },
    "routing_policy": {
        "auto_dispatch_logic": True,
        "worker_timeout_sec": 30,
        "fallback_to_persona": True
    },
    "providers": {
        "ide_bridge": {
            "name": "IDE / 本地开发者工具链 (IDE Bridge)",
            "auth_type": "ide_bridge",
            "base_url": "http://127.0.0.1:18890/v1",
            "api_key": "local-ide-session",
            "model": "gemini-3.1-pro-preview-customtools",
            "temperature": 0.7,
            "description": "复用本地已授权的 IDE / 官方开发者会话，合规高智商，代码与推导专精"
        },
        "deepseek_official": {
            "name": "DeepSeek 官方直接 API (逻辑战神)",
            "auth_type": "official_api",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
            "temperature": 0.2,
            "description": "官方正规 API Key，极低成本，专精代码重构、数学与复杂排错"
        },
        "google_gemini_official": {
            "name": "Google Gemini 官方直接 API",
            "auth_type": "official_api",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "api_key": "",
            "model": "gemini-2.0-flash",
            "temperature": 0.7,
            "description": "官方 API 直连，高情商、响应快、极度适合前台助手发声"
        },
        "local_ollama": {
            "name": "本地私有化 Ollama 引擎",
            "auth_type": "local_engine",
            "base_url": "http://127.0.0.1:11434/v1",
            "api_key": "ollama",
            "model": "qwen2.5-coder:7b",
            "temperature": 0.2,
            "description": "本地硬件完全离线运行，100% 隐私，断网也可用"
        }
    }
}


class ComputeManager:
    """
    算力矩阵与双池调度管理器
    """
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or CONFIG_PATH
        self._config: Dict[str, Any] = {}
        self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """读取算力配置，缺失时自动初始化默认合规骨架"""
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                    # 确保基础结构完整
                    for k in ("active_slots", "providers", "routing_policy"):
                        if k not in self._config:
                            self._config[k] = DEFAULT_CONFIG[k]
                    return self._config
            except Exception as e:
                logger.warning(f"读取算力配置异常: {e}，将采用默认骨架")
        self._config = json.loads(json.dumps(DEFAULT_CONFIG))
        self.save_config(self._config)
        return self._config

    def save_config(self, config: Dict[str, Any]) -> bool:
        """保存算力配置"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._config = config
            return True
        except Exception as e:
            logger.error(f"保存算力配置失败: {e}")
            return False

    def get_config(self) -> Dict[str, Any]:
        if not self._config:
            self.load_config()
        # 敏感信息保护：返回给前端时隐藏完整 API Key
        safe_copy = json.loads(json.dumps(self._config))
        for p_id, p in safe_copy.get("providers", {}).items():
            k = p.get("api_key", "")
            if k and len(k) > 8 and not k.startswith("local"):
                p["api_key_masked"] = f"{k[:3]}****{k[-4:]}"
                p["has_key"] = True
            else:
                p["api_key_masked"] = "" if not k else k
                p["has_key"] = bool(k and k != "none")
        return safe_copy

    def update_slots(self, persona_slot: str, logic_slot: str) -> bool:
        """更新前台发声与幕后工兵槽位绑定"""
        self.load_config()
        if persona_slot in self._config.get("providers", {}):
            self._config["active_slots"]["persona_slot"] = persona_slot
        if logic_slot in self._config.get("providers", {}):
            self._config["active_slots"]["logic_slot"] = logic_slot
        return self.save_config(self._config)

    def get_provider(self, slot_or_key: str) -> Optional[Dict[str, Any]]:
        """获取指定槽位或提供商配置 (自动桥接环境变量凭据)"""
        self.load_config()
        slots = self._config.get("active_slots", {})
        provider_key = slots.get(slot_or_key, slot_or_key)
        p = self._config.get("providers", {}).get(provider_key)
        if not p:
            return None
        p_copy = dict(p)
        # 环境变量动态补齐，杜绝配置硬编码与留空
        if not p_copy.get("api_key") or p_copy.get("api_key") == "none":
            if provider_key in ("deepseek_official", "deepseek"):
                p_copy["api_key"] = os.getenv("DEEPSEEK_API_KEY", "")
            elif provider_key in ("google_gemini_official", "gemini"):
                p_copy["api_key"] = os.getenv("GEMINI_API_KEY", "")
            elif provider_key in ("openai_official", "openai"):
                p_copy["api_key"] = os.getenv("OPENAI_API_KEY", "")
        return p_copy

    @staticmethod
    def is_logic_heavy_task(user_text: str) -> bool:
        """
        轻量意图识别：检测是否为需要高逻辑、写代码、排查 Bug 或数理推导的硬核任务
        """
        if not user_text:
            return False
        text = user_text.lower()
        
        # 1. 包含代码块或典型错误堆栈
        if "```" in user_text or "traceback" in text or "syntaxerror" in text or "exception" in text:
            return True
        
        # 2. 强逻辑意图关键词
        logic_patterns = [
            r"重构", r"死锁", r"报错", r"排查", r"编写.*代码", r"写一个.*(函数|脚本|算法|类|模块)",
            r"优化.*性能", r"sql.*(查询|优化)", r"正则(表达式)?", r"计算.*(推导|证明)",
            r"diff", r"bug", r"debug", r"refactor", r"deadlock", r"algorithm"
        ]
        for pat in logic_patterns:
            if re.search(pat, text):
                return True
        return False

    def test_provider_connection(self, provider_key: str) -> Dict[str, Any]:
        """
        测试指定 Provider 的连通性与延迟
        """
        provider = self.get_provider(provider_key)
        if not provider:
            return {"ok": False, "error": f"找不到 Provider: {provider_key}"}

        base_url = provider.get("base_url", "").rstrip("/")
        api_key = provider.get("api_key", "")
        model = provider.get("model", "default")
        auth_type = provider.get("auth_type", "official_api")

        endpoint = f"{base_url}/chat/completions"
        if "models" in base_url or base_url.endswith("/models"):
            endpoint = base_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AnanHub-ComputeMatrix/1.0"
        }
        if api_key and api_key != "none":
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 5
        }

        t0 = time.time()
        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                status = resp.status
                cost_ms = int((time.time() - t0) * 1000)
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                return {
                    "ok": True,
                    "status_code": status,
                    "latency_ms": cost_ms,
                    "model": model,
                    "message": f"连接正常 ({cost_ms}ms)"
                }
        except urllib.error.HTTPError as he:
            cost_ms = int((time.time() - t0) * 1000)
            # 401/403 明确是 Key 问题，但说明网络通畅
            err_msg = f"HTTP {he.code}: {he.reason}"
            return {"ok": False, "status_code": he.code, "latency_ms": cost_ms, "error": err_msg}
        except Exception as e:
            cost_ms = int((time.time() - t0) * 1000)
            return {"ok": False, "latency_ms": cost_ms, "error": str(e)}

    def chat_completion(
        self,
        slot_or_key: str,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 15,
        current_port: Optional[int] = None
    ) -> Tuple[Optional[str], int, int]:
        """
        向指定槽位或 Provider 发起真实大模型对话推导
        返回: (reply_text, prompt_tokens, completion_tokens)
        若上游离线、超时或未配置 Key，返回 (None, 0, 0) 由中枢无缝降级兜底
        """
        provider = self.get_provider(slot_or_key)
        if not provider:
            return None, 0, 0

        base_url = provider.get("base_url", "").rstrip("/")
        api_key = provider.get("api_key", "")
        model = provider.get("model", "default")

        # 防本地自死锁保护：如果 base_url 指向自身当前端口，不发本地 HTTP 自循环
        if current_port and f":{current_port}" in base_url:
            return None, 0, 0

        endpoint = f"{base_url}/chat/completions"
        if base_url.endswith("/chat/completions"):
            endpoint = base_url

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AnanHub-ComputeMatrix/1.0"
        }
        if api_key and api_key not in ("none", "local-ide-session", ""):
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else provider.get("temperature", 0.7),
            "max_tokens": max_tokens or 2048
        }

        try:
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                choices = data.get("choices", [])
                if choices:
                    content = choices[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    p_in = usage.get("prompt_tokens", len(str(messages)) // 3)
                    p_out = usage.get("completion_tokens", len(content) // 2)
                    return content, p_in, p_out
        except Exception as e:
            logger.warning(f"调用 Provider [{provider.get('name')}] 异常: {e}")
            return None, 0, 0
        return None, 0, 0


# 全局单例
compute_manager = ComputeManager()
