"""Verilator/cocotb simulation boundary with deterministic run records."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from openrtl.domain._validation import identifier, relative_path


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[tuple[str, ...], str, int], Awaitable[ProcessResult]]


@dataclass(frozen=True)
class SimulationRequest:
    run_id: str
    top: str
    sources: tuple[str, ...]
    test_module: str
    build_directory: str
    trace_uri: str
    seed: int = 1
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", identifier(self.run_id, "run_id"))
        object.__setattr__(self, "top", identifier(self.top, "top"))
        if not self.sources or len(set(self.sources)) != len(self.sources):
            raise ValueError("simulation sources must be non-empty and unique")
        object.__setattr__(self, "sources", tuple(relative_path(value) for value in self.sources))
        object.__setattr__(self, "test_module", identifier(self.test_module, "test_module"))
        object.__setattr__(self, "build_directory", relative_path(self.build_directory))
        object.__setattr__(self, "trace_uri", relative_path(self.trace_uri))
        if self.seed < 0 or self.timeout_seconds < 1:
            raise ValueError("simulation seed and timeout are invalid")


class SimulationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class SimulationResult:
    run_id: str
    status: SimulationStatus
    compile_command: tuple[str, ...]
    test_command: tuple[str, ...]
    compile_output: ProcessResult
    test_output: ProcessResult | None
    trace_uri: str | None
    failure_signature: str | None = None


class VerilatorBackend:
    """Compile and run cocotb through explicit injected process execution."""

    def __init__(self, runner: ProcessRunner, executable: str = "/usr/bin/verilator") -> None:
        if not executable.startswith("/"):
            raise ValueError("Verilator executable must be absolute")
        self._runner = runner
        self._executable = executable

    async def run(self, request: SimulationRequest, workspace: str) -> SimulationResult:
        compile_command = (
            self._executable,
            "--binary",
            "--trace",
            "--assert",
            "--top-module",
            request.top,
            "-Mdir",
            request.build_directory,
            *request.sources,
        )
        compiled = await self._runner(compile_command, workspace, request.timeout_seconds)
        test_command = (
            "/usr/bin/env",
            f"RANDOM_SEED={request.seed}",
            f"MODULE={request.test_module}",
            f"TOPLEVEL={request.top}",
            f"SIM_BUILD={request.build_directory}",
            "make",
            "-f",
            "Makefile",
        )
        if compiled.exit_code != 0:
            return SimulationResult(
                request.run_id,
                SimulationStatus.FAILED,
                compile_command,
                test_command,
                compiled,
                None,
                None,
                "verilator.compile.failed",
            )
        tested = await self._runner(test_command, workspace, request.timeout_seconds)
        status = SimulationStatus.PASSED if tested.exit_code == 0 else SimulationStatus.FAILED
        return SimulationResult(
            request.run_id,
            status,
            compile_command,
            test_command,
            compiled,
            tested,
            request.trace_uri,
            None if status is SimulationStatus.PASSED else "cocotb.test.failed",
        )
