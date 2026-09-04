"""Run a bounded matrix of real composed-package producer and consumer simulations."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.composed_package_case import (  # noqa: E402
    read_file, record, run_case, sha, validate_configuration, write_json,
)
from tools.verilator_canary import (  # noqa: E402
    VerilatorToolchain, discover_verilator_toolchain,
)


@dataclass(frozen=True, order=True)
class MatrixConfiguration:
    width: int
    depth: int
    seed: int

    @property
    def case_id(self) -> str:
        return f"w{self.width}-d{self.depth}-s{self.seed}"


MATRIX = (
    MatrixConfiguration(4, 2, 7),
    MatrixConfiguration(8, 4, 33),
    MatrixConfiguration(16, 3, 91),
)

CaseRunner = Callable[
    [Path, Path, Path, Path, VerilatorToolchain, int, int, int, int],
    dict[str, Any],
]


def validate_matrix(configurations: tuple[MatrixConfiguration, ...]) -> None:
    if not 2 <= len(configurations) <= 8:
        raise ValueError("matrix must contain between two and eight cases")
    identities = set()
    for configuration in configurations:
        validate_configuration(configuration.width, configuration.depth, configuration.seed)
        if configuration.case_id in identities:
            raise ValueError("matrix configurations must be unique")
        identities.add(configuration.case_id)
    if len({value.width for value in configurations}) < 2:
        raise ValueError("matrix must vary width")
    if len({value.depth for value in configurations}) < 2:
        raise ValueError("matrix must vary depth")
    if not any(value.depth & (value.depth - 1) for value in configurations):
        raise ValueError("matrix must include a non-power-of-two depth")


def run_matrix(root: Path, output: Path, fifo_evidence: Path, skid_evidence: Path,
               toolchain: VerilatorToolchain, timeout: int,
               configurations: tuple[MatrixConfiguration, ...] = MATRIX,
               case_runner: CaseRunner = run_case) -> dict[str, Any]:
    validate_matrix(configurations)
    root = root.resolve(strict=True)
    output = output if output.is_absolute() else root / output
    if not output.is_relative_to(root / "build") or output == root / "build" or ".." in output.parts:
        raise ValueError("matrix output must be a new bounded build subdirectory")
    if output.exists() or output.is_symlink() or any(value.is_symlink() for value in output.parents):
        raise ValueError("matrix output already exists or contains a symlink")
    source_paths = (
        root / "examples/fifo/rtl/sync_fifo.sv",
        root / "examples/skid_buffer/rtl/skid_buffer.sv",
        root / "examples/composed_stream/rtl/fifo_skid_stream.sv",
    )
    source_digests = [sha(read_file(value)) for value in source_paths]
    output.mkdir(parents=True)
    (output / ".openrtl-composed-matrix-owner").write_text(
        "openrtl-composed-package-matrix-v1\n", encoding="utf-8"
    )
    cases = []
    for configuration in configurations:
        case_output = output / "cases" / configuration.case_id
        result = case_runner(root, case_output, fifo_evidence, skid_evidence, toolchain,
                             timeout, configuration.width, configuration.depth,
                             configuration.seed)
        expected = {"width": configuration.width, "depth": configuration.depth,
                    "capacity": configuration.depth + 1, "seed": configuration.seed}
        if (result.get("status") != "passed" or result.get("configuration") != expected
                or result.get("provider_called") is not False):
            raise ValueError("matrix case outcome does not match its configuration")
        counts = result.get("coverage", {}).get("counts", {})
        if counts.get("max_occupancy") != configuration.depth + 1:
            raise ValueError("matrix case did not reach its configured capacity")
        cases.append({"case_id": configuration.case_id, "configuration": expected,
                      "evidence": record(output, case_output / "evidence.json"),
                      "producer_waveform": record(output, case_output / "producer-run/waves.vcd"),
                      "consumer_waveform": record(output, case_output / "consumer-run/waves.vcd"),
                      "lock_digest": result["lock_digest"],
                      "coverage": result["coverage"]})
    if source_digests != [sha(read_file(value)) for value in source_paths]:
        raise ValueError("matrix execution changed repository RTL")
    summary = {"schema": "openrtl.composed-package-matrix.v1", "status": "passed",
               "case_count": len(cases), "cases": cases,
               "source_sha256": source_digests, "provider_called": False,
               "remote_operations": False}
    write_json(output / "matrix-evidence.json", summary)
    return summary


def main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--fifo-evidence", type=Path, required=True)
    parser.add_argument("--skid-evidence", type=Path, required=True)
    parser.add_argument("--verilator-executable", required=True)
    parser.add_argument("--make-executable", required=True)
    parser.add_argument("--cocotb-config-executable", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parsed = parser.parse_args(arguments)
    if not 1 <= parsed.timeout_seconds <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    toolchain = discover_verilator_toolchain(
        verilator=parsed.verilator_executable, make=parsed.make_executable,
        cocotb_config=parsed.cocotb_config_executable,
    )
    result = run_matrix(parsed.root, parsed.output_directory, parsed.fifo_evidence,
                        parsed.skid_evidence, toolchain, parsed.timeout_seconds)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
