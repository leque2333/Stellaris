import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

# 适配 Windows pythonw.exe 无窗运行环境（重定向标准输入输出至日志文件）
BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "bridge_server.log"
if sys.stdout is None or sys.stderr is None:
    _f = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _f
    if sys.stderr is None:
        sys.stderr = _f

import psutil
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# 设置日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AntigravityBridge")

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "bridge_config.json"
ASTRBOT_CMD_CONFIG = Path(r"D:\机器人\astrbot\data\cmd_config.json")

# 加载配置
def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取 bridge_config.json 失败: {e}")
    return {
        "host": "0.0.0.0",
        "port": 8765,
        "admin_id": "1000000001",
        "secret_token": "anan_agy_bridge_sec_8f7b2c9e1a4d",
        "model": "gemini-3.8-flash",
        "command_timeout": 60,
        "data_temp_dir": r"D:\机器人\astrbot\data\temp",
    }

CONFIG = load_config()
ADMIN_ID = str(CONFIG.get("admin_id", "1000000001"))
SECRET_TOKEN = CONFIG.get("secret_token", "")
MODEL_NAME = CONFIG.get("model", "gemini-3.8-flash")
TEMP_DIR = Path(CONFIG.get("data_temp_dir", r"D:\机器人\astrbot\data\temp"))
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 动态提取 Gemini API 密钥
def get_gemini_api_key() -> Optional[str]:
    # 优先环境变量
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    # 从 AstrBot 配置自动提取
    if ASTRBOT_CMD_CONFIG.exists():
        try:
            with open(ASTRBOT_CMD_CONFIG, "r", encoding="utf-8") as f:
                d = json.load(f)
            for s in d.get("provider_sources", []):
                if s.get("id") == "gemini_main_source":
                    k = s.get("api_key") or s.get("key")
                    if isinstance(k, list) and k:
                        return k[0]
                    if isinstance(k, str) and k:
                        return k
        except Exception as e:
            logger.warning(f"从 cmd_config.json 读取 Gemini 密钥失败: {e}")
    return None

