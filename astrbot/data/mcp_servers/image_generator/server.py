import base64
import json
import os
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime

from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("image-generation-mcp")

# 数据持久化目录与配额文件
DATA_DIR = "/AstrBot/data/mcp_servers/image_generator"
QUOTA_FILE = os.path.join(DATA_DIR, "quota_store.json")
CACHE_DIR = "/AstrBot/data/temp/generated_images"

# Google Gemini API 配置
GEMINI_KEY = os.environ.get(
    "GEMINI_API_KEY", "AQ.Ab8RN6LZkWDrkP1sbw3JkFSA5QgS2ywY100t9hXBYs_6v-27yQ"
)
HTTP_PROXY = os.environ.get("HTTP_PROXY", "http://host.docker.internal:7897")
MONTHLY_BANANA_QUOTA = 3  # 每人每月 3 张高级配额


class QuotaManager:

  def __init__(self, filepath: str):
    self.filepath = filepath
    self._ensure_loaded()

  def _ensure_loaded(self):
    os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
    if os.path.exists(self.filepath):
      try:
        with open(self.filepath, "r", encoding="utf-8") as f:
          self.data = json.load(f)
      except Exception:
        self.data = {}
    else:
      self.data = {}

  def _save(self):
    try:
      with open(self.filepath, "w", encoding="utf-8") as f:
        json.dump(self.data, f, ensure_ascii=False, indent=2)
    except Exception as e:
      print(f"Error saving quota: {e}", file=sys.stderr)

  def get_current_month(self) -> str:
    return datetime.now().strftime("%Y-%m")

  def get_quota_status(self, user_id: str) -> dict:
    self._ensure_loaded()
    current_month = self.get_current_month()
    user_str = str(user_id or "default_user").strip()

    user_info = self.data.get(user_str, {})
    if user_info.get("month") != current_month:
      # 新月份自动重置
      used = 0
    else:
      used = user_info.get("used", 0)

    remaining = max(0, MONTHLY_BANANA_QUOTA - used)
    return {
        "user_id": user_str,
        "month": current_month,
        "used": used,
        "total": MONTHLY_BANANA_QUOTA,
        "remaining": remaining,
        "can_use_banana": remaining > 0,
    }

  def consume_banana(self, user_id: str):
    self._ensure_loaded()
    current_month = self.get_current_month()
    user_str = str(user_id or "default_user").strip()

    status = self.get_quota_status(user_str)
    used = status["used"] + 1
    self.data[user_str] = {
        "month": current_month,
        "used": used,
        "last_used_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    self._save()


quota_mgr = QuotaManager(QUOTA_FILE)


def generate_with_google_banana(prompt: str) -> bytes:
  """使用 Google Nano Banana 原生生图架构生成图片"""
  models = [
      "gemini-3.1-flash-image",
      "gemini-2.5-flash-image",
      "gemini-nano-banana-2.1",
  ]
  url_base = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}"

  payload = json.dumps({
      "contents": [{"parts": [{"text": prompt}]}]
  }).encode("utf-8")

  opener = urllib.request.build_opener()
  if HTTP_PROXY:
    proxy_handler = urllib.request.ProxyHandler(
        {"http": HTTP_PROXY, "https": HTTP_PROXY}
    )
    opener = urllib.request.build_opener(proxy_handler)

  last_error = None
  for model in models:
    api_url = url_base.format(model, GEMINI_KEY)
    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
      with opener.open(req, timeout=40) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))

      parts = res_data.get("candidates", [{}])[0].get("content", {}).get(
          "parts", []
      )
      for part in parts:
        if "inlineData" in part:
          img_b64 = part["inlineData"]["data"]
          return base64.b64decode(img_b64)
      raise ValueError(f"No inlineData found in response from {model}")
    except Exception as e:
      last_error = e
      continue

  raise RuntimeError(f"All Google Banana models failed: {last_error}")


def generate_with_flux_free(
    prompt: str, width: int, height: int, seed: int
) -> tuple[bytes, str]:
  """使用完全免费的 Flux.1 引擎出图"""
  if seed < 0:
    seed = random.randint(1, 99999999)

  encoded_prompt = urllib.parse.quote(prompt.strip())
  url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&model=flux&seed={seed}&nologo=true"

  headers = {
      "User-Agent": (
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      )
  }
  opener = urllib.request.build_opener()
  if HTTP_PROXY:
    proxy_handler = urllib.request.ProxyHandler(
        {"http": HTTP_PROXY, "https": HTTP_PROXY}
    )
    opener = urllib.request.build_opener(proxy_handler)

  req = urllib.request.Request(url, headers=headers)
  with opener.open(req, timeout=35) as resp:
    img_data = resp.read()

  return img_data, url


