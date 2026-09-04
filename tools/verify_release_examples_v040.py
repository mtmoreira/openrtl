"""Exercise the installed 0.4 wheel through leaf simulation and package reuse."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from typing import Any

from tools.composed_package_matrix import MATRIX, run_matrix
from tools.skid_buffer_case import run_skid_buffer_case
from tools.verilator_canary import discover_verilator_toolchain, run_verilator_canary


def validate_matrix_proof(summary: dict[str, Any]) -> None:
    """Require all reviewed configurations and producer/consumer waveform equality."""
    if (summary.get("schema") != "openrtl.composed-package-matrix.v1"
            or summary.get("status") != "passed" or summary.get("case_count") != 3
            or summary.get("provider_called") is not False
            or summary.get("remote_operations") is not False):
        raise ValueError("installed matrix summary is invalid")
    cases = summary.get("cases")
    if not isinstance(cases, list) or len(cases) != len(MATRIX):
        raise ValueError("installed matrix cases are incomplete")
    for case, expected in zip(cases, MATRIX, strict=True):
        configuration = {"width": expected.width, "depth": expected.depth,
                         "capacity": expected.depth + 1, "seed": expected.seed}
        if (case.get("case_id") != expected.case_id
                or case.get("configuration") != configuration
                or case.get("coverage", {}).get("counts", {}).get("max_occupancy") != expected.depth + 1):
            raise ValueError("installed matrix configuration or coverage is invalid")
        producer = case.get("producer_waveform", {})
        consumer = case.get("consumer_waveform", {})
        if (not producer.get("sha256") or producer.get("sha256") != consumer.get("sha256")):
            raise ValueError("installed producer and consumer waveforms differ")


def main() -> int:
    import openrtl

    root = Path(__file__).resolve().parents[1]
    package = Path(openrtl.__file__).resolve(strict=True)
    if version("openrtl") != "0.4.0" or root in package.parents:
        raise RuntimeError("0.4 acceptance requires an installed wheel outside the examples")
    output = root / "build/release-v040"
    output.mkdir(parents=True, exist_ok=False)
    toolchain = discover_verilator_toolchain()
    fifo = run_verilator_canary(root, output / "fifo", toolchain, timeout_seconds=180)
    run_skid_buffer_case(root, output / "skid", toolchain, timeout_seconds=180)
    summary = run_matrix(root, output / "matrix", fifo.evidence_manifest,
                         output / "skid/evidence.json", toolchain, 180)
    validate_matrix_proof(summary)
    matrix = output / "matrix/matrix-evidence.json"
    document = {"schema": "openrtl.installed-examples-v040.v1", "status": "passed",
                "case_count": summary["case_count"],
                "matrix_sha256": hashlib.sha256(matrix.read_bytes()).hexdigest(),
                "provider_called": False, "remote_operations": False}
    (output / "acceptance.json").write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    print("CHECKPOINT installed_v040_leaf_and_composed_examples passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
