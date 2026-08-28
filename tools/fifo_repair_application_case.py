"""Run the reviewed FIFO repair through failing and repaired Verilator evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Sequence
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openrtl.adapters import (  # noqa: E402
    analyze_fifo_waveform,
    apply_reviewed_source_edits,
    fifo_repair_focus,
    propose_fifo_repairs,
    surfer_command_file,
)
from openrtl.application import RepairApproval, build_source_edit_plan  # noqa: E402
from openrtl.adapters.waveforms import WaveformFocus  # noqa: E402
from tools.verilator_canary import (  # noqa: E402
    VerilatorToolchain,
    discover_verilator_toolchain,
)


_MARKER_TEXT = "openrtl-fifo-repair-application-v2\n"
_KNOWN_OUTPUTS = frozenset(
    {
        ".openrtl-fifo-repair-application-owner",
        "application.json",
        "before",
        "candidate",
        "comparison.json",
        "debug-session.json",
        "edit-plan.json",
        "evidence.json",
        "focus-after.sucl",
        "focus-before.sucl",
        "proposal.json",
        "repaired",
    }
)


@dataclass(frozen=True)
class SimulationAttempt:
    log: Path
    results: Path
    waveform: Path
    simulation_build: Path
    passed: bool


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/fifo-repair-application"),
    )
    parser.add_argument("--verilator-executable")
    parser.add_argument("--make-executable")
    parser.add_argument("--cocotb-config-executable")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parsed = parser.parse_args(arguments)
    if parsed.timeout_seconds < 1 or parsed.timeout_seconds > 600:
        raise ValueError("repair validation timeout must be between 1 and 600 seconds")
    root = parsed.root.resolve(strict=True)
    output = _prepare_output(root, parsed.output_directory)
    toolchain = discover_verilator_toolchain(
        verilator=parsed.verilator_executable,
        make=parsed.make_executable,
        cocotb_config=parsed.cocotb_config_executable,
    )
    source = root / "examples/fifo/faults/sync_fifo_level_fault.sv"
    if not source.is_file() or source.is_symlink():
        raise RuntimeError("reviewed FIFO fault source is unavailable")

    before = _run_simulation(
        root,
        output / "before",
        source,
        toolchain,
        expect_pass=False,
        timeout_seconds=parsed.timeout_seconds,
    )
    before_report = analyze_fifo_waveform(
        root,
        before.waveform,
        rtl_path=source.relative_to(root),
    )
    if before_report.passed or len(before_report.findings) != 1:
        raise RuntimeError("faulty FIFO did not produce one bounded debug finding")
    finding = before_report.findings[0]
    if ".level." not in finding.finding_id or finding.requirement_id != "fifo.write":
        raise RuntimeError("faulty FIFO finding is not the reviewed level update failure")

    debug_path = output / "debug-session.json"
    proposal_path = output / "proposal.json"
    edit_plan_path = output / "edit-plan.json"
    application_path = output / "application.json"
    comparison_path = output / "comparison.json"
    before_focus_path = output / "focus-before.sucl"
    after_focus_path = output / "focus-after.sucl"
    repaired_source = output / "candidate/sync_fifo.sv"
    proposal = propose_fifo_repairs(
        before_report,
        report_uri=debug_path.relative_to(root).as_posix(),
    )
    _write_json(debug_path, before_report.payload())
    _write_json(proposal_path, proposal.payload())
    edit_specs = _load_edit_specs(
        root / "examples/fifo/faults/level_update_edit_spec.json"
    )
    edit_plan = build_source_edit_plan(
        proposal_id=proposal.proposal_id,
        debug_session_id=before_report.session_id,
        source_path=source.relative_to(root).as_posix(),
        source=source.read_bytes(),
        edit_specs=edit_specs,
    )
    _write_json(edit_plan_path, edit_plan.payload())
    before_focus = fifo_repair_focus(before_report)
    before_focus_path.write_text(surfer_command_file(before_focus), encoding="utf-8")

    application = apply_reviewed_source_edits(
        root,
        proposal_path=proposal_path,
        debug_session_path=debug_path,
        edit_plan_path=edit_plan_path,
        output_path=repaired_source,
        approval=RepairApproval(
            proposal.proposal_id,
            ("repair.change.level",),
            edit_plan.content_digest,
            "Reviewed the linked FIFO level finding, waveform edge, and exact source anchors.",
        ),
    )
    _write_json(application_path, application.payload())

    after = _run_simulation(
        root,
        output / "repaired",
        repaired_source,
        toolchain,
        expect_pass=True,
        timeout_seconds=parsed.timeout_seconds,
    )
    after_report = analyze_fifo_waveform(
        root,
        after.waveform,
        rtl_path=repaired_source.relative_to(root),
    )
    if not after_report.passed or after_report.findings:
        raise RuntimeError("repaired FIFO still has waveform findings")
    after_focus_path.write_text(
        surfer_command_file(
            WaveformFocus(
                after.waveform.relative_to(root).as_posix(),
                before_focus.start_fs,
                min(before_focus.end_fs, after_report.waveform_anchor.end_fs),
                before_focus.signals,
                tuple(
                    value
                    for value in before_focus.markers_fs
                    if value <= after_report.waveform_anchor.end_fs
                ),
            )
        ),
        encoding="utf-8",
    )

    comparison: dict[str, Any] = {
        "after": {
            "finding_ids": tuple(value.finding_id for value in after_report.findings),
            "passed": after_report.passed,
            "waveform": after.waveform.relative_to(root).as_posix(),
        },
        "application_id": application.application_id,
        "before": {
            "finding_ids": tuple(value.finding_id for value in before_report.findings),
            "passed": before_report.passed,
            "waveform": before.waveform.relative_to(root).as_posix(),
        },
        "proposal_id": proposal.proposal_id,
        "schema": "openrtl.repair-comparison.v1",
        "status": "validated",
    }
    _write_json(comparison_path, comparison)
    evidence_path = output / "evidence.json"
    evidence = {
        "artifacts": {
            name: _file_evidence(root, path)
            for name, path in (
                ("application", application_path),
                ("before_log", before.log),
                ("before_results", before.results),
                ("before_waveform", before.waveform),
                ("comparison", comparison_path),
                ("debug_session", debug_path),
                ("edit_plan", edit_plan_path),
                ("focus_after", after_focus_path),
                ("focus_before", before_focus_path),
                ("proposal", proposal_path),
                ("repaired_log", after.log),
                ("repaired_results", after.results),
                ("repaired_source", repaired_source),
                ("repaired_waveform", after.waveform),
            )
        },
        "authorization_boundary": {
            "candidate_only": True,
            "gui_launched": False,
            "production_rtl_modified": False,
            "remote_operations": False,
        },
        "qualified_application_id": application.application_id,
        "qualified_edit_plan_digest": edit_plan.content_digest,
        "schema": "openrtl.repair-application-evidence.v2",
        "status": "passed",
        "toolchain": {
            "cocotb_config": str(toolchain.cocotb_config),
            "make": str(toolchain.make),
            "verilator": str(toolchain.verilator),
        },
    }
    _write_json(evidence_path, evidence)

    summary = {
        "after_waveform": after.waveform.relative_to(root).as_posix(),
        "application": application_path.relative_to(root).as_posix(),
        "before_finding_ids": tuple(value.finding_id for value in before_report.findings),
        "before_waveform": before.waveform.relative_to(root).as_posix(),
        "comparison": comparison_path.relative_to(root).as_posix(),
        "evidence": evidence_path.relative_to(root).as_posix(),
        "edit_plan": edit_plan_path.relative_to(root).as_posix(),
        "edit_plan_digest": edit_plan.content_digest,
        "proposal": proposal_path.relative_to(root).as_posix(),
        "repaired_finding_ids": tuple(value.finding_id for value in after_report.findings),
        "schema": "openrtl.fifo-repair-application-case.v2",
        "status": "passed",
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _load_edit_specs(path: Path) -> tuple[dict[str, str], ...]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("reviewed FIFO edit specification is unavailable")
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict) or set(payload) != {"edits", "schema"}:
        raise ValueError("reviewed FIFO edit specification fields are invalid")
    if payload.get("schema") != "openrtl.source-edit-spec.v1":
        raise ValueError("reviewed FIFO edit specification schema is invalid")
    edits = payload.get("edits")
    expected_fields = {
        "change_id",
        "edit_id",
        "expected_before",
        "operation",
        "replacement",
    }
    if (
        not isinstance(edits, list)
        or not edits
        or any(not isinstance(value, dict) or set(value) != expected_fields for value in edits)
        or any(not all(isinstance(item, str) for item in value.values()) for value in edits)
    ):
        raise ValueError("reviewed FIFO edit specification edits are invalid")
    return tuple(
        {str(key): str(item) for key, item in value.items()}
        for value in edits
    )


def _prepare_output(root: Path, candidate: Path) -> Path:
    build_path = root / "build"
    if build_path.is_symlink():
        raise RuntimeError("repository build root must not be a symlink")
    build_root = build_path.resolve()
    selected = candidate if candidate.is_absolute() else root / candidate
    resolved = selected.resolve()
    if not resolved.is_relative_to(build_root) or resolved.is_symlink():
        raise RuntimeError("repair output directory must be inside the build root")
    marker = resolved / ".openrtl-fifo-repair-application-owner"
    if resolved.exists():
        if not resolved.is_dir():
            raise RuntimeError("repair output path is not a directory")
        entries = {value.name for value in resolved.iterdir()}
        if entries and (
            not marker.is_file()
            or marker.is_symlink()
            or marker.read_text(encoding="utf-8") != _MARKER_TEXT
        ):
            raise RuntimeError("repair output directory has no valid ownership marker")
        unknown = sorted(entries - _KNOWN_OUTPUTS)
        if unknown:
            raise RuntimeError(f"repair output contains unowned entries: {', '.join(unknown)}")
        for entry in resolved.iterdir():
            if entry.is_symlink():
                raise RuntimeError("repair output contains a symlink")
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    else:
        resolved.mkdir(parents=True)
    marker.write_text(_MARKER_TEXT, encoding="utf-8")
    return resolved


def _run_simulation(
    root: Path,
    output: Path,
    source: Path,
    toolchain: VerilatorToolchain,
    *,
    expect_pass: bool,
    timeout_seconds: int,
) -> SimulationAttempt:
    output.mkdir()
    temporary = output / "tmp"
    temporary.mkdir()
    attempt = SimulationAttempt(
        output / "run.log",
        output / "results.xml",
        output / "waves.vcd",
        output / "sim_build",
        expect_pass,
    )
    command = [
        str(toolchain.make),
        "-C",
        str(root / "examples/fifo/dv"),
        "SIM=verilator",
        f"VERILATOR_BIN_DIR={toolchain.verilator.parent}",
        f"SIM_BUILD={attempt.simulation_build}",
        f"COCOTB_RESULTS_FILE={attempt.results}",
        f"SIM_ARGS=--trace --trace-file {attempt.waveform}",
        f"VERILOG_SOURCES={source}",
        "MODULE=test_fifo_level_repair",
    ]
    path_entries = (
        toolchain.verilator.parent,
        toolchain.cocotb_config.parent,
        toolchain.make.parent,
        Path("/usr/bin"),
        Path("/bin"),
    )
    environment = {
        "PATH": os.pathsep.join(dict.fromkeys(str(value) for value in path_entries)),
        "PYTHONPATH": os.pathsep.join((str(root / "src"), str(root))),
        "TMPDIR": str(temporary),
    }
    try:
        with attempt.log.open("wb") as stream:
            completed = subprocess.run(
                command,
                cwd=root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("FIFO repair simulation exceeded its timeout") from error
    if expect_pass != (completed.returncode == 0):
        expected = "pass" if expect_pass else "fail"
        raise RuntimeError(f"FIFO repair simulation did not {expected}; inspect {attempt.log}")
    _verify_attempt(attempt, expect_pass=expect_pass)
    return attempt


def _verify_attempt(attempt: SimulationAttempt, *, expect_pass: bool) -> None:
    for path in (attempt.log, attempt.results, attempt.waveform):
        if not path.is_file() or path.is_symlink() or path.stat().st_size < 1:
            raise RuntimeError(f"FIFO repair simulation artifact is missing: {path.name}")
    log = attempt.log.read_bytes()
    if b"%Warning-" in log or b"WIDTHEXPAND" in log or b"CMPCONST" in log:
        raise RuntimeError("FIFO repair simulation emitted a Verilator warning")
    try:
        root = ET.fromstring(attempt.results.read_bytes())
    except ET.ParseError as error:
        raise RuntimeError("FIFO repair results XML is invalid") from error
    cases = list(root.iter("testcase"))
    failed = bool(list(root.iter("failure")) or list(root.iter("error")))
    if len(cases) != 1 or failed == expect_pass:
        raise RuntimeError("FIFO repair results do not match the expected outcome")
    waveform = attempt.waveform.read_bytes()
    if b"$timescale" not in waveform or b"$enddefinitions $end" not in waveform:
        raise RuntimeError("FIFO repair waveform is incomplete")
    if (
        not attempt.simulation_build.is_dir()
        or attempt.simulation_build.is_symlink()
        or not any(attempt.simulation_build.iterdir())
    ):
        raise RuntimeError("FIFO repair simulation build is missing")


def _file_evidence(root: Path, path: Path) -> dict[str, str | int]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
