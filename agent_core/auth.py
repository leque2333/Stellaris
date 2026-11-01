# -*- coding: utf-8 -*-
"""
Stellaris Core - 控制台安全身份与鉴权管理器 (Auth & Session Manager)
为公网映射环境提供现代化的密码校验、Session Token 会话生命周期与 RBAC 访问控制。

安全约定：
1. 不再内置任何默认口令：首次启动随机生成，只在控制台打印一次并落到
   `data/auth_initial_password.txt`（仅本机可读），登录后强制改密。
2. 口令统一用 PBKDF2-HMAC-SHA256（带算法前缀），老 `sha256(salt:password)`
   配置在登录成功时**渐进升级**，不打断已部署实例。
3. 改密会吊销全部旧会话；会话数有上限，避免配置文件无限膨胀。
"""

import hashlib
import json
import os
import secrets
import time
from typing import Any, Dict, Optional, Tuple

# 口令哈希参数：算法$迭代次数$十六进制摘要
PASSWORD_ALGO = "pbkdf2_sha256"
PASSWORD_ROUNDS = 240_000


class AuthManager:
    """
    中枢控制台统一鉴权管理器
    """

    CONFIG_FILE = "data/auth_config.json"
    DEFAULT_USERNAME = "admin"
    MIN_PASSWORD_LENGTH = 12
    MAX_SESSIONS = 20
    # 历史版本内置过的弱口令；命中就立刻轮换，避免旧配置继续可用
    WEAK_PASSWORDS = ("admin888", "123456", "admin", "password", "anan888")
    INITIAL_PASSWORD_FILE = "data/auth_initial_password.txt"

    def __init__(self, base_dir: str, initial_password: Optional[str] = None):
        self.base_dir = base_dir
        self.config_path = os.getenv("HUB_AUTH_CONFIG_PATH") or os.path.join(
            base_dir, self.CONFIG_FILE
        )
        # 初始口令文件永远跟着**实际使用的配置文件**走，
        # 避免测试/多实例把口令写进真实 data/ 目录
        self.password_file = os.path.join(
            os.path.dirname(self.config_path) or base_dir,
            os.path.basename(self.INITIAL_PASSWORD_FILE),
        )
        # 只有本次启动真的生成了新口令才非空，测试/调用方可直接拿来登录
        self.initial_password: Optional[str] = None
        self._initial_password_override = initial_password
        self._ensure_initialized()

    # ---------------- 口令哈希 ----------------
    @staticmethod
    def _pbkdf2_hex(password: str, salt: str, rounds: int) -> str:
        """PBKDF2-HMAC-SHA256 摘要（十六进制）。"""
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds)
        ).hex()

    @classmethod
    def _legacy_hash(cls, password: str, salt: str) -> str:
        """历史格式：单轮 sha256(salt:password)，只用于校验和迁移。"""
        return hashlib.sha256((salt + ":" + password).encode("utf-8")).hexdigest()

    def _hash_password(self, password: str, salt: str) -> str:
        """生成新格式哈希：`pbkdf2_sha256$迭代次数$摘要`。"""
        digest = self._pbkdf2_hex(password, salt, PASSWORD_ROUNDS)
        return f"{PASSWORD_ALGO}${PASSWORD_ROUNDS}${digest}"

    def _verify_hash(self, password: str, salt: str, stored: str) -> bool:
        """校验口令；同时兼容新格式与历史 sha256 格式。"""
        stored = str(stored or "")
        if not stored:
            return False
        if stored.startswith(PASSWORD_ALGO + "$"):
            parts = stored.split("$", 2)
            if len(parts) != 3:
                return False
            try:
                rounds = int(parts[1])
            except (TypeError, ValueError):
                return False
            return secrets.compare_digest(
                self._pbkdf2_hex(password, salt, rounds), parts[2]
            )
        return secrets.compare_digest(self._legacy_hash(password, salt), stored)

    def _is_legacy_hash(self, stored: str) -> bool:
        return bool(stored) and not str(stored).startswith(PASSWORD_ALGO + "$")

    # ---------------- 初始化 ----------------
    @staticmethod
    def _random_password() -> str:
        """生成 24 位随机口令（大小写数字符号混合，足够抗爆破）。"""
        alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789!@#%^*-_+="
        return "".join(secrets.choice(alphabet) for _ in range(24))

    def _ensure_initialized(self):
        """确保存在合法的鉴权配置；首次启动生成随机口令，绝不使用默认口令。

        注意：这里**不做**任何“写存量配置”的破坏性动作——弱口令轮换放在
        `enforce_password_policy()`，只在服务真正启动时调用一次。
        否则任何 `import console.server`（比如跑测试）都会改掉线上口令。
        """
        if os.path.exists(self.config_path):
            return
        os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
        explicit = self._initial_password_override or os.getenv("HUB_AUTH_PASSWORD")
        password = explicit or self._random_password()
        # 随机口令必须改密；运维显式指定口令的（容器/自动化）不强制，
        # 但可以用 HUB_FORCE_PASSWORD_CHANGE=1 打开。
        force = bool(os.getenv("HUB_FORCE_PASSWORD_CHANGE")) or not explicit
        self._write_initial_config(password, force)

    def enforce_password_policy(self) -> None:
        """启动时检查存量口令：仍是历史弱口令就轮换并吊销会话。

        由 `console.server.run_server()` 显式调用，构造 AuthManager 不产生副作用。
        """
        if os.path.exists(self.config_path):
            self._retire_weak_default()

    def _write_initial_config(self, password: str, force_change: bool = True):
        """写入首启配置：口令 + 强制改密标记。"""
        salt = secrets.token_hex(16)
        now = int(time.time())
        config = {
            "enabled": True,
            "username": self.DEFAULT_USERNAME,
            "password_hash": self._hash_password(password, salt),
            "salt": salt,
            "sessions": {},
            "must_change_password": bool(force_change),
            "created_at": now,
            "updated_at": now,
        }
        if not self.save_config(config):
            print("[AuthManager] 初始化配置失败，请检查 data/ 目录权限")
            return
        self.initial_password = password
        if force_change:
            self._announce_password(password)

    def _announce_password(self, password: str):
        """把首启口令打印一次并落到仅本机可读的文件。"""
        banner = (
            "\n" + "=" * 62 + "\n"
            "  首次启动：已生成控制台管理员随机口令（请立即保存并修改）\n"
            f"  账号：{self.DEFAULT_USERNAME}\n"
            f"  口令：{password}\n"
            f"  同时写入：{self.password_file}\n"
            + "=" * 62 + "\n"
        )
        print(banner, flush=True)
        try:
            os.makedirs(os.path.dirname(self.password_file) or ".", exist_ok=True)
            with open(self.password_file, "w", encoding="utf-8") as handle:
                handle.write(
                    "Stellaris 控制台初始口令（首次登录后必须修改，改完请删除本文件）\n"
                    f"username: {self.DEFAULT_USERNAME}\n"
                    f"password: {password}\n"
                )
            os.chmod(self.password_file, 0o600)
        except Exception as exc:  # 写不了文件也不影响登录，控制台已经打印过
            print(f"[AuthManager] 初始口令文件写入失败: {exc}", flush=True)

    def _retire_weak_default(self):
        """存量配置若仍是历史弱口令，启动时立刻轮换并清空所有会话。"""
        try:
            config = self.load_config()
        except Exception:
            return
        salt = str(config.get("salt", ""))
        stored = str(config.get("password_hash", ""))
        if not stored or not salt:
            return
        if not any(self._verify_hash(weak, salt, stored) for weak in self.WEAK_PASSWORDS):
            return
        candidate = self._initial_password_override or os.getenv("HUB_AUTH_PASSWORD")
        # 显式指定的口令本身仍属弱口令时，宁可换成随机口令，也不保留弱口令
        if not candidate or candidate.lower() in self.WEAK_PASSWORDS:
            candidate = self._random_password()
        password = candidate
        new_salt = secrets.token_hex(16)
        config["salt"] = new_salt
        config["password_hash"] = self._hash_password(password, new_salt)
        config["sessions"] = {}  # 旧会话一并失效
        config["must_change_password"] = True
        config["password_rotated_at"] = int(time.time())
        if not self.save_config(config):
            return
        self.initial_password = password
        print(
            "\n⚠️ 检测到控制台仍在用历史默认口令，已自动轮换并吊销全部旧会话。\n",
            flush=True,
        )
        self._announce_password(password)

    # ---------------- 配置读写 ----------------
    def load_config(self) -> Dict[str, Any]:
        """读取鉴权配置；文件损坏时返回一个“拒绝登录”的空配置。"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {
            "enabled": True,
            "username": self.DEFAULT_USERNAME,
            "password_hash": "",
            "salt": "",
            "sessions": {},
            "must_change_password": True,
        }

    def save_config(self, config: Dict[str, Any]) -> bool:
        """保存鉴权配置"""
        os.makedirs(os.path.dirname(self.config_path) or ".", exist_ok=True)
        try:
            config["updated_at"] = int(time.time())
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(self.config_path, 0o600)
            except Exception:
                pass
            return True
        except Exception:
            return False

    def is_enabled(self) -> bool:
        """是否启用登录保护"""
        cfg = self.load_config()
        return bool(cfg.get("enabled", True))

    def must_change_password(self) -> bool:
        """首次登录（或口令被轮换后）必须先改密。"""
        return bool(self.load_config().get("must_change_password", False))

    # ---------------- 登录与会话 ----------------
    def verify_credentials(self, username: str, password: str) -> bool:
        """校验用户名与密码；老格式哈希校验通过后自动升级。"""
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return True
        if username.strip() != cfg.get("username", self.DEFAULT_USERNAME):
            return False
        salt = cfg.get("salt", "")
        expected_hash = cfg.get("password_hash", "")
        if not self._verify_hash(password, salt, expected_hash):
            return False
        if self._is_legacy_hash(expected_hash):
            self._upgrade_hash(cfg, password)
        return True

    def _upgrade_hash(self, cfg: Dict[str, Any], password: str):
        """把历史 sha256 哈希就地升级成 PBKDF2，会话保持不变。"""
        new_salt = secrets.token_hex(16)
        cfg["salt"] = new_salt
        cfg["password_hash"] = self._hash_password(password, new_salt)
        cfg["hash_upgraded_at"] = int(time.time())
        self.save_config(cfg)

    def create_session(self, username: str, duration_days: int = 7) -> str:
        """创建登录会话 Token"""
        cfg = self.load_config()
        token = secrets.token_hex(32)
        now = int(time.time())
        expires_at = now + (duration_days * 86400)

        sessions = cfg.get("sessions", {})
        # 清理已过期的历史会话
        valid_sessions = {k: v for k, v in sessions.items() if v.get("expires_at", 0) > now}
        # 会话数上限：按创建时间保留最新的若干条
        if len(valid_sessions) >= self.MAX_SESSIONS:
            ordered = sorted(
                valid_sessions.items(),
                key=lambda item: item[1].get("created_at", 0),
                reverse=True,
            )
            valid_sessions = dict(ordered[: self.MAX_SESSIONS - 1])
        valid_sessions[token] = {
            "username": username,
            "created_at": now,
            "expires_at": expires_at,
        }
        cfg["sessions"] = valid_sessions
        self.save_config(cfg)
        return token

    def validate_session(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        """校验 Token 是否处于有效会话期"""
        cfg = self.load_config()
        if not cfg.get("enabled", True):
            return {"username": self.DEFAULT_USERNAME, "admin": True}
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

    def revoke_all_sessions(self) -> int:
        """吊销全部会话，返回被清掉的条数。"""
        cfg = self.load_config()
        count = len(cfg.get("sessions") or {})
        cfg["sessions"] = {}
        self.save_config(cfg)
        return count

    def change_password(self, old_pass: str, new_pass: str) -> Tuple[bool, str]:
        """修改管理员密码；成功后吊销全部旧会话。"""
        cfg = self.load_config()
        if not self.verify_credentials(cfg.get("username", self.DEFAULT_USERNAME), old_pass):
            return False, "原密码校验错误"
        new_pass = str(new_pass or "").strip()
        if len(new_pass) < self.MIN_PASSWORD_LENGTH:
            return False, f"新密码长度至少需要 {self.MIN_PASSWORD_LENGTH} 位"
        if new_pass == str(old_pass or "").strip():
            return False, "新密码不能与原密码相同"
        if new_pass.lower() in self.WEAK_PASSWORDS:
            return False, "新密码过于简单，请换一个"

        cfg = self.load_config()  # 重新读，避免覆盖并发写入的会话
        new_salt = secrets.token_hex(16)
        cfg["salt"] = new_salt
        cfg["password_hash"] = self._hash_password(new_pass, new_salt)
        cfg["must_change_password"] = False
        cfg["password_changed_at"] = int(time.time())
        cfg["sessions"] = {}  # 改密即踢下线，旧 Cookie 立刻失效
        self.save_config(cfg)
        self._remove_password_file()
        return True, "密码已修改，请重新登录"

    def _remove_password_file(self):
        """改完密码就删掉初始口令文件。"""
        try:
            if os.path.exists(self.password_file):
                os.remove(self.password_file)
        except Exception:
            pass

    def get_status(self, token: Optional[str]) -> Dict[str, Any]:
        """获取当前鉴权系统状态与登录信息"""
        session = self.validate_session(token)
        cfg = self.load_config()
        return {
            "auth_enabled": bool(cfg.get("enabled", True)),
            "authenticated": session is not None,
            "username": session.get("username") if session else None,
            "expires_at": session.get("expires_at") if session else None,
            "must_change_password": bool(cfg.get("must_change_password", False)),
        }
