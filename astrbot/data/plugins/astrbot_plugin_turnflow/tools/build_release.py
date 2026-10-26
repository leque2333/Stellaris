"""Build a new release without replacing existing archives; verify extracted code."""

import argparse
import ast
import hashlib
import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT
PLUGIN_NAME = "astrbot_plugin_turnflow"
ROOT_FILES = {
    "__init__.py", "_conf_schema.json", "caption_bridge.py",
    "continuation_predictor.py", "forward_handler.py",
    "main.py", "message_parser.py", "metadata.yaml", "recall_adapter.py",
    "requirements.txt", "turn_state.py", "README.md", "CHANGELOG.md",
    "LICENSE", "README_EN.md", "THIRD_PARTY_NOTICES.md",
}


def release_files():
    for path in sorted(SOURCE.iterdir()):
        if not path.is_file():
            continue
        rel = path.relative_to(SOURCE)
        if "__pycache__" in rel.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if rel.as_posix() in ROOT_FILES:
            yield path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("version must be X.Y.Z")
    metadata = (SOURCE / "metadata.yaml").read_text(encoding="utf-8")
    if not re.search(rf"^version:\s*{re.escape(args.version)}\s*$", metadata, re.M):
        parser.error("metadata version mismatch")
    if not re.search(rf"^name:\s*{PLUGIN_NAME}\s*$", metadata, re.M):
        parser.error("metadata plugin name mismatch")
    output = ROOT / "dist" / f"{PLUGIN_NAME}-{args.version}.zip"
    if output.exists():
        parser.error(f"refusing to overwrite {output}")
    schema = json.loads((SOURCE / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["max_collection_wait"]["default"] == 30.0
    files = list(release_files())
    missing = ROOT_FILES - {path.name for path in files}
    if missing:
        parser.error(f"missing release files: {sorted(missing)}")
    with tempfile.TemporaryDirectory(prefix="turnflow_release_") as temp:
        temp_root = Path(temp).resolve()
        assert temp_root.is_relative_to(Path(tempfile.gettempdir()).resolve())
        for index, path in enumerate(files):
            if path.suffix == ".py":
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                py_compile.compile(
                    str(path), cfile=str(temp_root / f"compile_{index}.pyc"), doraise=True
                )
        candidate = temp_root / "candidate.zip"
        with ZipFile(candidate, "x", ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, f"{PLUGIN_NAME}/{path.relative_to(SOURCE).as_posix()}")
        extract_root = temp_root / "\u4e2d\u6587 path smoke"
        with ZipFile(candidate) as archive:
            assert archive.testzip() is None
            names = archive.namelist()
            assert all("\\" not in name for name in names)
            assert all(name.startswith(PLUGIN_NAME + "/") for name in names)
            assert {name.split("/", 1)[1] for name in names} == ROOT_FILES
            assert not any("__pycache__" in name or name.endswith((".pyc", ".pyo")) for name in names)
            assert not any("lite_link_parser/" in name or name.endswith("/link_parser_adapter.py") for name in names)
            for name in names:
                assert (extract_root / name).resolve().is_relative_to(extract_root.resolve())
            archive.extractall(extract_root)
        extracted = extract_root / PLUGIN_NAME
        for path in files:
            assert path.read_bytes() == (extracted / path.relative_to(SOURCE)).read_bytes()
        env = os.environ.copy()
        env["TURNFLOW_TEST_PLUGIN"] = str(extracted)
        subprocess.run(
            [sys.executable, "-B", "-m", "unittest", "discover",
             "-s", str(ROOT / "tests"), "-q"],
            cwd=temp_root, env=env, check=True,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as destination:
            destination.write(candidate.read_bytes())
    print(json.dumps({
        "archive": str(output),
        "file_count": len(files),
        "backslash_count": 0,
        "bad_root_count": 0,
        "pycache_count": 0,
        "extract_smoke": True,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "size_bytes": output.stat().st_size,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
