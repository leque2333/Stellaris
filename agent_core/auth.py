# -*- coding: utf-8 -*-
"""
Stellaris Core - 控制台安全身份与鉴权管理器 (Auth & Session Manager)
为公网映射环境提供现代化的密码校验、Session Token 会话生命周期与 RBAC 访问控制。
"""

import hashlib
import json
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple


class AuthManager:
    """
    中枢控制台统一鉴权管理器
    """
    CONFIG_FILE = "data/auth_config.json"
    DEFAULT_USERNAME = "admin"
    DEFAULT_PASSWORD = os.getenv("HUB_AUTH_PASSWORD", "admin888")

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.config_path = os.getenv("HUB_AUTH_CONFIG_PATH") or os.path.join(
            base_dir, self.CONFIG_FILE
        )
        self._ensure_initialized()

    def _hash_password(self, password: str, salt: str) -> str:
        """加盐哈希计算"""
        return hashlib.sha256((salt + ":" + password).encode('utf-8')).hexdigest()

    def _ensure_initialized(self):
        """确保存在合法的鉴权配置，如不存在则创建默认管理员账户"""
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            salt = secrets.token_hex(16)
            default_config = {
                "enabled": True,
                "username": self.DEFAULT_USERNAME,
                "password_hash": self._hash_password(self.DEFAULT_PASSWORD, salt),
                "salt": salt,
                "sessions": {},
                "created_at": int(time.time()),
                "updated_at": int(time.time())
            }
            try:
                with open(self.config_path, "w", encoding="utf-8") as f:
                    json.dump(default_config, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[AuthManager] 初始化配置失败: {e}")

    def load_config(self) -> Dict[str, Any]:
        """读取鉴权配置"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        salt = secrets.token_hex(16)
        return {
            "enabled": True,
            "username": self.DEFAULT_USERNAME,
            "password_hash": self._hash_password(self.DEFAULT_PASSWORD, salt),
            "salt": salt,
            "sessions": {}
        }

    def save_config(self, config: Dict[str, Any]) -> bool:
        """保存鉴权配置"""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        try:
            config["updated_at"] = int(time.time())
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def is_enabled(self) -> bool:
        """是否启用登录保护"""
        cfg = self.load_config()
        return bool(cfg.get("enabled", True))

    def verify_credentials(self, username: str, password: str) -> bool:
        """校验用户名与密码"""
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return True
        if username.strip() != cfg.get("username", self.DEFAULT_USERNAME):
            return False
        salt = cfg.get("salt", "")
        expected_hash = cfg.get("password_hash", "")
        computed_hash = self._hash_password(password, salt)
        return secrets.compare_digest(computed_hash, expected_hash)

    def create_session(self, username: str, duration_days: int = 7) -> str:
        """创建登录会话 Token"""
        cfg = self.load_config()
        token = secrets.token_hex(32)
        now = int(time.time())
        expires_at = now + (duration_days * 86400)

        sessions = cfg.get("sessions", {})
        # 清理已过期的历史会话
        valid_sessions = {k: v for k, v in sessions.items() if v.get("expires_at", 0) > now}
        valid_sessions[token] = {
            "username": username,
            "created_at": now,
            "expires_at": expires_at
        }
        cfg["sessions"] = valid_sessions
        self.save_config(cfg)
        return token

    def validate_session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """校验 Token 是否处于有效会话期"""
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return {"username": "admin", "admin": True}
        if not token:
            return None

        sessions = cfg.get("sessions", {})
        session = sessions.get(token)
        if not session:
            return None

        now = int(time.time())
        if session.get("expires_at", 0) < now:
            # 会话已过期，清理
            del sessions[token]
            cfg["sessions"] = sessions
            self.save_config(cfg)
            return None

        return session

    def revoke_session(self, token: Optional[str]) -> bool:
        """注销会话"""
        if not token:
            return True
        cfg = self.load_config()
        sessions = cfg.get("sessions", {})
        if token in sessions:
            del sessions[token]
            cfg["sessions"] = sessions
            self.save_config(cfg)
        return True

    def change_password(self, old_pass: str, new_pass: str) -> Tuple[bool, str]:
        """修改管理员密码"""
        cfg = self.load_config()
        if not self.verify_credentials(cfg.get("username", self.DEFAULT_USERNAME), old_pass):
            return False, "原密码校验错误"
        if not new_pass or len(new_pass.strip()) < 4:
            return False, "新密码长度至少需要 4 位"

        new_salt = secrets.token_hex(16)
        cfg["salt"] = new_salt
        cfg["password_hash"] = self._hash_password(new_pass.strip(), new_salt)
        self.save_config(cfg)
        return True, "密码已成功修改"

    def get_status(self, token: Optional[str]) -> Dict[str, Any]:
        """获取当前鉴权系统状态与登录信息"""
        session = self.validate_session(token)
        cfg = self.load_config()
        return {
            "auth_enabled": bool(cfg.get("enabled", True)),
            "authenticated": session is not None,
            "username": session.get("username") if session else None,
            "expires_at": session.get("expires_at") if session else None
        }
