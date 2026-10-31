# -*- coding: utf-8 -*-
"""提交前自检：源码、文档、测试里不能出现真实账号和 Key。

用作 pre-commit hook，也可以手动跑：

    python scripts/check_no_secrets.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    "data", "backups", "logs", "__pycache__", ".git",
    "node_modules", "archive", ".venv", "venv",
}

# 真实账号与 Key 的特征
PATTERNS = [
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub PAT"),
    (re.compile(r"hub-sk-[a-z0-9]{8,}"), "中枢 API Key"),
    # 只抓"把号码直接赋给身份常量/字段"的写法，避免版本号、时间戳误报
    (re.compile(r"""(MASTER_QQ|BOT_QQ|ADMIN_QQ|admin_qq|master_qq|qq|uin)\s*[=:]\s*["']?\d{5,12}["']?"""), "硬编码账号"),
    (re.compile(r"""(admins_id|id_whitelist)\s*[=:]\s*\[\s*["']\d{5,12}["']"""), "硬编码账号"),
]

ALLOW_EXTS = {".py", ".js", ".html", ".css", ".md", ".json", ".yaml", ".yml", ".bat", ".sh", ".toml"}

# 这些行允许出现数字（版本号、端口、时间戳）
ALLOW_LINE_HINTS = (
    "18895", "18890", "18891", "6185", "6099", "9877", "6199",
    "version", "Version", "port", "PORT", "timeout", "max_tokens",
    "http://", "https://", "2026-", "0x", "sha", "commit",
)


def scan() -> list[tuple[str, int, str, str]]:
    problems = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in ALLOW_EXTS:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        rel = str(path.relative_to(ROOT))
        # 测试目录只查 Key，不查号码（测试用假号，但避免误报）
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(h in line for h in ALLOW_LINE_HINTS):
                continue
            for pattern, label in PATTERNS:
                # 测试本来就要用号码（假号），只拦 Key
                if label == "硬编码账号" and rel.startswith(("tests/", "tests\\")):
                    continue
                if pattern.search(line):
                    problems.append((rel, lineno, label, line.strip()[:100]))
                    break
    return problems


def main() -> int:
    problems = scan()
    if not problems:
        print("OK 未发现真实账号或 Key")
        return 0
    print(f"发现 {len(problems)} 处可疑内容：\n")
    for rel, lineno, label, snippet in problems:
        print(f"  {rel}:{lineno}  [{label}]")
        print(f"    {snippet}")
    print("\n真实值请放到 data/ 下（已被 .gitignore 忽略），源码里用占位符。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
