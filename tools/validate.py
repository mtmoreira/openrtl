"""Provider-free repository validation front door."""

from __future__ import annotations

import argparse
import compileall
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verilator_canary import (  # noqa: E402
    describe_artifacts,
    discover_verilator_toolchain,
    run_verilator_canary,
)


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
        if path.suffix not in {".json", ".md", ".py", ".toml", ".sv", ".zsh"} and path.name not in {
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
        "docs/adr/0004-evidence-linked-debug-sessions.md",
        "docs/adr/0005-reviewable-repair-proposals.md",
        "docs/adr/0006-reviewed-repair-application.md",
        "docs/adr/0007-evidence-bound-source-edits.md",
        "docs/adr/0008-proposal-to-edit-plan.md",
        "docs/adr/0009-expert-source-edit-output.md",
        "docs/adr/0010-controlled-expert-invocation.md",
        "pyproject.toml",
        "src/openrtl/__init__.py",
        "src/openrtl/adapters/canary.py",
        "src/openrtl/adapters/fifo_debug.py",
        "src/openrtl/adapters/fifo_repair.py",
        "src/openrtl/adapters/source_edit_application.py",
        "src/openrtl/adapters/expert_source_edits.py",
        "src/openrtl/adapters/expert_invocation.py",
        "src/openrtl/application/expert_edits.py",
        "src/openrtl/application/expert_invocation.py",
        "src/openrtl/application/repair_execution.py",
        "src/openrtl/py.typed",
        "examples/fifo/rtl/sync_fifo.sv",
        "examples/fifo/dv/test_sync_fifo.py",
        "evals/openrtl_v1.json",
        "tools/verilator_canary.py",
        "tests/test_canary_evidence.py",
        "tests/test_fifo_debug_sessions.py",
        "tests/test_repair_application.py",
        "tests/test_expert_source_edits.py",
        "tests/test_expert_invocation.py",
        "tools/fifo_fault_case.py",
        "tools/fifo_repair_application_case.py",
        "examples/fifo/faults/sync_fifo_level_fault.sv",
        "examples/fifo/faults/level_update_edit_spec.json",
        "examples/fifo/dv/test_fifo_level_repair.py",
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


def _parse_arguments(arguments: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-verilator",
        action="store_true",
        help="explicitly select the external Verilator/cocotb FIFO canary",
    )
    parser.add_argument("--verilator-executable")
    parser.add_argument("--make-executable")
    parser.add_argument("--cocotb-config-executable")
    parser.add_argument("--verilator-output", type=Path)
    parser.add_argument("--verilator-timeout-seconds", type=int)
    parsed = parser.parse_args(arguments)
    explicit_tools = (
        parsed.verilator_executable,
        parsed.make_executable,
        parsed.cocotb_config_executable,
        parsed.verilator_output,
        parsed.verilator_timeout_seconds,
    )
    if any(value is not None for value in explicit_tools) and not parsed.with_verilator:
        parser.error("Verilator options require --with-verilator")
    return parsed


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parse_arguments(tuple(sys.argv[1:] if arguments is None else arguments))
    _validate_text_files()
    _validate_architecture()
    if not compileall.compile_dir(ROOT / "src", quiet=1):
        raise RuntimeError("source compilation failed")
    print("CHECKPOINT compile passed")
    _run_tests()
    if parsed.with_verilator:
        toolchain = discover_verilator_toolchain(
            verilator=parsed.verilator_executable,
            make=parsed.make_executable,
            cocotb_config=parsed.cocotb_config_executable,
        )
        print(f"CHECKPOINT verilator_executable selected {toolchain.verilator}")
        print(f"CHECKPOINT make_executable selected {toolchain.make}")
        print(f"CHECKPOINT cocotb_config_executable selected {toolchain.cocotb_config}")
        output_directory = parsed.verilator_output or Path("build/verilator-fifo-canary")
        if not output_directory.is_absolute():
            output_directory = ROOT / output_directory
        timeout_seconds = (
            120
            if parsed.verilator_timeout_seconds is None
            else parsed.verilator_timeout_seconds
        )
        artifacts = run_verilator_canary(
            ROOT,
            output_directory,
            toolchain,
            timeout_seconds=timeout_seconds,
        )
        print("CHECKPOINT verilator_cocotb_canary passed")
        for description in describe_artifacts(artifacts):
            print(description)
    else:
        print("CHECKPOINT verilator_cocotb_canary not_selected")
    print("OPENRTL_VALIDATION_STATUS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
