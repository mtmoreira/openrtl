"""Explicit, artifact-preserving Verilator/cocotb validation automation."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
from typing import Callable
import xml.etree.ElementTree as ET


_MARKER_TEXT = "openrtl-verilator-canary-v1\n"
_KNOWN_OUTPUTS = frozenset(
    {
        ".complete",
        ".openrtl-verilator-canary-owner",
        "canary.log",
        "results.xml",
        "sim_build",
        "tmp",
        "waves.vcd",
    }
)
_MAX_LOG_BYTES = 8 * 1024 * 1024
_MAX_RESULTS_BYTES = 1024 * 1024
_MAX_WAVEFORM_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class VerilatorToolchain:
    verilator: Path
    make: Path
    cocotb_config: Path


@dataclass(frozen=True)
class CanaryArtifacts:
    output_directory: Path
    log: Path
    results: Path
    waveform: Path
    simulation_build: Path


CommandRunner = Callable[..., subprocess.CompletedProcess[bytes]]


def _resolve_executable(name: str, override: str | None) -> Path:
    candidate = override if override is not None else shutil.which(name)
    if candidate is None:
        raise RuntimeError(f"required executable is unavailable: {name}")
    executable = Path(candidate)
    if not executable.is_absolute():
        raise RuntimeError(f"required executable must resolve to an absolute path: {name}")
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise RuntimeError(f"required executable is not executable: {name}")
    return executable


def discover_verilator_toolchain(
    *,
    verilator: str | None = None,
    make: str | None = None,
    cocotb_config: str | None = None,
) -> VerilatorToolchain:
    """Resolve every selected executable before the simulation has effects."""

    return VerilatorToolchain(
        verilator=_resolve_executable("verilator", verilator),
        make=_resolve_executable("make", make),
        cocotb_config=_resolve_executable("cocotb-config", cocotb_config),
    )


def _prepare_output_directory(root: Path, output_directory: Path) -> CanaryArtifacts:
    build_path = root / "build"
    if build_path.is_symlink():
        raise RuntimeError("repository build root must not be a symlink")
    build_root = build_path.resolve()
    candidate = output_directory if output_directory.is_absolute() else root / output_directory
    if candidate.is_symlink():
        raise RuntimeError("Verilator output directory must not be a symlink")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(build_root):
        raise RuntimeError("Verilator output directory must be inside the repository build root")

    if resolved.exists():
        if not resolved.is_dir():
            raise RuntimeError("Verilator output path is not a directory")
        entries = {entry.name for entry in resolved.iterdir()}
        unknown = sorted(entries - _KNOWN_OUTPUTS)
        if unknown:
            raise RuntimeError(f"Verilator output directory contains unowned entries: {', '.join(unknown)}")
        marker = resolved / ".openrtl-verilator-canary-owner"
        if entries and (
            not marker.is_file()
            or marker.is_symlink()
            or marker.read_text(encoding="utf-8") != _MARKER_TEXT
        ):
            raise RuntimeError("Verilator output directory has no valid ownership marker")
        for entry in resolved.iterdir():
            if entry.is_symlink():
                raise RuntimeError(f"Verilator output entry must not be a symlink: {entry.name}")
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
    else:
        resolved.mkdir(parents=True)

    (resolved / ".openrtl-verilator-canary-owner").write_text(
        _MARKER_TEXT,
        encoding="utf-8",
    )
    temporary_directory = resolved / "tmp"
    temporary_directory.mkdir()
    return CanaryArtifacts(
        output_directory=resolved,
        log=resolved / "canary.log",
        results=resolved / "results.xml",
        waveform=resolved / "waves.vcd",
        simulation_build=resolved / "sim_build",
    )


def _read_bounded(path: Path, maximum_bytes: int, label: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"Verilator {label} collateral is missing")
    size = path.stat().st_size
    if size < 1 or size > maximum_bytes:
        raise RuntimeError(f"Verilator {label} collateral has an invalid size")
    return path.read_bytes()


def _verify_artifacts(artifacts: CanaryArtifacts) -> None:
    log_data = _read_bounded(artifacts.log, _MAX_LOG_BYTES, "log")
    if b"%Warning-" in log_data or b"WIDTHEXPAND" in log_data or b"CMPCONST" in log_data:
        raise RuntimeError("Verilator canary emitted a lint warning")
    if b"TESTS=1 PASS=1 FAIL=0 SKIP=0" not in log_data:
        raise RuntimeError("Verilator canary pass summary is missing")

    results_data = _read_bounded(artifacts.results, _MAX_RESULTS_BYTES, "results")
    try:
        results_root = ET.fromstring(results_data)
    except ET.ParseError as error:
        raise RuntimeError("Verilator results XML is invalid") from error
    cases = list(results_root.iter("testcase"))
    if len(cases) != 1 or list(results_root.iter("failure")) or list(results_root.iter("error")):
        raise RuntimeError("Verilator results XML does not contain one passing test")

    waveform_data = _read_bounded(artifacts.waveform, _MAX_WAVEFORM_BYTES, "waveform")
    if b"$timescale" not in waveform_data or b"$enddefinitions $end" not in waveform_data:
        raise RuntimeError("Verilator waveform is not a complete VCD trace")
    if (
        not artifacts.simulation_build.is_dir()
        or artifacts.simulation_build.is_symlink()
        or not any(artifacts.simulation_build.iterdir())
    ):
        raise RuntimeError("Verilator simulation build collateral is missing")


def _simulation_environment(
    root: Path,
    toolchain: VerilatorToolchain,
    artifacts: CanaryArtifacts,
) -> dict[str, str]:
    path_entries = (
        toolchain.verilator.parent,
        toolchain.cocotb_config.parent,
        toolchain.make.parent,
        Path("/usr/bin"),
        Path("/bin"),
    )
    path_value = os.pathsep.join(dict.fromkeys(str(entry) for entry in path_entries))
    python_paths = [root / "src", root]
    agentrig_source = root.parent / "agentrig" / "src"
    if agentrig_source.is_dir():
        python_paths.append(agentrig_source)
    return {
        "PATH": path_value,
        "PYTHONPATH": os.pathsep.join(str(path) for path in python_paths),
        "RANDOM_SEED": "1",
        "TMPDIR": str(artifacts.output_directory / "tmp"),
    }


def run_verilator_canary(
    root: Path,
    output_directory: Path,
    toolchain: VerilatorToolchain,
    *,
    timeout_seconds: int = 120,
    runner: CommandRunner = subprocess.run,
) -> CanaryArtifacts:
    """Run the selected local toolchain and retain only verified collateral."""

    if timeout_seconds < 1 or timeout_seconds > 600:
        raise ValueError("Verilator timeout must be between 1 and 600 seconds")
    resolved_root = root.resolve(strict=True)
    required = (
        resolved_root / "examples/fifo/rtl/sync_fifo.sv",
        resolved_root / "examples/fifo/dv/Makefile",
        resolved_root / "examples/fifo/dv/test_sync_fifo.py",
    )
    if any(not source.is_file() for source in required):
        raise RuntimeError("Verilator canary root is missing required FIFO collateral")

    artifacts = _prepare_output_directory(resolved_root, output_directory)
    command = [
        str(toolchain.make),
        "-C",
        str(resolved_root / "examples/fifo/dv"),
        "SIM=verilator",
        f"VERILATOR_BIN_DIR={toolchain.verilator.parent}",
        f"SIM_BUILD={artifacts.simulation_build}",
        f"COCOTB_RESULTS_FILE={artifacts.results}",
        f"SIM_ARGS=--trace --trace-file {artifacts.waveform}",
    ]
    environment = _simulation_environment(resolved_root, toolchain, artifacts)
    try:
        with artifacts.log.open("wb") as log_stream:
            completed = runner(
                command,
                cwd=resolved_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                check=False,
            )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Verilator canary exceeded its configured timeout") from error
    if completed.returncode != 0:
        raise RuntimeError(f"Verilator canary failed; inspect {artifacts.log}")

    _verify_artifacts(artifacts)
    rtl_digest = hashlib.sha256(required[0].read_bytes()).hexdigest()
    (artifacts.output_directory / ".complete").write_text(
        f"rtl_sha256={rtl_digest}\n",
        encoding="utf-8",
    )
    return artifacts


def describe_artifacts(artifacts: CanaryArtifacts) -> tuple[str, ...]:
    return (
        f"COLLATERAL canary_log={artifacts.log}",
        f"COLLATERAL results={artifacts.results}",
        f"COLLATERAL waveform={artifacts.waveform}",
        f"COLLATERAL simulation_build={artifacts.simulation_build}",
    )
