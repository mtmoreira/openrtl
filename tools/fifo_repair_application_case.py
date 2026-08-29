"""Run the reviewed FIFO repair through failing and repaired Verilator evidence."""

from __future__ import annotations

import argparse
import asyncio
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

from agentrig.capabilities import (  # noqa: E402
    CapabilityDescriptor,
    CapabilityFeature,
    CapabilityKind,
    CapabilityLimit,
    DataRetention,
    GenerationUsage,
    ModelMetadata,
    TextGenerationFinishReason,
)
from agentrig.core import (  # noqa: E402
    CancellationSource,
    RunContext,
    RunId,
    SystemClock,
    Uuid4IdGenerator,
)
from agentrig.testing import (  # noqa: E402
    ScriptedStructuredGeneration,
    ScriptedStructuredGenerator,
)
from openrtl.adapters import (  # noqa: E402
    analyze_fifo_waveform,
    apply_reviewed_source_edits,
    draft_source_edit_plan,
    fifo_repair_focus,
    inspect_vcd,
    invoke_expert_source_edits,
    prepare_expert_source_edit_request,
    propose_fifo_repairs,
    surfer_command_file,
)
from openrtl.application import ExpertInvocationPolicy, RepairApproval  # noqa: E402
from openrtl.adapters.waveforms import WaveformFocus  # noqa: E402
from tools.verilator_canary import (  # noqa: E402
    VerilatorToolchain,
    discover_verilator_toolchain,
)


