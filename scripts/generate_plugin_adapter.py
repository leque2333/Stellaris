# -*- coding: utf-8 -*-
"""用执行 AI 为一个新插件生成适配器（默认只打印提示词，不调用模型）。

用法：
    python scripts/generate_plugin_adapter.py --plugin-dir <dir>
    python scripts/generate_plugin_adapter.py --plugin-dir <dir> --run

生成物写到 console/adapters/generated/<plugin_id>.py，ENABLED=False；
人工 review 后再打开，避免未审查代码自动进入网关。
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
GENERATED = ROOT / "console" / "adapters" / "generated"
CONTRACT = ROOT / "docs" / "PLUGIN_ADAPTERS.md"
EXISTING = ROOT / "console" / "adapters" / "astrbot.py"
FORBIDDEN_IMPORTS = {"subprocess", "socket", "requests", "urllib", "http.client"}
MAX_FILE_CHARS = 12000


def _read(path: Path, limit: int = MAX_FILE_CHARS) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def _plugin_files(plugin_dir: Path) -> list[Path]:
    names = ("metadata.yaml", "package.json", "_conf_schema.json", "README.md", "readme.md")
    files = [plugin_dir / name for name in names if (plugin_dir / name).is_file()]
    for py in sorted(plugin_dir.glob("*.py")):
        files.append(py)
    return files[:12]


def collect_plugin_context(plugin_dir: Path) -> dict:
    plugin_dir = Path(plugin_dir)
    files = {}
    for path in _plugin_files(plugin_dir):
        files[path.name] = _read(path)
    return {
        "plugin_dir": str(plugin_dir),
        "files": files,
    }


def infer_plugin_id(plugin_dir: Path) -> str:
    meta = _read(Path(plugin_dir) / "metadata.yaml", 2000)
    match = re.search(r"^name:\s*(\S+)", meta, re.MULTILINE)
    if match:
        return match.group(1).strip()
    package = _read(Path(plugin_dir) / "package.json", 4000)
    try:
        data = json.loads(package)
        if data.get("name"):
            return str(data["name"]).strip()
    except Exception:
        pass
    return Path(plugin_dir).name


def build_prompt(plugin_id: str, context: dict, samples: str = "") -> str:
    file_blocks = "\n\n".join(
        f"--- {name} ---\n{content}" for name, content in context["files"].items()
    )
    contract = _read(CONTRACT, 8000)
    existing = _read(EXISTING, 6000)
    output = f"console/adapters/generated/{plugin_id}.py"
    test_output = f"tests/test_adapter_{plugin_id}.py"
    return f"""你在为 Stellaris 生成一个插件适配器。

## 契约（节选）
{contract}

## 现有适配器示例
{existing}

## 插件信息
plugin_id: {plugin_id}
目录: {context['plugin_dir']}

{file_blocks}

## 样例请求（脱敏）
{samples or '没有样例请求，按 manifest 和源码推断；缺字段时返回空字符串。'}

## 任务
1. 写 {output}
2. 写 {test_output}
3. 适配器只做归一化：实现 Adapter 的 detect/context/group_name
4. 必须设置 ENABLED = False
5. 不联网、不执行命令、不写业务数据
6. test 覆盖：UID、群聊、私聊、缺字段降级
7. 输出简短中文总结，不要粘贴整份代码
"""


def validate_adapter(path: Path, plugin_id: str) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"文件不存在: {path}"]
    text = _read(path, 50000)
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [f"语法错误: {exc}"]
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    bad = imports & FORBIDDEN_IMPORTS
    if bad:
        errors.append(f"禁止导入: {sorted(bad)}")
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    if "Adapter" not in classes:
        errors.append("缺少 class Adapter")
    if f'plugin_id = "{plugin_id}"' not in text and f"plugin_id = '{plugin_id}'" not in text:
        errors.append(f"缺少 plugin_id = {plugin_id}")
    if "ENABLED = False" not in text:
        errors.append("必须设置 ENABLED = False")
    return errors


async def _run_agent(prompt: str) -> str:
    from agent_core.execution.agent import ExecutionAgent

    agent = ExecutionAgent(workspace=ROOT)
    result = await agent.run(prompt, mode="execute", return_format="text", max_turns=8)
    return result.text if result.ok else (result.error or "生成失败")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--plugin-id", default="")
    parser.add_argument("--samples", default="", help="脱敏请求样例文件")
    parser.add_argument("--run", action="store_true", help="真正调用执行 AI")
    parser.add_argument("--force", action="store_true", help="覆盖已有生成物")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plugin_dir = Path(args.plugin_dir).expanduser().resolve()
    if not plugin_dir.is_dir():
        print(f"插件目录不存在: {plugin_dir}", file=sys.stderr)
        return 2
    plugin_id = args.plugin_id.strip() or infer_plugin_id(plugin_dir)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", plugin_id):
        print("plugin_id 只能包含字母数字 _ . -", file=sys.stderr)
        return 2
    context = collect_plugin_context(plugin_dir)
    samples = _read(Path(args.samples), 8000) if args.samples else ""
    prompt = build_prompt(plugin_id, context, samples)

    output = GENERATED / f"{plugin_id}.py"
    if output.exists() and not args.force:
        print(f"已存在，拒绝覆盖: {output}（要覆盖加 --force）", file=sys.stderr)
        return 3
    if not args.run:
        print(prompt)
        print(f"\n[dry-run] 将写入: {output}")
        return 0

    text = asyncio.run(_run_agent(prompt))
    print(text)
    errors = validate_adapter(output, plugin_id)
    if errors:
        print("\n生成物校验失败：")
        for error in errors:
            print(f"- {error}")
        return 4
    print(f"\n已生成并通过静态校验: {output}")
    print("下一步：人工 review、跑测试，然后把 ENABLED 改成 True。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
