"""Run and diagnose a deterministic ready/valid skid-buffer refill fault."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
    VcdIndex,
    WaveformFocus,
    analyze_skid_buffer_waveform,
    propose_skid_buffer_repairs,
    skid_buffer_repair_focus,
    surfer_command_file,
)
from tools.verilator_canary import (  # noqa: E402
    VerilatorToolchain,
    discover_verilator_toolchain,
)


_MARKER = "openrtl-skid-buffer-case-v1\n"
_KNOWN_OUTPUTS = frozenset(
    {
        ".openrtl-skid-buffer-case-owner",
        "before",
        "comparison.json",
        "debug-session.json",
        "evidence.json",
        "focus-after.sucl",
        "focus-before.sucl",
        "focus.sucl",
        "repair-proposal.json",
        "repaired",
        "summary.json",
    }
)


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("build/skid-buffer-case"),
    )
    parser.add_argument("--verilator-executable")
    parser.add_argument("--make-executable")
    parser.add_argument("--cocotb-config-executable")
    parser.add_argument("--timeout-seconds", type=int, default=120)
    parsed = parser.parse_args(arguments)
    if parsed.timeout_seconds < 1 or parsed.timeout_seconds > 600:
        parser.error("--timeout-seconds must be between 1 and 600")
    root = parsed.root.resolve(strict=True)
    toolchain = discover_verilator_toolchain(
        verilator=parsed.verilator_executable,
        make=parsed.make_executable,
        cocotb_config=parsed.cocotb_config_executable,
    )
    payload = run_skid_buffer_case(
        root,
        parsed.output_directory,
        toolchain,
        timeout_seconds=parsed.timeout_seconds,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def run_skid_buffer_case(
    root: Path,
    output_directory: Path,
    toolchain: VerilatorToolchain,
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Retain failing and repaired simulations plus linked debug evidence."""

    resolved_root = root.resolve(strict=True)
    output = _prepare_output(resolved_root, output_directory)
    production = resolved_root / "examples/skid_buffer/rtl/skid_buffer.sv"
    fault = resolved_root / "examples/skid_buffer/faults/skid_buffer_refill_fault.sv"
    production_digest = _sha256(production)
    before = _run_simulation(
        resolved_root,
        output / "before",
        fault,
        toolchain,
        expect_pass=False,
        timeout_seconds=timeout_seconds,
    )
    repaired = _run_simulation(
        resolved_root,
        output / "repaired",
        production,
        toolchain,
        expect_pass=True,
        timeout_seconds=timeout_seconds,
    )
    if _sha256(production) != production_digest:
        raise RuntimeError("production skid-buffer RTL changed during validation")

    before_report = analyze_skid_buffer_waveform(
        resolved_root,
        before["waveform"],
        rtl_path=Path(
            "examples/skid_buffer/faults/skid_buffer_refill_fault.sv"
        ),
    )
    repaired_report = analyze_skid_buffer_waveform(
        resolved_root,
        repaired["waveform"],
        rtl_path=Path("examples/skid_buffer/rtl/skid_buffer.sv"),
    )
    if before_report.passed or len(before_report.findings) != 1:
        raise RuntimeError("skid-buffer fault did not produce one exact debug finding")
    if not repaired_report.passed:
        raise RuntimeError("production skid-buffer RTL retained debug findings")
    finding = before_report.findings[0]
    if ".refill-ready." not in finding.finding_id:
        raise RuntimeError("skid-buffer fault finding is not refill readiness")
    proposal_path = output / "repair-proposal.json"
    debug_path = output / "debug-session.json"
    proposal = propose_skid_buffer_repairs(
        before_report,
        report_uri=debug_path.relative_to(resolved_root).as_posix(),
    )
    focus = skid_buffer_repair_focus(before_report)
    before_focus_path = output / "focus-before.sucl"
    repaired_focus_path = output / "focus-after.sucl"
    _write_json(debug_path, before_report.payload())
    _write_json(proposal_path, proposal.payload())
    before_focus_path.write_text(surfer_command_file(focus), encoding="utf-8")
    repaired_focus = WaveformFocus(
        repaired["waveform"].relative_to(resolved_root).as_posix(),
        focus.start_fs,
        focus.end_fs,
        focus.signals,
        focus.markers_fs,
    )
    repaired_focus_path.write_text(
        surfer_command_file(repaired_focus),
        encoding="utf-8",
    )

    marker = finding.waveform_anchor.markers_fs[0]
    before_ready = _ready_before(before["waveform"], marker)
    repaired_ready = _ready_before(repaired["waveform"], marker)
    if before_ready != 0 or repaired_ready != 1:
        raise RuntimeError("skid-buffer waveform difference is not visibly causal")
    comparison: dict[str, Any] = {
        "before_s_ready": before_ready,
        "finding_id": finding.finding_id,
        "focus_end_fs": focus.end_fs,
        "focus_start_fs": focus.start_fs,
        "marker_fs": marker,
        "repaired_s_ready": repaired_ready,
        "schema": "openrtl.skid-buffer-visual-comparison.v1",
        "status": "visibly_distinct",
    }
    comparison_path = output / "comparison.json"
    _write_json(comparison_path, comparison)

    evidence: dict[str, Any] = {
        "artifacts": {
            "before_log": _evidence(resolved_root, before["log"]),
            "before_results": _evidence(resolved_root, before["results"]),
            "before_waveform": _evidence(resolved_root, before["waveform"]),
            "comparison": _evidence(resolved_root, comparison_path),
            "debug_session": _evidence(resolved_root, debug_path),
            "before_focus": _evidence(resolved_root, before_focus_path),
            "repair_proposal": _evidence(resolved_root, proposal_path),
            "repaired_log": _evidence(resolved_root, repaired["log"]),
            "repaired_focus": _evidence(resolved_root, repaired_focus_path),
            "repaired_results": _evidence(resolved_root, repaired["results"]),
            "repaired_waveform": _evidence(resolved_root, repaired["waveform"]),
        },
        "production_rtl": _evidence(resolved_root, production),
        "requirements": [
            "skid.reset",
            "skid.accept",
            "skid.backpressure",
            "skid.order",
            "skid.refill",
        ],
        "schema": "openrtl.skid-buffer-evidence.v1",
        "status": "passed",
    }
    evidence_path = output / "evidence.json"
    _write_json(evidence_path, evidence)
    payload: dict[str, Any] = {
        "before_finding_ids": [value.finding_id for value in before_report.findings],
        "before_waveform": before["waveform"].relative_to(resolved_root).as_posix(),
        "comparison": comparison_path.relative_to(resolved_root).as_posix(),
        "evidence": evidence_path.relative_to(resolved_root).as_posix(),
        "before_focus": before_focus_path.relative_to(resolved_root).as_posix(),
        "proposal": proposal_path.relative_to(resolved_root).as_posix(),
        "repaired_finding_ids": [value.finding_id for value in repaired_report.findings],
        "repaired_focus": repaired_focus_path.relative_to(resolved_root).as_posix(),
        "repaired_waveform": repaired["waveform"].relative_to(resolved_root).as_posix(),
        "schema": "openrtl.skid-buffer-case.v1",
        "status": "passed",
        "visual_evidence": comparison,
    }
    _write_json(output / "summary.json", payload)
    return payload