@mcp.tool(
    name="generate_image",
    description="智能生成高质量图片。每位用户每月享有 3 张 Google Nano Banana 高级原生画质配额，额度用尽后自动切换至免费高速 Flux 引擎，保证随时可用。",
)
def generate_image(
    prompt: str,
    user_id: str = "default_user",
    width: int = 1024,
    height: int = 1024,
    seed: int = -1,
) -> list:
  """生成图片接口。

  Args:
      prompt: 图像描述提示词。建议扩充画面细节与质感修饰。
      user_id: 用户身份标识（如 QQ 号），用于精准计算当月每人 3 张的高级额度。
      width: 图像宽度，默认 1024。
      height: 图像高度，默认 1024。
      seed: 随机种子，-1 为随机生成。

  Returns:
      包含生图引擎来源说明、配额消耗情况与图像文件的列表。
  """
  os.makedirs(CACHE_DIR, exist_ok=True)
  status = quota_mgr.get_quota_status(user_id)
  engine_used = ""
  quota_notice = ""
  img_data = None
  web_url = ""

  # 1. 如果用户当月还有 Google Banana 额度，优先尝试高级引擎
  if status["can_use_banana"]:
    try:
      img_data = generate_with_google_banana(prompt)
      quota_mgr.consume_banana(user_id)
      remaining = status["remaining"] - 1
      engine_used = "🍌 Google Nano Banana (原生高级旗舰引擎)"
      quota_notice = f"✨ **已消耗 1 次高级配额**（您本月剩余高级额度：`{remaining} / {MONTHLY_BANANA_QUOTA}` 张）"
    except Exception as e:
      print(f"Google Banana fallback triggered: {e}", file=sys.stderr)
      # Google API 429 或未开通计费时无损降级，不扣额度
      engine_used = "🚀 Flux.1 (免费极速引擎 - 因高级通道繁忙自动回退)"
      quota_notice = (
          f"💡 高级通道配额暂未就绪，已为您**无损切换至免费 Flux 引擎**（不扣除您本月剩余的"
          f" `{status['remaining']}` 次高级配额）！"
      )

  # 2. 如果额度已用尽或触发了回退，使用免费的高清 Flux 引擎
  if img_data is None:
    if not status["can_use_banana"]:
      engine_used = "🌿 Flux.1 (免费高速引擎)"
      quota_notice = (
          f"💡 您本月的高级配额已用完（`{MONTHLY_BANANA_QUOTA} /"
          f" {MONTHLY_BANANA_QUOTA}`"
          " 张），已为您自动切换至**免费且不限量的 Flux 引擎**出图！"
      )

    img_data, web_url = generate_with_flux_free(prompt, width, height, seed)

  # 3. 本地持久化缓存
  file_name = f"gen_{int(time.time())}_{random.randint(1000, 9999)}.jpg"
  file_path = os.path.join(CACHE_DIR, file_name)
  with open(file_path, "wb") as f:
    f.write(img_data)

  markdown_text = (
      f"🎨 **图片绘制成功！**\n"
      f"- **所用引擎**: {engine_used}\n"
      f"- **配额状态**: {quota_notice}\n"
      f"- **生图描述**: {prompt}\n"
  )
  if web_url:
    markdown_text += f"\n![Image]({web_url})\n"

  return [markdown_text, Image(data=img_data, format="jpeg")]


@mcp.tool(
    name="check_image_quota",
    description="查询指定用户本月剩余的 Google Nano Banana 高级生图额度及使用详情。",
)
def check_image_quota(user_id: str = "default_user") -> str:
  """查询生图配额。"""
  status = quota_mgr.get_quota_status(user_id)
  return (
      f"📊 **您的生图配额报告（{status['month']} 月）**\n"
      f"- 本月高级配额总量: **{status['total']}** 张\n"
      f"- 已使用高级配额: **{status['used']}** 张\n"
      f"- 剩余高级配额: **{status['remaining']}** 张\n"
      f"- 免费 Flux 引擎额度: **不限次数（免费无限用）**\n\n"
      f"💡 每月 1 号会自动刷新 3 张高级配额；用完后不影响生图，将无缝使用免费高清引擎。"
  )


if __name__ == "__main__":
  mcp.run()
