# -*- coding: utf-8 -*-
"""一键把 AstrBot 接到中枢（反向连接器）

背景：中枢是 OpenAI 兼容网关，本来就能被任何客户端调用；但用户必须手改
`cmd_config.json` 才能接上。这个脚本反过来主动配置 AstrBot：

    1. 自动找 AstrBot 的 data 目录（env / 常见路径 / --astrbot-dir）
    2. 检查中枢是否在跑、模型名是什么
    3. 准备中枢客户端 Key（没有 clients.json 时自动生成并落盘）
    4. 把中枢写进 AstrBot 的 provider_sources + provider，并设为默认模型
    5. 初始化 API 安全配置（data/api_security.json，AstrBot 走内网自动豁免签名）
    6. 可选：装 P4 执行器插件、配 mcp_hub_tools.py（--p4）
    7. 可选：重启 AstrBot（--restart，仅 docker 部署）

幂等：重复运行只会更新，不会重复添加。

用法：
    python scripts/connect_astrbot.py --dry-run          # 只看会改什么
    python scripts/connect_astrbot.py                    # 执行
    python scripts/connect_astrbot.py --p4 --restart     # 连 P4 一起装并重启

退出码：0 成功 / 1 有校验失败 / 2 环境不满足
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HUB_NAME = "anan_hub_source"
PROVIDER_NAME = "anan_hub_main"
DEFAULT_PORT = 18895
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_KEY = os.getenv("HUB_API_KEY", "")  # 从环境变量读，不要写死在仓库里

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------
# 基础工具
# ----------------------------------------------------------------------
def log(msg: str, level: str = "info") -> None:
    prefix = {"info": "  ", "ok": "✓ ", "warn": "! ", "err": "✗ "}.get(level, "  ")
    print(f"{prefix}{msg}")


def read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        log(f"读取失败 {path}: {exc}", "err")
        return None


def write_json(path: Path, data: Dict[str, Any]) -> None:
    """原子写，UTF-8 无 BOM（保持 AstrBot 原格式）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def candidate_ips() -> List[str]:
    """列出所有可能的宿主机 IPv4，按"像局域网地址"的顺序排。"""
    found: List[str] = []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetIPAddress -AddressFamily IPv4 | Select-Object -ExpandProperty IPAddress"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        found.extend(x.strip() for x in out.splitlines() if x.strip())
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            found.append(s.getsockname()[0])
    except Exception:
        pass

    def score(ip: str) -> tuple:
        if ip.startswith("192.168."):
            return (0, ip)
        if ip.startswith("10."):
            return (1, ip)
        if ip.startswith("172."):
            return (2, ip)
        if ip.startswith("127.") or ip.startswith("169.254."):
            return (9, ip)
        return (5, ip)

    uniq = []
    for ip in sorted(set(found), key=score):
        if ip and ip not in uniq and not ip.startswith("127.") and not ip.startswith("169.254."):
            uniq.append(ip)
    return uniq


def pick_hub_base(port: int, explicit: str = "") -> Tuple[str, str]:
    """挑一个真正能访问到中枢的地址。返回 (base_url, 说明)。"""
    if explicit:
        return f"http://{explicit}:{port}", "指定地址"
    # 先确认中枢在本机活着
    local_ok, _, _ = probe_hub(f"http://127.0.0.1:{port}/v1")
    if not local_ok:
        return f"http://127.0.0.1:{port}", "本机不可达"
    for ip in candidate_ips():
        ok, _, _ = probe_hub(f"http://{ip}:{port}/v1", timeout=2.5)
        if ok:
            return f"http://{ip}:{port}", f"自动选中 {ip}"
    return f"http://127.0.0.1:{port}", "没找到容器可达的地址"


def http_get(url: str, timeout: float = 5.0) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "anan-connect/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        return 0, str(exc).encode()


# ----------------------------------------------------------------------
# 1. 找 AstrBot
# ----------------------------------------------------------------------
def safe_resolve(path: Path) -> Optional[Path]:
    """resolve() 遇到失效 junction / 断链会抛 FileNotFoundError，这里吞掉。

    Windows 上 D:\\WpSystem、部分应用目录是失效 junction，会让整个扫描中断。
    """
    try:
        return path.resolve()
    except Exception:
        return None