_MARKER_TEXT = "openrtl-fifo-repair-application-v2\n"
_VISIBLE_SIGNAL_SUFFIXES = frozenset(
    {
        "clk",
        "empty",
        "full",
        "level",
        "rd_ready",
        "rd_valid",
        "read_accepted",
        "rst_n",
        "wr_ready",
        "wr_valid",
        "write_accepted",
    }
)
_KNOWN_OUTPUTS = frozenset(
    {
        ".openrtl-fifo-repair-application-owner",
        "application.json",
        "before",
        "candidate",
        "comparison.json",
        "debug-session.json",
        "edit-plan.json",
        "edit-plan-planning.json",
        "expert-edit-response.json",
        "expert-edit-request.json",
        "expert-edit-spec.json",
        "expert-edit-suggestion.json",
        "expert-invocation-envelope.json",
        "expert-invocation-report.json",
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
    planning_report_path = output / "edit-plan-planning.json"
    expert_request_path = output / "expert-edit-request.json"
    expert_response_path = output / "expert-edit-response.json"
    expert_spec_path = output / "expert-edit-spec.json"
    expert_suggestion_path = output / "expert-edit-suggestion.json"
    expert_envelope_path = output / "expert-invocation-envelope.json"
    expert_invocation_path = output / "expert-invocation-report.json"
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
    expert_request = prepare_expert_source_edit_request(
        root,
        proposal_path=proposal_path,
        debug_session_path=debug_path,
        source_path=source,
    )
    _write_json(expert_request_path, expert_request.payload())
    external_spec = json.loads(
        (root / "examples/fifo/faults/level_update_edit_spec.json").read_text(
            encoding="utf-8"
        )
    )
    expert_response = {
        "applies_changes": False,
        "change_ids": list(expert_request.change_ids),
        "context_pack_digest": expert_request.context_pack_digest,
        "context_pack_id": expert_request.context_pack.pack_id,
        "debug_session_id": expert_request.debug_session_id,
        "edits": external_spec["edits"],
        "expert_role": "diagnosis_closure_engineer",
        "proposal_id": expert_request.proposal_id,
        "request_digest": expert_request.content_digest,
        "request_id": expert_request.request_id,
        "schema": "openrtl.expert-source-edit-output.v1",
        "source": {
            "content_digest": expert_request.source_digest,
            "path": expert_request.source_path,
        },
        "status": "proposed_untrusted",
    }
    invocation_policy = ExpertInvocationPolicy(
        "runtime.scripted.expert-edits",
        "scripted.expert-source-edits",
        "scripted",
        "scripted-expert-v1",
        DataRetention.NOT_RETAINED,
    )
    generator = ScriptedStructuredGenerator[dict[str, Any]](
        descriptor=CapabilityDescriptor(
            capability_id=invocation_policy.capability_id,
            version="1",
            kind=CapabilityKind.STRUCTURED_GENERATION,
            features=frozenset({CapabilityFeature.STRUCTURED_OUTPUT}),
            limits={
                CapabilityLimit.MAX_OUTPUT_TOKENS: invocation_policy.max_output_tokens
            },
            data_retention=DataRetention.NOT_RETAINED,
        ),
        outcomes=(
            ScriptedStructuredGeneration(
                encoded_output=expert_response,
                usage=GenerationUsage(),
                model=ModelMetadata(
                    provider="scripted",
                    model_id=invocation_policy.model,
                ),
                finish_reason=TextGenerationFinishReason.COMPLETED,
            ),
        ),
    )
    id_generator = Uuid4IdGenerator(RunId)
    invocation = asyncio.run(
        invoke_expert_source_edits(
            root,
            request_path=expert_request_path,
            proposal_path=proposal_path,
            debug_session_path=debug_path,
            source_path=source,
            generator=generator,
            policy=invocation_policy,
            context=RunContext.create_root(
                clock=SystemClock(),
                id_generator=id_generator,
                cancellation=CancellationSource().token,
            ),
        )
    )
    expert_spec = invocation.edit_spec
    expert_suggestion = invocation.suggestion
    _write_json(expert_envelope_path, invocation.envelope)
    _write_json(expert_response_path, invocation.response)
    _write_json(expert_spec_path, expert_spec)
    _write_json(expert_suggestion_path, expert_suggestion)
    _write_json(expert_invocation_path, invocation.report.payload())
    edit_plan, planning_report = draft_source_edit_plan(
        root,
        proposal_path=proposal_path,
        debug_session_path=debug_path,
        source_path=source,
        edit_spec_path=expert_spec_path,
    )
    _write_json(edit_plan_path, edit_plan.payload())
    _write_json(planning_report_path, planning_report.payload())
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
    after_focus = WaveformFocus(
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
    after_focus_path.write_text(surfer_command_file(after_focus), encoding="utf-8")
    visual_evidence = _verify_visible_waveform_difference(
        root,
        before,
        after,
        before_focus,
        after_focus,
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
        "schema": "openrtl.repair-comparison.v2",
        "status": "validated",
        "visual_evidence": visual_evidence,
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
                ("edit_plan_planning", planning_report_path),
                ("expert_edit_request", expert_request_path),
                ("expert_edit_response", expert_response_path),
                ("expert_edit_spec", expert_spec_path),
                ("expert_edit_suggestion", expert_suggestion_path),
                ("expert_invocation_envelope", expert_envelope_path),
                ("expert_invocation_report", expert_invocation_path),
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
        "qualified_expert_suggestion_id": invocation.report.suggestion_id,
        "schema": "openrtl.repair-application-evidence.v6",
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
        "edit_plan_planning": planning_report_path.relative_to(root).as_posix(),
        "expert_edit_request": expert_request_path.relative_to(root).as_posix(),
        "expert_edit_spec": expert_spec_path.relative_to(root).as_posix(),
        "expert_edit_suggestion": expert_suggestion_path.relative_to(root).as_posix(),
        "expert_invocation_envelope": expert_envelope_path.relative_to(root).as_posix(),
        "expert_invocation_report": expert_invocation_path.relative_to(root).as_posix(),
        "proposal": proposal_path.relative_to(root).as_posix(),
        "repaired_finding_ids": tuple(value.finding_id for value in after_report.findings),
        "schema": "openrtl.fifo-repair-application-case.v6",
        "status": "passed",
        "visual_evidence": visual_evidence,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


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


def _verify_visible_waveform_difference(
    root: Path,
    before: SimulationAttempt,
    after: SimulationAttempt,
    before_focus: WaveformFocus,
    after_focus: WaveformFocus,
) -> dict[str, Any]:
    if (
        before_focus.start_fs != after_focus.start_fs
        or before_focus.end_fs != after_focus.end_fs
        or before_focus.markers_fs != after_focus.markers_fs
        or before_focus.signals != after_focus.signals
    ):
        raise RuntimeError("before and repaired waveform focus must be directly comparable")
    if len(before_focus.markers_fs) != 1:
        raise RuntimeError("visible repair evidence requires one finding marker")
    marker_fs = before_focus.markers_fs[0]
    if not before_focus.start_fs < marker_fs < before_focus.end_fs:
        raise RuntimeError("repair finding must be inside the visible focus window")
    selected_suffixes = {
        signal.rpartition(".")[2] for signal in before_focus.signals
    }
    if not _VISIBLE_SIGNAL_SUFFIXES.issubset(selected_suffixes):
        raise RuntimeError("repair focus omits causal FIFO signals")

    before_index, before_inspection = inspect_vcd(
        root,
        before.waveform,
        signals=before_focus.signals,
    )
    after_index, after_inspection = inspect_vcd(
        root,
        after.waveform,
        signals=after_focus.signals,
    )
    if (
        before_inspection.trace_end_fs <= before_focus.end_fs
        or after_inspection.trace_end_fs <= after_focus.end_fs
    ):
        raise RuntimeError("repair traces must extend beyond the visible focus window")

    hierarchy = before_focus.signals[0].rpartition(".")[0]
    if not hierarchy or any(
        signal.rpartition(".")[0] != hierarchy for signal in before_focus.signals
    ):
        raise RuntimeError("repair focus signals must share one hierarchy")
    clock = f"{hierarchy}.clk"
    level = f"{hierarchy}.level"
    for index in (before_index, after_index):
        if not index.transitions(clock, marker_fs + 1, before_focus.end_fs):
            raise RuntimeError("repair focus requires a clock transition after the finding")

    before_level = _binary_integer(before_index.value_at(level, marker_fs), level)
    repaired_level = _binary_integer(after_index.value_at(level, marker_fs), level)
    before_visible_level = _binary_integer(
        before_index.value_at(level, before_focus.end_fs),
        level,
    )
    repaired_visible_level = _binary_integer(
        after_index.value_at(level, after_focus.end_fs),
        level,
    )
    if (
        before_level != before_visible_level
        or repaired_level != repaired_visible_level
        or before_level == repaired_level
    ):
        raise RuntimeError("repair level difference is not visible across the focus window")

    return {
        "before": {
            "level_at_focus_end": before_visible_level,
            "level_at_marker": before_level,
            "trace_end_fs": before_inspection.trace_end_fs,
        },
        "focus": {
            "end_fs": before_focus.end_fs,
            "marker_fs": marker_fs,
            "signals": before_focus.signals,
            "start_fs": before_focus.start_fs,
        },
        "repaired": {
            "level_at_focus_end": repaired_visible_level,
            "level_at_marker": repaired_level,
            "trace_end_fs": after_inspection.trace_end_fs,
        },
        "schema": "openrtl.repair-visual-comparison.v1",
        "status": "visibly_distinct",
    }


def _binary_integer(value: str | None, signal: str) -> int:
    if value is None or not value or any(character not in "01" for character in value):
        raise RuntimeError(f"repair visual evidence signal is not binary: {signal}")
    return int(value, 2)


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
