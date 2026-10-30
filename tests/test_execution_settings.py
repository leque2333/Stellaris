# -*- coding: utf-8 -*-
"""执行 AI 凭据：文件优先、掩码不回显、可测试连接"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core.execution import settings as exec_settings  # noqa: E402

ENV_KEYS = (
    "HUB_EXECUTION_AI_PATH",
    "HUB_MODELS_CONFIG_PATH",
    "HUB_EXEC_API_KEY",
    "HUB_EXEC_BASE_URL",
    "HUB_EXEC_MODEL",
)


class _FakeLLM:
    def __init__(self, reply="OK"):
        self.reply = reply
        self.api_key = "fake"
        self.calls = []

    async def generate_with_tools(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply, []


class ExecutionSettingsTest(unittest.TestCase):
    def setUp(self):
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = Path(tempfile.mkdtemp(prefix="anan_exec_cfg_"))
        self.path = self.tmp / "execution_ai.json"
        os.environ["HUB_EXECUTION_AI_PATH"] = str(self.path)
        self.models_cfg = self.tmp / "models_config.json"
        self.models_cfg.write_text('{"function_credentials": []}', encoding="utf-8")
        os.environ["HUB_MODELS_CONFIG_PATH"] = str(self.models_cfg)

    def tearDown(self):
        import shutil
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_defaults_fall_back_to_main_model(self):
        data = exec_settings.public_settings()
        self.assertEqual(data["source"], "main")
        self.assertFalse(data["has_api_key"])
        self.assertEqual(data["api_key"], "")
        self.assertFalse(data["file_exists"])
        self.assertTrue(data["base_url"].startswith("http"))
        self.assertTrue(data["model"])

    def test_save_masks_key_and_round_trips(self):
        saved = exec_settings.save_settings(
            base_url="https://api.example.com/v1",
            model="probe-model",
            api_key="sk-secret-1234567890",
        )
        self.assertEqual(saved["source"], "file")
        self.assertTrue(saved["has_api_key"])
        self.assertNotIn("sk-secret-1234567890", json.dumps(saved))
        self.assertIn(exec_settings.MASKED_KEY, saved["api_key"])

        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["api_key"], "sk-secret-1234567890")
        self.assertEqual(on_disk["model"], "probe-model")

        loaded = exec_settings.load_settings()
        self.assertEqual(loaded["api_key"], "sk-secret-1234567890")
        self.assertEqual(loaded["base_url"], "https://api.example.com/v1")

    def test_masked_key_keeps_existing_secret(self):
        exec_settings.save_settings(api_key="sk-keep-me-0001", model="m1")
        exec_settings.save_settings(api_key=exec_settings.MASKED_KEY, model="m2")
        loaded = exec_settings.load_settings()
        self.assertEqual(loaded["api_key"], "sk-keep-me-0001")
        self.assertEqual(loaded["model"], "m2")

    def test_clear_key(self):
        exec_settings.save_settings(api_key="sk-clear-me", model="m1")
        saved = exec_settings.save_settings(clear_api_key=True)
        self.assertFalse(saved["has_api_key"])
        self.assertEqual(exec_settings.load_settings()["source"], "main")

    def test_env_key_not_persisted_to_file(self):
        os.environ["HUB_EXEC_API_KEY"] = "sk-from-env"
        os.environ["HUB_EXEC_BASE_URL"] = "https://env.example.com/v1"
        loaded = exec_settings.load_settings()
        self.assertEqual(loaded["source"], "env")
        self.assertEqual(loaded["api_key"], "sk-from-env")

        exec_settings.save_settings(model="m-env")
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["api_key"], "")
        self.assertEqual(exec_settings.load_settings()["source"], "env")

    def test_connection_ok(self):
        exec_settings.save_settings(api_key="sk-test", model="m-test")
        fake = _FakeLLM("OK")
        result = asyncio.run(exec_settings.test_connection(llm=fake))
        self.assertTrue(result["ok"])
        self.assertEqual(result["reply"], "OK")
        self.assertEqual(result["model"], "m-test")
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertTrue(fake.calls)

    def test_connection_reports_fallback_text(self):
        exec_settings.save_settings(api_key="sk-test", model="m-test")
        fake = _FakeLLM("（提示：未检测到有效大模型 API Key）")
        result = asyncio.run(exec_settings.test_connection(llm=fake))
        self.assertFalse(result["ok"])
        self.assertIn("未检测到", result["error"])

    def test_function_credential_takes_priority(self):
        self.models_cfg.write_text(json.dumps({
            "function_credentials": [
                {
                    "name": "深寻",
                    "provider": "DeepSeek",
                    "model": "deepseek-chat",
                    "api_key": "sk-fn-123456",
                    "is_active": True,
                    "for_execution": True,
                },
                {
                    "name": "普通功能凭证",
                    "provider": "OpenAI",
                    "model": "gpt-4o-mini",
                    "api_key": "sk-normal-999999",
                    "is_active": True,
                },
            ],
        }), encoding="utf-8")
        exec_settings.save_settings(api_key="sk-file-999999", model="file-model")

        loaded = exec_settings.load_settings()
        self.assertEqual(loaded["source"], "function")
        self.assertEqual(loaded["api_key"], "sk-fn-123456")
        self.assertEqual(loaded["model"], "deepseek-chat")
        self.assertEqual(loaded["base_url"], "https://api.deepseek.com/v1")
        self.assertEqual(loaded["credential_name"], "深寻")

        public = exec_settings.public_settings()
        self.assertNotIn("sk-fn-123456", json.dumps(public))

    def test_function_credential_used_without_marker(self):
        self.models_cfg.write_text(json.dumps({
            "function_credentials": [{
                "name": "普通功能凭证",
                "provider": "OpenAI",
                "model": "gpt-4o-mini",
                "api_key": "sk-normal-999999",
                "is_active": True,
            }],
        }), encoding="utf-8")
        loaded = exec_settings.load_settings()
        self.assertEqual(loaded["source"], "function")
        self.assertEqual(loaded["api_key"], "sk-normal-999999")
        self.assertEqual(loaded["base_url"], "https://api.openai.com/v1")

    def test_connection_requires_key(self):
        result = asyncio.run(exec_settings.test_connection())
        self.assertFalse(result["ok"])
        self.assertIn("Key", result["error"])


if __name__ == "__main__":
    unittest.main()