def detect_astrbot_data(explicit: str = "") -> Optional[Path]:
    """返回 AstrBot 的 data 目录（里面有 cmd_config.json 与 plugins/）。"""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_dir() else None

    env = os.getenv("ASTRBOT_DATA_DIR", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p

    roots = [PROJECT_ROOT, PROJECT_ROOT.parent, Path.cwd(), Path.cwd().parent, Path.home()]
    rels = [
        "astrbot/data", "AstrBot/data", "astrbot", "AstrBot",
        "data/astrbot", "app/AstrBot/data",
    ]
    # 容器内路径（在 AstrBot 容器里直接跑时）
    rels.append("/AstrBot/data")

    seen = set()
    for root in roots:
        for rel in rels:
            p = safe_resolve(root / rel)
            if p is None or p in seen:
                continue
            seen.add(p)
            if p.is_dir() and (p / "cmd_config.json").exists():
                return p
    # 兜底：扫项目同级目录，找任何名字含 astrbot 的文件夹下的 data
    for parent in (PROJECT_ROOT, PROJECT_ROOT.parent, Path.home()):
        try:
            for child in parent.iterdir():
                if not child.is_dir():
                    continue
                if "astrbot" not in child.name.lower():
                    continue
                for rel in ("data", "AstrBot/data", "astrbot/data"):
                    cand = safe_resolve(child / rel)
                    if cand is not None and cand.is_dir() and (cand / "cmd_config.json").exists():
                        return cand
        except Exception:
            continue
    # 最后再扫一层：同级目录里的子项目（如 D:\Stellaris_test\astrbot\data）
    for parent in (PROJECT_ROOT.parent,):
        try:
            for child in parent.iterdir():
                if not child.is_dir():
                    continue
                cand = safe_resolve(child / "astrbot" / "data")
                if cand is not None and cand.is_dir() and (cand / "cmd_config.json").exists():
                    return cand
        except Exception:
            continue
    # docker 部署：数据卷通常就在项目下
    for rel in ("astrbot/data",):
        p = (PROJECT_ROOT / rel).resolve()
        if p.is_dir():
            return p
    return None


# ----------------------------------------------------------------------
# 2. 检查中枢
# ----------------------------------------------------------------------
def probe_hub(base: str, timeout: float = 5.0) -> Tuple[bool, str, str]:
    """返回 (可用, 模型名, 说明)。

    "可用"的口径是"能连上并返回 HTTP 响应"，不要求 200：
    有些部署只开放 /v1/chat/completions，/v1/models 会 404，但连接本身是通的。
    """
    status, body = http_get(f"{base.rstrip('/')}/v1/models", timeout=timeout)
    if status == 0:
        return False, DEFAULT_MODEL, f"无响应：{body[:120].decode('utf-8', 'ignore')}"
    if status == 200:
        try:
            data = json.loads(body.decode("utf-8", "ignore"))
            models = data.get("data") or []
            if models:
                return True, str(models[0].get("id") or DEFAULT_MODEL), f"HTTP 200，{len(models)} 个模型"
        except Exception:
            pass
        return True, DEFAULT_MODEL, "HTTP 200"
    # 非 200 但连上了：说明端口是通的
    return True, DEFAULT_MODEL, f"HTTP {status}（连接正常，/v1/models 未开放）"


# ----------------------------------------------------------------------
# 3. 中枢客户端 Key
# ----------------------------------------------------------------------
def ensure_hub_key(clients_path: Path, dry_run: bool = False) -> str:
    """确保 clients.json 里有一个 AstrBot 客户端；返回它的 Key。"""
    data = read_json(clients_path)
    if data is None:
        data = {"require_key": False, "clients": []}
        log("clients.json 不存在，将创建（require_key 保持 false，避免打断现有客户端）", "warn")
    clients = data.setdefault("clients", [])
    for item in clients:
        if not isinstance(item, dict):
            continue
        if item.get("id") == "astrbot":
            keys = item.get("keys") or []
            if keys:
                return str(keys[0])
            item["keys"] = [DEFAULT_KEY]
            if not dry_run:
                write_json(clients_path, data)
            return DEFAULT_KEY
    clients.append({
        "id": "astrbot",
        "name": "AstrBot QQ 通道",
        "keys": [DEFAULT_KEY],
        "channel": "qq",
        "default_actor": "master",
        "allow_recall_memory": True,
        "allow_ingest_timeline": True,
        "rate_limit_per_minute": 120,
        "tool_permissions": {"role": "admin", "allow": [], "deny": []},
    })
    if not dry_run:
        write_json(clients_path, data)
    log(f"已登记客户端 astrbot，Key={DEFAULT_KEY}", "ok")
    return DEFAULT_KEY


# ----------------------------------------------------------------------
# 4. 改 cmd_config.json
# ----------------------------------------------------------------------
def patch_cmd_config(cfg: Dict[str, Any], base_url: str, key: str, model: str) -> Dict[str, Any]:
    """把中枢写进 provider_sources + provider，并设为默认；幂等。"""
    out = copy.deepcopy(cfg)

    sources = out.setdefault("provider_sources", [])
    src = next((s for s in sources if isinstance(s, dict) and s.get("id") == HUB_NAME), None)
    if src is None:
        src = {"id": HUB_NAME, "type": "openai_chat_completion"}
        sources.append(src)
    src["type"] = "openai_chat_completion"
    src["api_base"] = base_url.rstrip("/")
    src["api_key"] = [key]
    src["key"] = [key]

    providers = out.setdefault("provider", [])
    prov = next((p for p in providers if isinstance(p, dict) and p.get("id") == PROVIDER_NAME), None)
    if prov is None:
        prov = {"id": PROVIDER_NAME}
        providers.append(prov)
    prov.update({
        "enable": True,
        "model": model,
        "provider_source_id": HUB_NAME,
        "modalities": [],
        "custom_extra_body": {},
    })

    settings = out.setdefault("provider_settings", {})
    # 记忆检索要等整轮回复才能查库重答，流式下做不了，接入时统一关掉
    settings["streaming_response"] = False
    pool = settings.get("provider_pool")
    if not isinstance(pool, list) or not pool:
        settings["provider_pool"] = [PROVIDER_NAME]
    elif PROVIDER_NAME not in pool:
        pool.append(PROVIDER_NAME)

    agent = out.setdefault("agent_runner", {})
    agent.setdefault("runner_type", "local")
    agent_cfg = agent.setdefault("config", {})
    model_cfg = agent_cfg.setdefault("model", {})
    model_cfg["provider_id"] = PROVIDER_NAME

    return out


# ----------------------------------------------------------------------
# 5. P4 可选安装
# ----------------------------------------------------------------------
PROXY_PORTS = (18891, 53353, 443, 18890)


def detect_reverse_proxy(ports=PROXY_PORTS, host: str = "127.0.0.1", timeout: float = 0.3) -> Optional[int]:
    """看本机有没有反代在监听；返回命中的端口，没有返回 None。"""
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            if sock.connect_ex((host, int(port))) == 0:
                return int(port)
        finally:
            sock.close()
    return None


def report_deployment_mode(base_url: str) -> List[str]:
    """没有反代就提醒：要么绑回环，要么限制来源网段。"""
    from urllib.parse import urlparse

    proxy_port = detect_reverse_proxy()
    host = urlparse(base_url).hostname or ""
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if proxy_port:
        return [f"检测到反代端口 {proxy_port}，保持 0.0.0.0（TLS 由反代负责）"]
    if loopback:
        return ["没有反代且 AstrBot 走本机回环：建议 HUB_BIND=127.0.0.1，控制台只给本机用"]
    return [
        "没有检测到反向代理：18895 是明文，AstrBot 又需要局域网可访问",
        "建议执行：python scripts/setup_security.py --enable-local-only --lan",
        "（只给本机用时，把 HUB_BIND 设成 127.0.0.1）",
    ]


def ensure_security_config() -> List[str]:
    """初始化 API 安全配置：没有就建一份（密钥先生成好，签名默认关）。

    AstrBot 走内网（docker / 局域网），默认豁免签名，所以这里不需要改它任何配置。
    """
    try:
        for extra in (str(PROJECT_ROOT), str(Path(__file__).resolve().parent)):
            if extra not in sys.path:
                sys.path.insert(0, extra)
        from agent_core import api_security

        if api_security.config_path().exists():
            return ["API 安全配置已存在，未改动"]
        import setup_security

        config = setup_security.apply(generate_secret=True)
        state = "开" if config["signature"]["enabled"] else "关"
        return [f"已生成 {api_security.config_path().name}（签名{state}，密钥已就绪）"]
    except Exception as exc:
        return [f"API 安全配置跳过：{type(exc).__name__}: {exc}"]


def install_p4(data_dir: Path, base_url: str, key: str) -> List[str]:
    """把薄壳 MCP + 执行器插件装进 AstrBot 的 data 目录；返回改动清单。"""
    changed: List[str] = []

    # 5.1 薄壳 MCP 服务脚本
    src_script = PROJECT_ROOT / "console" / "mcp_hub_tools.py"
    dst_script = data_dir / "mcp_hub_tools.py"
    if src_script.exists():
        shutil.copy2(src_script, dst_script)
        changed.append(f"复制 {dst_script}")

    # 5.2 执行器插件
    src_plugin = PROJECT_ROOT / "astrbot_plugins" / "astrbot_plugin_hub_tool_executor"
    dst_plugin = data_dir / "plugins" / "astrbot_plugin_hub_tool_executor"
    if src_plugin.is_dir():
        dst_plugin.mkdir(parents=True, exist_ok=True)
        for item in src_plugin.iterdir():
            if item.name == "__pycache__":
                continue
            if item.is_file():
                shutil.copy2(item, dst_plugin / item.name)
        changed.append(f"复制插件 {dst_plugin}")

    # 5.3 mcp_server.json 增加 hub-tools
    mcp_path = data_dir / "mcp_server.json"
    mcp = read_json(mcp_path) or {"mcpServers": {}}
    servers = mcp.setdefault("mcpServers", {})
    servers["hub-tools"] = {
        "command": "python",
        "args": ["/AstrBot/data/mcp_hub_tools.py"],
        "env": {
            "YDC_API_KEY": os.getenv("YDC_API_KEY", ""),
            "HUB_CLEAN_URL": f"{base_url.rstrip('/')}/api/tools/clean",
            "HUB_KEY": key,
            "HTTP_PROXY": os.getenv("ASTRBOT_HTTP_PROXY", "http://192.168.65.254:7897"),
            "HTTPS_PROXY": os.getenv("ASTRBOT_HTTP_PROXY", "http://192.168.65.254:7897"),
            "NO_PROXY": "localhost,127.0.0.1,192.168.*",
        },
    }
    write_json(mcp_path, mcp)
    changed.append(f"更新 {mcp_path}（新增 hub-tools）")

    # 5.4 cmd_config.json 的 plugin_set
    cfg_path = data_dir / "cmd_config.json"
    cfg = read_json(cfg_path)
    if cfg is not None:
        plugin_set = cfg.setdefault("plugin_set", [])
        if "astrbot_plugin_hub_tool_executor" not in plugin_set:
            plugin_set.append("astrbot_plugin_hub_tool_executor")
            write_json(cfg_path, cfg)
            changed.append("cmd_config.json：启用 astrbot_plugin_hub_tool_executor")

    return changed


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="把 AstrBot 一键接到助手中枢")
    parser.add_argument("--astrbot-dir", default="", help="AstrBot data 目录（默认自动查找）")
    parser.add_argument("--hub", default="", help="中枢地址，默认自动探测局域网 IP")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--model", default="", help="写入 AstrBot 的模型名，默认取中枢 /v1/models")
    parser.add_argument("--p4", action="store_true", help="同时安装 P4 薄壳 MCP + 执行器插件")
    parser.add_argument("--restart", action="store_true", help="改完后重启 AstrBot 容器（docker）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的改动")
    args = parser.parse_args()

    print("=" * 60)
    print(" 助手中枢 × AstrBot 一键连接")
    print("=" * 60)

    # 1) 中枢地址（逐个候选 IP 实测）
    base, why = pick_hub_base(args.port, args.hub)
    base_url = f"{base}/v1"
    log(f"中枢地址：{base_url}（{why}）")
    ok, model, detail = probe_hub(base_url)
    if not ok:
        log(f"中枢不可达（{detail}）", "err")
        log("请先启动中枢：python console/server.py 18895", "warn")
        return 2
    model = args.model or model or DEFAULT_MODEL
    log(f"中枢在线：{detail}；模型 {model}", "ok")

    # 2) AstrBot 目录
    data_dir = detect_astrbot_data(args.astrbot_dir)
    if data_dir is None:
        log("没找到 AstrBot 的 data 目录", "err")
        log("请用 --astrbot-dir 指定（通常是 ...\\AstrBot\\data 或容器挂载的 astrbot/data）", "warn")
        return 2
    log(f"AstrBot data：{data_dir}", "ok")

    cfg_path = data_dir / "cmd_config.json"
    cfg = read_json(cfg_path)
    if cfg is None:
        log(f"{cfg_path} 不可读，无法继续", "err")
        return 2

    # 3) Key
    clients_path = PROJECT_ROOT / "data" / "clients.json"
    key = ensure_hub_key(clients_path, dry_run=args.dry_run)
    log(f"中枢客户端 Key：{key}")

    # 4) 备份 + 改 cmd_config
    if not args.dry_run:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = cfg_path.with_name(f"cmd_config.json.bak_connect_{stamp}")
        shutil.copy2(cfg_path, backup)
        log(f"已备份 {backup.name}")

    new_cfg = patch_cmd_config(cfg, base_url, key, model)
    changed = [
        f"provider_sources += {HUB_NAME}（{base_url}）",
        f"provider += {PROVIDER_NAME}（model={model}）",
        f"agent_runner.config.model.provider_id = {PROVIDER_NAME}",
    ]
    if not args.dry_run:
        write_json(cfg_path, new_cfg)
        log("已写入 cmd_config.json", "ok")

    # 5) P4
    if args.p4:
        if args.dry_run:
            changed.append("P4：将复制 mcp_hub_tools.py + 执行器插件，并更新 mcp_server.json / plugin_set")
        else:
            p4_changes = install_p4(data_dir, base_url, key)
            changed.extend(f"P4：{c}" for c in p4_changes)
            log("P4 组件已安装", "ok")

    # 5.5) API 安全配置：初始化时建好，AstrBot 内网自动豁免，不用改客户端
    if not args.dry_run:
        changed.extend(f"安全：{item}" for item in ensure_security_config())
        log("API 安全配置就绪", "ok")
        for line in report_deployment_mode(base_url):
            log(line, "warn" if "建议" in line or "没有检测到" in line else "info")

    # 6) 重启
    if args.restart and not args.dry_run:
        log("重启 AstrBot 容器 ...")
        res = subprocess.run(["docker", "restart", "astrbot"], capture_output=True, text=True)
        if res.returncode == 0:
            log("已重启，等待 30 秒让 provider 与插件加载 ...")
            time.sleep(30)
            log("完成，去 QQ 里发条消息试试", "ok")
        else:
            log(f"重启失败：{res.stderr.strip()[:200]}", "warn")
            log("请手动重启 AstrBot 后生效", "warn")

    print("\n改动清单：")
    for item in changed:
        print(f"  - {item}")

    if not args.restart and not args.dry_run:
        print("\n下一步：重启 AstrBot 让配置生效")
        print("  docker restart astrbot")
        print("  或 WebUI → 配置 → 服务提供商 里确认 anan_hub_main 已启用")

    return 0


if __name__ == "__main__":
    sys.exit(main())