def _prepare_output(root: Path, candidate: Path) -> Path:
    build_root = (root / "build").resolve()
    selected = candidate if candidate.is_absolute() else root / candidate
    resolved = selected.resolve()
    if not resolved.is_relative_to(build_root) or resolved.is_symlink():
        raise ValueError("skid-buffer output must be a non-symlink inside build")
    marker = resolved / ".openrtl-skid-buffer-case-owner"
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError("skid-buffer output path is not a directory")
        entries = {value.name for value in resolved.iterdir()}
        if entries - _KNOWN_OUTPUTS:
            raise ValueError("skid-buffer output contains unowned entries")
        if entries and (
            not marker.is_file()
            or marker.is_symlink()
            or marker.read_text(encoding="utf-8") != _MARKER
        ):
            raise ValueError("skid-buffer output ownership marker is invalid")
        for value in resolved.iterdir():
            if value.is_symlink():
                raise ValueError("skid-buffer output contains a symlink")
            if value.is_dir():
                shutil.rmtree(value)
            else:
                value.unlink()
    else:
        resolved.mkdir(parents=True)
    marker.write_text(_MARKER, encoding="utf-8")
    return resolved


def _run_simulation(
    root: Path,
    output: Path,
    source: Path,
    toolchain: VerilatorToolchain,
    *,
    expect_pass: bool,
    timeout_seconds: int,
) -> dict[str, Path]:
    output.mkdir()
    temporary = output / "tmp"
    temporary.mkdir()
    paths = {
        "log": output / "run.log",
        "results": output / "results.xml",
        "waveform": output / "waves.vcd",
        "simulation_build": output / "sim_build",
    }
    command = [
        str(toolchain.make),
        "-C",
        str(root / "examples/skid_buffer/dv"),
        "SIM=verilator",
        f"VERILATOR_BIN_DIR={toolchain.verilator.parent}",
        f"SIM_BUILD={paths['simulation_build']}",
        f"COCOTB_RESULTS_FILE={paths['results']}",
        f"SIM_ARGS=--trace --trace-file {paths['waveform']}",
        f"VERILOG_SOURCES={source}",
        "MODULE=test_skid_buffer",
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
    with paths["log"].open("wb") as stream:
        try:
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
            raise RuntimeError("skid-buffer simulation exceeded its timeout") from error
    if expect_pass != (completed.returncode == 0):
        expected = "pass" if expect_pass else "fail"
        raise RuntimeError(f"skid-buffer simulation did not {expected}; inspect {paths['log']}")
    _verify_attempt(paths, expect_pass=expect_pass)
    return paths


def _verify_attempt(paths: dict[str, Path], *, expect_pass: bool) -> None:
    for name in ("log", "results", "waveform"):
        path = paths[name]
        if not path.is_file() or path.is_symlink() or path.stat().st_size < 1:
            raise RuntimeError(f"skid-buffer simulation artifact is missing: {path.name}")
    log = paths["log"].read_bytes()
    if b"%Warning-" in log or b"WIDTHEXPAND" in log or b"CMPCONST" in log:
        raise RuntimeError("skid-buffer simulation emitted a Verilator warning")
    try:
        root = ET.fromstring(paths["results"].read_bytes())
    except ET.ParseError as error:
        raise RuntimeError("skid-buffer results XML is invalid") from error
    cases = list(root.iter("testcase"))
    failed = bool(list(root.iter("failure")) or list(root.iter("error")))
    if len(cases) != 1 or failed == expect_pass:
        raise RuntimeError("skid-buffer results do not match the expected outcome")
    waveform = paths["waveform"].read_bytes()
    if b"$timescale" not in waveform or b"$enddefinitions $end" not in waveform:
        raise RuntimeError("skid-buffer waveform is incomplete")
    simulation_build = paths["simulation_build"]
    if (
        not simulation_build.is_dir()
        or simulation_build.is_symlink()
        or not any(simulation_build.iterdir())
    ):
        raise RuntimeError("skid-buffer simulation build is missing")


def _ready_before(trace: Path, marker_fs: int) -> int:
    index = VcdIndex.parse(trace.read_text(encoding="utf-8"))
    value = index.value_before("skid_buffer.s_ready", marker_fs)
    if value not in ("0", "1"):
        raise RuntimeError("skid-buffer ready signal is unknown at the finding marker")
    return int(value)


def _evidence(root: Path, path: Path) -> dict[str, str | int]:
    content = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
