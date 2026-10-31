"""初始化安全配置：一条命令准备好 local_only / 签名，AstrBot 不用改任何东西。

用法：
    python scripts/setup_security.py --show
    python scripts/setup_security.py --generate-secret
    python scripts/setup_security.py --enable-signature --generate-secret
    python scripts/setup_security.py --enable-local-only --lan
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core import api_security  # noqa: E402

LAN_CIDRS = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]


def apply(
    *,
    enable_local_only: bool = False,
    disable_local_only: bool = False,
    enable_signature: bool = False,
    disable_signature: bool = False,
    generate_secret: bool = False,
    secret: str = "",
    lan: bool = False,
    window_seconds: int = 0,
) -> Dict[str, Any]:
    """按参数合并写入配置，返回保存后的配置。"""
    patch: Dict[str, Any] = {}
    if enable_local_only or disable_local_only:
        patch["local_only"] = bool(enable_local_only)
    if lan:
        patch["allow_cidrs"] = sorted(
            set(api_security.load_config()["allow_cidrs"]) | set(LAN_CIDRS)
        )
    signature: Dict[str, Any] = {}
    if enable_signature or disable_signature:
        signature["enabled"] = bool(enable_signature)
    if window_seconds:
        signature["window_seconds"] = window_seconds
    if generate_secret:
        signature["secret"] = secrets.token_hex(32)
    if secret:
        signature["secret"] = secret
    if signature:
        patch["signature"] = signature
    if patch:
        return api_security.save_config(patch)
    return api_security.load_config()


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 / 调整 API 安全配置")
    parser.add_argument("--show", action="store_true", help="只打印当前配置")
    parser.add_argument("--enable-local-only", action="store_true", help="只允许白名单来源访问")
    parser.add_argument("--disable-local-only", action="store_true", help="关闭本机限制")
    parser.add_argument("--enable-signature", action="store_true", help="开启 /v1 与 /hub/v1 签名校验")
    parser.add_argument("--disable-signature", action="store_true", help="关闭签名校验")
    parser.add_argument("--generate-secret", action="store_true", help="生成随机密钥写入配置")
    parser.add_argument("--secret", default="", help="直接指定密钥")
    parser.add_argument("--window-seconds", type=int, default=0, help="签名时间窗口")
    parser.add_argument("--lan", action="store_true", help="把局域网 / docker 网段加入放行列表")
    parser.add_argument("--rotate-secret", action="store_true", help="轮换签名密钥（旧密钥留过渡期）")
    parser.add_argument("--grace-minutes", type=int, default=30, help="旧密钥过渡分钟数，默认 30")
    args = parser.parse_args()

    if args.show:
        print(json.dumps(api_security.status(), ensure_ascii=False, indent=2))
        return 0

    if args.rotate_secret:
        saved = api_security.rotate_secret(max(60, args.grace_minutes * 60))
        print(f"密钥已轮换：{api_security.config_path()}")
        print(f"新密钥（只显示这一次）：{saved['signature']['secret']}")
        print(f"旧密钥保留 {args.grace_minutes} 分钟")
        return 0

    config = apply(
        enable_local_only=args.enable_local_only,
        disable_local_only=args.disable_local_only,
        enable_signature=args.enable_signature,
        disable_signature=args.disable_signature,
        generate_secret=args.generate_secret,
        secret=args.secret,
        lan=args.lan,
        window_seconds=args.window_seconds,
    )
    status = api_security.status()
    print(f"配置已写入：{api_security.config_path()}")
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if config["signature"]["enabled"]:
        print("\n提示：内网来源（本机 / docker / 局域网）已默认豁免签名，AstrBot 无需改动；")
        print("      外部客户端需按 docs/API_SECURITY.md 的算法加 X-Timestamp + X-Signature。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
