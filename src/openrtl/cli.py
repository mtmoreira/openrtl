"""Provider-free local command line interface for OpenRTL V1."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Sequence

from agentrig.capabilities import ToolInvocation
from agentrig.core import (
    CancellationSource,
    RunContext,
    RunId,
    SystemClock,
    Uuid4IdGenerator,
)
from agentrig.integrations import CommandInput
from openrtl.adapters import (
    LocalDesignCatalog,
    analyze_fifo_waveform,
    build_surfer_tool,
    fifo_repair_focus,
    inspect_vcd,
    load_fifo_canary_evidence,
    propose_fifo_repairs,
    surfer_command_file,
)
from openrtl.application import (
    EXPERT_DEFINITIONS,
    FIFO_RUN_REF,
    FIFO_SOURCE_REFS,
    OpenRTLWorkflow,
    run_scripted_fifo,
)
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
    verified = subcommands.add_parser(
        "verified-canary",
        help="build FIFO package candidacy from retained Verilator evidence",
    )
    verified.add_argument("--root", type=Path, default=Path.cwd())
    verified.add_argument(
        "--manifest",
        type=Path,
        default=Path("build/verilator-fifo-canary/evidence.json"),
    )
    verified.add_argument("--mode", choices=("build", "learn"), default="build")
    waveform = subcommands.add_parser(
        "waveform",
        help="inspect VCD traces and prepare an explicit Surfer focus",
    )
    waveform_commands = waveform.add_subparsers(
        dest="waveform_command",
        required=True,
    )
    inspect = waveform_commands.add_parser(
        "inspect",
        help="list signals or inspect bounded transitions",
    )
    _add_waveform_selection_arguments(inspect)
    inspect.add_argument("--output", type=Path)
    focus = waveform_commands.add_parser(
        "focus",
        help="write inspection JSON and a deterministic Surfer command file",
    )
    _add_waveform_selection_arguments(focus)
    focus.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/waveform-focus"),
    )
    focus.add_argument("--surfer-executable", type=Path)
    focus.add_argument("--launch", action="store_true")
    diagnose_fifo = waveform_commands.add_parser(
        "diagnose-fifo",
        help="explain FIFO clock-edge behavior and flag invariant violations",
    )
    _add_fifo_debug_arguments(diagnose_fifo)
    diagnose_fifo.add_argument("--output", type=Path)
    propose_fifo = waveform_commands.add_parser(
        "propose-fifo-repair",
        help="derive a reviewable non-applying repair proposal from FIFO findings",
    )
    _add_fifo_debug_arguments(propose_fifo)
    propose_fifo.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/fifo-repair-proposal"),
    )
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
    if arguments.command == "verified-canary":
        project_root = arguments.root.resolve()
        verified_run = load_fifo_canary_evidence(
            project_root,
            arguments.manifest,
            (*FIFO_SOURCE_REFS, FIFO_RUN_REF),
        )
        result = run_scripted_fifo(
            project_root,
            InteractionMode(arguments.mode),
            verified_run,
        )
        print(
            json.dumps(
                {
                    "covered_requirements": [
                        row.requirement_id for row in result.coverage if row.covered
                    ],
                    "evidence_id": verified_run.evidence.evidence_id,
                    "learning": result.learning is not None,
                    "package_digest": result.package.content_digest,
                    "package_id": result.package.package_id,
                    "publication_ready": result.package.publication_ready,
                    "run_id": verified_run.run.run_id,
                    "run_status": verified_run.run.status.value,
                    "trace_uri": verified_run.run.trace_uri,
                    "trust": result.package.trust.value,
                },
                sort_keys=True,
                indent=2,
            )
        )
        return 0
    if arguments.command == "waveform":
        return _waveform_command(arguments)
    raise AssertionError("argparse returned an unknown command")


def _add_waveform_selection_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("trace", type=Path)
    command.add_argument("--root", type=Path, default=Path.cwd())
    command.add_argument("--signal", action="append", default=[])
    command.add_argument("--start-fs", type=int, default=0)
    command.add_argument("--end-fs", type=int)
    command.add_argument("--max-transitions", type=int, default=200)


def _add_fifo_debug_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("trace", type=Path)
    command.add_argument("--root", type=Path, default=Path.cwd())
    command.add_argument("--start-fs", type=int, default=0)
    command.add_argument("--end-fs", type=int)
    command.add_argument("--depth", type=int)
    command.add_argument("--hierarchy", default="sync_fifo")
    command.add_argument(
        "--rtl",
        type=Path,
        default=Path("examples/fifo/rtl/sync_fifo.sv"),
    )


def _waveform_command(arguments: argparse.Namespace) -> int:
    root = arguments.root.resolve(strict=True)
    if arguments.waveform_command in ("diagnose-fifo", "propose-fifo-repair"):
        report = analyze_fifo_waveform(
            root,
            arguments.trace,
            start_fs=arguments.start_fs,
            end_fs=arguments.end_fs,
            depth=arguments.depth,
            hierarchy=arguments.hierarchy,
            rtl_path=arguments.rtl,
        )
        if arguments.waveform_command == "diagnose-fifo":
            payload = report.payload()
            if arguments.output is not None:
                output = _contained_output(root, arguments.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                _write_json(output, payload)
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if report.passed else 1

        output_directory = _contained_output(root, arguments.output_directory)
        output_directory.mkdir(parents=True, exist_ok=True)
        debug_path = output_directory / "debug-session.json"
        proposal_path = output_directory / "repair-proposal.json"
        focus_path = output_directory / "focus.sucl"
        proposal = propose_fifo_repairs(
            report,
            report_uri=debug_path.relative_to(root).as_posix(),
        )
        _write_json(debug_path, report.payload())
        _write_json(proposal_path, proposal.payload())
        focus_path.write_text(
            surfer_command_file(fifo_repair_focus(report)),
            encoding="utf-8",
        )
        print(json.dumps(proposal.payload(), indent=2, sort_keys=True))
        return 0

    signals = tuple(arguments.signal)
    index, inspection = inspect_vcd(
        root,
        arguments.trace,
        signals=signals,
        start_fs=arguments.start_fs,
        end_fs=arguments.end_fs,
        max_transitions=arguments.max_transitions,
    )
    payload = inspection.payload()
    if arguments.waveform_command == "inspect":
        if arguments.output is not None:
            output = _contained_output(root, arguments.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if arguments.waveform_command != "focus":
        raise AssertionError("argparse returned an unknown waveform command")
    if not signals:
        raise ValueError("waveform focus requires at least one --signal")
    if arguments.launch and arguments.surfer_executable is None:
        raise ValueError("--launch requires --surfer-executable")

    output_directory = _contained_output(root, arguments.output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)
    focus = index.focus(
        inspection.trace,
        signals,
        inspection.start_fs,
        inspection.end_fs,
    )
    inspection_path = output_directory / "inspection.json"
    command_path = output_directory / "focus.sucl"
    command_path.write_text(surfer_command_file(focus), encoding="utf-8")
    payload.update(
        {
            "command_file": command_path.relative_to(root).as_posix(),
            "markers_fs": focus.markers_fs,
        }
    )
    inspection_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    launched_process_id: int | None = None
    if arguments.launch:
        executable = arguments.surfer_executable.resolve(strict=True)
        if not executable.is_file():
            raise ValueError("Surfer executable must be a regular file")
        launched_process_id = asyncio.run(
            _launch_surfer(
                root,
                executable,
                (root / inspection.trace).resolve(strict=True),
                command_path,
            )
        )
    print(
        json.dumps(
            {
                "command_file": str(command_path),
                "inspection": str(inspection_path),
                "launched_process_id": launched_process_id,
                "trace": str((root / inspection.trace).resolve(strict=True)),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


async def _launch_surfer(
    root: Path,
    executable: Path,
    trace: Path,
    command_file: Path,
) -> int:
    tool = build_surfer_tool(
        workspace=str(root),
        surfer_executable=str(executable),
    )
    invocation = ToolInvocation(
        invocation_id="waveform.surfer.launch",
        contract=tool.contract,
        input=CommandInput(
            arguments=("--command-file", str(command_file), str(trace)),
        ),
    )
    id_generator = Uuid4IdGenerator(RunId)
    context = RunContext.create_root(
        clock=SystemClock(),
        id_generator=id_generator,
        cancellation=CancellationSource().token,
    )
    result = await tool.invoke(invocation, context)
    return result.unwrap().process_id


def _contained_output(root: Path, candidate: Path) -> Path:
    output = candidate if candidate.is_absolute() else root / candidate
    resolved = output.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as error:
        raise ValueError("waveform output must be contained by its root") from error
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ValueError("waveform output must not traverse symlinks")
    return resolved


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


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
