"""Provider-free repository validation front door."""

from __future__ import annotations

import compileall
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "runs",
    "sim_build",
}


def _validate_text_files() -> None:
    checked = 0
    for path in sorted(ROOT.rglob("*")):
        relative_path = path.relative_to(ROOT)
        if not path.is_file() or any(
            part in IGNORED_DIRECTORY_NAMES for part in relative_path.parts
        ):
            continue
        if path.suffix not in {".md", ".py", ".toml", ".sv", ".zsh"} and path.name not in {
            ".gitignore",
            "LICENSE",
        }:
            continue
        data = path.read_bytes()
        if b"\x00" in data:
            raise RuntimeError(f"binary data in text file: {relative_path}")
        if data and not data.endswith(b"\n"):
            raise RuntimeError(f"missing final newline: {relative_path}")
        checked += 1
    print(f"CHECKPOINT text_files valid {checked}")


def _validate_architecture() -> None:
    required = {
        "AGENTS.md",
        "LICENSE",
        "README.md",
        "docs/architecture.md",
        "docs/development-plan.md",
        "docs/adr/0001-artifact-first-context.md",
        "docs/adr/0002-local-first-design-library.md",
        "docs/adr/0003-simulation-toolchain.md",
        "pyproject.toml",
        "src/openrtl/__init__.py",
        "src/openrtl/py.typed",
        "examples/fifo/rtl/sync_fifo.sv",
        "examples/fifo/dv/test_sync_fifo.py",
        "evals/openrtl_v1.json",
    }
    missing = sorted(path for path in required if not (ROOT / path).is_file())
    if missing:
        raise RuntimeError(f"missing required files: {', '.join(missing)}")
    print("CHECKPOINT architecture required_files_present")


def _run_tests() -> None:
    environment = dict(os.environ)
    inherited = environment.get("PYTHONPATH", "")
    candidates = (
        str(ROOT / "src"),
        str(ROOT),
        inherited,
        str(ROOT.parent / "agentrig" / "src"),
    )
    environment["PYTHONPATH"] = ":".join(value for value in candidates if value)
    completed = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("unit tests failed")
    model = subprocess.run(
        [sys.executable, "-m", "unittest", "examples.fifo.test_model"],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    if model.returncode != 0:
        raise RuntimeError("FIFO model tests failed")
    print("CHECKPOINT tests passed")


def main() -> int:
    _validate_text_files()
    _validate_architecture()
    if not compileall.compile_dir(ROOT / "src", quiet=1):
        raise RuntimeError("source compilation failed")
    print("CHECKPOINT compile passed")
    _run_tests()
    print("OPENRTL_VALIDATION_STATUS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
