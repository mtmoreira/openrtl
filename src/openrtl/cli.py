"""Provider-free local command line interface for OpenRTL V1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from openrtl.adapters import LocalDesignCatalog
from openrtl.application import EXPERT_DEFINITIONS, OpenRTLWorkflow
from openrtl.domain import InteractionMode


_CANARY_FILES = (
    "examples/fifo/spec.md",
    "examples/fifo/model.py",
    "examples/fifo/test_model.py",
    "examples/fifo/rtl/sync_fifo.sv",
    "examples/fifo/dv/Makefile",
    "examples/fifo/dv/test_sync_fifo.py",
)
_FIFO_REQUIREMENTS = (
    "fifo.reset",
    "fifo.write",
    "fifo.read",
    "fifo.order",
    "fifo.backpressure",
    "fifo.simultaneous",
    "fifo.wrap",
    "fifo.status",
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="openrtl")
    subcommands = root.add_subparsers(dest="command", required=True)
    subcommands.add_parser("experts", help="list stable expert contracts")
    plan = subcommands.add_parser("plan", help="show the deterministic V1 workflow")
    plan.add_argument("--mode", choices=("build", "learn"), default="build")
    canary = subcommands.add_parser("canary", help="validate FIFO collateral structure")
    canary.add_argument("--root", type=Path, default=Path.cwd())
    catalog = subcommands.add_parser("catalog", help="list local reusable designs")
    catalog.add_argument("--root", type=Path, required=True)
    return root


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    if arguments.command == "experts":
        print(
            json.dumps(
                [
                    {
                        "produces": [kind.value for kind in value.produces],
                        "purpose": value.purpose,
                        "required_tools": value.required_tools,
                        "role": value.role.value,
                    }
                    for value in EXPERT_DEFINITIONS
                ],
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "plan":
        state = OpenRTLWorkflow().create(InteractionMode(arguments.mode))
        print(json.dumps([value.value for value in state.stages]))
        return 0
    if arguments.command == "canary":
        errors = validate_fifo_canary(arguments.root.resolve())
        print(json.dumps({"errors": errors, "valid": not errors}, sort_keys=True))
        return 0 if not errors else 1
    if arguments.command == "catalog":
        catalog = LocalDesignCatalog(arguments.root.resolve())
        print(json.dumps({"package_ids": catalog.package_ids()}, sort_keys=True))
        return 0
    raise AssertionError("argparse returned an unknown command")


def validate_fifo_canary(root: Path) -> tuple[str, ...]:
    if root == Path("/"):
        raise ValueError("canary root must be bounded")
    errors: list[str] = []
    for relative in _CANARY_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing:{relative}")
    specification = root / "examples/fifo/spec.md"
    if specification.is_file():
        content = specification.read_text()
        for requirement in _FIFO_REQUIREMENTS:
            if f"`{requirement}`" not in content:
                errors.append(f"missing-requirement:{requirement}")
    rtl = root / "examples/fifo/rtl/sync_fifo.sv"
    if rtl.is_file():
        content = rtl.read_text()
        for feature in ("always_ff", "assert", "write_pointer", "read_pointer"):
            if feature not in content:
                errors.append(f"missing-rtl-feature:{feature}")
    return tuple(errors)


if __name__ == "__main__":
    raise SystemExit(main())