# 系统状态收集器（纯本地毫秒级，0 Token，0 成本）
def get_system_status() -> dict:
    cpu_percent = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
            disks.append({
                "device": part.device,
                "mountpoint": part.mountpoint,
                "total_gb": round(usage.total / (1024**3), 1),
                "used_gb": round(usage.used / (1024**3), 1),
                "free_gb": round(usage.free / (1024**3), 1),
                "percent": usage.percent,
            })
        except Exception:
            pass

    # 获取内存占用前 5 的进程
    top_procs = []
    try:
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent", "cpu_percent"]):
            try:
                procs.append(p.info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        procs.sort(key=lambda x: x.get("memory_percent") or 0, reverse=True)
        top_procs = [
            f"{p['name']}(PID:{p['pid']}, 内存:{p['memory_percent']:.1f}%)"
            for p in procs[:5]
        ]
    except Exception as e:
        logger.warning(f"获取进程信息失败: {e}")

    # 尝试获取显卡信息
    gpu_info = None
    try:
        smi = shutil.which("nvidia-smi")
        if smi:
            res = subprocess.run(
                [smi, "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                parts = [p.strip() for p in res.stdout.strip().split(",")]
                if len(parts) >= 5:
                    gpu_info = {
                        "name": parts[0],
                        "temp_c": parts[1],
                        "util_percent": parts[2],
                        "mem_used_mb": parts[3],
                        "mem_total_mb": parts[4],
                    }
    except Exception:
        pass

    return {
        "cpu_percent": cpu_percent,
        "memory_percent": mem.percent,
        "memory_used_gb": round(mem.used / (1024**3), 1),
        "memory_total_gb": round(mem.total / (1024**3), 1),
        "disks": disks,
        "top_processes": top_procs,
        "gpu": gpu_info,
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(psutil.boot_time())),
    }

# 屏幕截图捕获器
def capture_screenshot() -> tuple[bool, str]:
    save_filename = f"screenshot_{int(time.time())}.jpg"
    save_path = TEMP_DIR / save_filename
    
    # 方式 1: PIL ImageGrab
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab()
        im.save(save_path, "JPEG", quality=85)
        logger.info(f"PIL 截图成功: {save_path}")
        return True, save_filename
    except Exception as e:
        logger.warning(f"PIL ImageGrab 截图失败: {e}，尝试 PowerShell GDI 方式...")

    # 方式 2: PowerShell GDI
    ps_cmd = f"""
    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bmp)
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bmp.Save('{str(save_path).replace("\\", "\\\\")}', [System.Drawing.Imaging.ImageFormat]::Jpeg)
    $graphics.Dispose()
    $bmp.Dispose()
    """
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if save_path.exists() and save_path.stat().st_size > 0:
            logger.info(f"PowerShell 截图成功: {save_path}")
            return True, save_filename
    except Exception as e:
        logger.error(f"PowerShell 截图异常: {e}")

    return False, "当前处于无显示输出环境或锁屏状态，未能成功抓取桌面画面。"

# 异步 PowerShell 执行器
async def run_powershell(command: str, timeout: int = 60) -> tuple[int, str, str]:
    logger.info(f"执行 PowerShell 指令: {command}")
    proc = await asyncio.create_subprocess_shell(
        f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "{command}"',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout_bytes.decode("utf-8", errors="replace").strip()
        err = stderr_bytes.decode("utf-8", errors="replace").strip()
        return proc.returncode or 0, out, err
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return -1, "", f"命令执行超时（超过 {timeout} 秒）已强制中止。"

# 智能 Antigravity Agent 执行器
async def run_antigravity_agent(instruction: str) -> str:
    api_key = get_gemini_api_key()
    if not api_key:
        logger.warning("未检测到 Gemini API Key，降级为常规指令处理。")
        code, out, err = await run_powershell(instruction)
        return out if code == 0 else f"执行失败:\n{err or out}"

    try:
        from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig
        
        system_instructions = (
            "你是反重力（Antigravity）驻留在 Windows 宿主机上的本地智能执行助手。"
            "助手（主人的 AI 伴侣）转交了主人对电脑的操作需求。"
            "请使用你的系统与终端能力安全、准确地完成该需求，并在最后用简洁明了的技术结论向助手汇报执行结果。"
            "避免啰嗦，重点输出执行状态、关键数据或遇到的错误。"
        )

        config = LocalAgentConfig(
            system_instructions=system_instructions,
            capabilities=CapabilitiesConfig(),
            api_key=api_key,
            model=MODEL_NAME,
        )

        logger.info(f"启动 Antigravity Agent 处理自然语言任务: {instruction}")
        async with Agent(config) as agent:
            response = await agent.chat(instruction)
            tokens = []
            async for token in response:
                tokens.append(token)
            result = "".join(tokens).strip()
            return result
    except Exception as e:
        logger.error(f"Antigravity Agent 执行异常: {e}，尝试本地执行...")
        code, out, err = await run_powershell(instruction)
        if code == 0 and out:
            return out
        return f"反重力智能执行时出错: {e}"

# FastAPI 应用
app = FastAPI(title="Antigravity Host Bridge", version="1.0.0")

class ExecuteRequest(BaseModel):
    admin_id: str
    action: Optional[str] = "auto" # auto, powershell, status, screenshot, lock
    command: Optional[str] = ""
    instruction: Optional[str] = ""
    timeout: Optional[int] = 60

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "Antigravity Bridge",
        "os": "Windows",
        "user": os.environ.get("USERNAME", "administrator"),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

@app.get("/status")
async def status_endpoint(x_bridge_token: Optional[str] = Header(None)):
    if SECRET_TOKEN and x_bridge_token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Unauthorized")
    return get_system_status()

@app.post("/execute")
async def execute_endpoint(req: ExecuteRequest, x_bridge_token: Optional[str] = Header(None)):
    # 鉴权
    if SECRET_TOKEN and x_bridge_token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Token 鉴权失败")
    if str(req.admin_id).strip() != ADMIN_ID:
        raise HTTPException(status_code=403, detail="仅限主人本人操作电脑")

    action = (req.action or "auto").lower().strip()
    command = (req.command or "").strip()
    instruction = (req.instruction or "").strip() or command

    # 1. 查询系统状态（0 Token 毫秒级）
    if action == "status" or instruction in ("状态", "系统状态", "电脑状态", "电脑怎么样了", "查看状态"):
        status_data = get_system_status()
        text = (
            f"💻 电脑运行状态：\n"
            f"• CPU 负载：{status_data['cpu_percent']}%\n"
            f"• 内存占用：{status_data['memory_used_gb']}G / {status_data['memory_total_gb']}G ({status_data['memory_percent']}%)\n"
        )
        if status_data.get("gpu"):
            g = status_data["gpu"]
            text += f"• 显卡状态：{g['name']} | 温度 {g['temp_c']}°C | 占用 {g['util_percent']}% | 显存 {g['mem_used_mb']}M/{g['mem_total_mb']}M\n"
        
        text += "• 磁盘剩余：\n"
        for d in status_data["disks"]:
            text += f"  - {d['mountpoint']} 剩余 {d['free_gb']}G ({100 - d['percent']:.1f}% 可用)\n"

        if status_data["top_processes"]:
            text += "• 高内存应用：\n  " + "\n  ".join(status_data["top_processes"])

        return {
            "success": True,
            "type": "text",
            "message": text.strip(),
            "data": status_data,
        }

    # 2. 屏幕截图（0 Token 毫秒级）
    if action == "screenshot" or instruction in ("截屏", "截图", "看屏幕", "屏幕截图", "看看屏幕"):
        ok, res_path_or_msg = capture_screenshot()
        if ok:
            return {
                "success": True,
                "type": "image",
                "filename": res_path_or_msg,
                "file_path": str(TEMP_DIR / res_path_or_msg),
                "container_path": f"/AstrBot/data/temp/{res_path_or_msg}",
                "message": "屏幕截屏已生成~",
            }
        else:
            return {
                "success": False,
                "type": "text",
                "message": f"截图未能成功：{res_path_or_msg}",
            }

    # 3. 锁定屏幕（0 Token 毫秒级）
    if action == "lock" or instruction in ("锁屏", "锁定电脑", "锁住电脑"):
        subprocess.run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return {
            "success": True,
            "type": "text",
            "message": "已将电脑立即锁定~",
        }

    # 4. 显式 PowerShell / 命令执行
    if action == "powershell" or command:
        cmd_to_run = command or instruction
        code, out, err = await run_powershell(cmd_to_run, timeout=req.timeout or 60)
        output = out if out else (err if err else "[无输出]")
        return {
            "success": (code == 0),
            "type": "text",
            "returncode": code,
            "message": output,
        }

    # 5. 复杂自然语言意图：调用本地 Antigravity Agent
    agent_output = await run_antigravity_agent(instruction)
    return {
        "success": True,
        "type": "text",
        "message": agent_output,
    }

if __name__ == "__main__":
    host = CONFIG.get("host", "0.0.0.0")
    port = int(CONFIG.get("port", 8765))
    logger.info(f"启动 Antigravity 宿主机桥梁服务于 http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
