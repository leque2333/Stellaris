# -*- coding: utf-8 -*-
"""
Stellaris Core - 全合规双池算力矩阵与调度中枢 (Compute Providers & Dual-Pool Dispatcher)
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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ComputeProviders")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("COMPUTE_CONFIG_PATH") or (BASE_DIR / "data" / "compute_providers.json"))

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
            "description": "官方 API 直连，高情商、响应快、极度适合前台角色发声"
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
                json.loads(resp.read().decode("utf-8", errors="ignore"))  # 顺便校验响应是合法 JSON
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

    def resolve_model(self, slot_or_key: str = "persona_slot") -> str:
        """返回当前槽位最可能实际使用的模型，用于账本记录。"""
        try:
            candidates = self._candidate_providers(slot_or_key)
            if candidates:
                return str(candidates[0].get("model") or "")
        except Exception:
            pass
        return ""

    def _candidate_providers(self, slot_or_key: str) -> List[Dict[str, Any]]:
        """组装候选 provider：凭证池优先，compute_providers 配置兜底"""
        self.load_config()
        models_cfg_path = os.path.join(os.path.dirname(self.config_path), "models_config.json")
        models_cfg: Dict[str, Any] = {}
        if os.path.exists(models_cfg_path):
            try:
                with open(models_cfg_path, "r", encoding="utf-8") as f:
                    models_cfg = json.load(f)
            except Exception:
                pass

        candidate_providers: List[Dict[str, Any]] = []
        is_func_slot = (slot_or_key in ("logic_slot", "function", "tool"))

        if is_func_slot:
            for fc in models_cfg.get("function_credentials", []):
                if fc.get("is_active", True) and (fc.get("api_key") or fc.get("auth_type") in ("ollama", "ide_bridge")):
                    candidate_providers.append({
                        "name": fc.get("name") or fc.get("provider", "功能凭证"),
                        "base_url": fc.get("base_url") or "https://api.openai.com/v1",
                        "api_key": fc.get("api_key", ""),
                        "model": fc.get("model", "default"),
                        "reasoning_effort": fc.get("reasoning_effort", "none"),
                        "temperature": fc.get("temperature", 0.2),
                    })
        else:
            for mc in models_cfg.get("master_credentials", []):
                if mc.get("is_active", True) and mc.get("api_key"):
                    candidate_providers.append({
                        "name": mc.get("name") or mc.get("provider", "主控凭证"),
                        "base_url": mc.get("base_url") or "https://generativelanguage.googleapis.com/v1beta/openai/",
                        "api_key": mc.get("api_key", ""),
                        "model": mc.get("model") or models_cfg.get("active_model", "gemini-3.8-flash"),
                        "reasoning_effort": mc.get("reasoning_effort") or models_cfg.get("reasoning_effort", "none"),
                        "temperature": 0.7,
                    })

        p_def = self.get_provider(slot_or_key)
        if p_def:
            # 兜底 provider 也跟随 Web 选择的主控 Gemini 模型
            provider_key = self._config.get("active_slots", {}).get(slot_or_key, slot_or_key)
            if provider_key == "google_gemini_official":
                active_master_model = ""
                for mc in models_cfg.get("master_credentials", []):
                    if mc.get("is_active", True) and mc.get("api_key") and mc.get("model"):
                        active_master_model = str(mc["model"])
                        break
                if active_master_model.startswith("gemini"):
                    p_def = dict(p_def)
                    p_def["model"] = active_master_model
            # 空 key 的 provider 只会制造 401，跳过它，避免打断有效凭证的轮换
            if p_def.get("api_key") or p_def.get("auth_type") in ("ollama", "ide_bridge"):
                candidate_providers.append(p_def)
        return candidate_providers

    def _build_payload(
        self,
        provider: Dict[str, Any],
        messages: List[Dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": provider.get("model", "default"),
            "messages": messages,
            "temperature": temperature if temperature is not None else provider.get("temperature", 0.7),
            "max_tokens": max_tokens or 2048,
        }
        if provider.get("reasoning_effort") and provider.get("reasoning_effort") != "none":
            payload["reasoning_effort"] = provider["reasoning_effort"]
        if extra:
            for key, value in extra.items():
                if value is not None:
                    payload[key] = value
        payload["model"] = provider.get("model", payload.get("model", "default"))
        return payload

    def _request_headers(self, provider: Dict[str, Any]) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AnanHub-ComputeMatrix/1.0",
        }
        api_key = provider.get("api_key", "")
        if api_key and api_key not in ("none", "local-ide-session", ""):
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _endpoint(self, provider: Dict[str, Any]) -> str:
        base_url = provider.get("base_url", "").rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    def _execute_request(
        self,
        provider: Dict[str, Any],
        messages: List[Dict[str, Any]],
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: int,
        current_port: Optional[int],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        base_url = provider.get("base_url", "").rstrip("/")
        if current_port and f":{current_port}" in base_url:
            return None

        payload = self._build_payload(provider, messages, temperature, max_tokens, extra)
        req = urllib.request.Request(
            self._endpoint(provider),
            data=json.dumps(payload).encode("utf-8"),
            headers=self._request_headers(provider),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    def chat_completion_response(
        self,
        slot_or_key: str,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 30,
        current_port: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """返回上游完整 OpenAI 响应（含 tool_calls），供网关透传"""
        for idx, prov in enumerate(self._candidate_providers(slot_or_key)):
            p_name = prov.get("name", f"凭证-{idx+1}")
            try:
                data = self._execute_request(
                    prov, messages, temperature, max_tokens, timeout, current_port, extra
                )
                if data and data.get("choices"):
                    return data
            except Exception as e:
                logger.warning("凭证 [%s] 调用报错: %s，切换备用凭证", p_name, e)
                try:
                    from . import messages as _msgs

                    _msgs.warn(f"凭证「{p_name}」调用失败：{str(e)[:80]}，已切备用")
                except Exception:
                    pass
                self._report_failover(p_name, e)
                continue
        return None

    def _report_failover(self, p_name: str, error: Exception) -> None:
        """凭证轮换只是运维事件，不写时间轴（那是 AI 的自传记忆）。"""
        try:
            from . import messages as _msgs

            _msgs.warn(f"凭证「{p_name}」轮换：{str(error)[:80]}")
        except Exception:
            pass

    def chat_completion(
        self,
        slot_or_key: str,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 30,
        current_port: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[str], int, int]:
        """兼容旧调用：只取文本内容与 token 数"""
        data = self.chat_completion_response(
            slot_or_key, messages, temperature, max_tokens, timeout, current_port, extra
        )
        if not data:
            return None, 0, 0
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        content = message.get("content") or ""
        usage = data.get("usage", {}) or {}
        p_in = int(usage.get("prompt_tokens", len(str(messages)) // 3) or 0)
        p_out = int(usage.get("completion_tokens", len(content) // 2) or 0)
        return content, p_in, p_out

    def stream_chat_completion(
        self,
        slot_or_key: str,
        messages: List[Dict[str, Any]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        current_port: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        """真流式：逐块 yield 上游 SSE 原始字节；首个成功连接前支持故障转移"""
        for idx, prov in enumerate(self._candidate_providers(slot_or_key)):
            p_name = prov.get("name", f"凭证-{idx+1}")
            base_url = prov.get("base_url", "").rstrip("/")
            if current_port and f":{current_port}" in base_url:
                continue

            payload = self._build_payload(prov, messages, temperature, max_tokens, extra)
            payload["stream"] = True
            headers = self._request_headers(prov)
            headers["Accept"] = "text/event-stream"

            req = urllib.request.Request(
                self._endpoint(prov),
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                resp = urllib.request.urlopen(req, timeout=timeout)
            except Exception as e:
                logger.warning("流式凭证 [%s] 连接失败: %s，切换备用凭证", p_name, e)
                try:
                    from . import messages as _msgs

                    _msgs.warn(f"流式凭证「{p_name}」连接失败：{str(e)[:80]}，已切备用")
                except Exception:
                    pass
                self._report_failover(p_name, e)
                continue

            try:
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    yield chunk
            finally:
                try:
                    resp.close()
                except Exception:
                    pass
            return


# 全局单例（网关与 CLI 共用）
compute_manager = ComputeManager()

