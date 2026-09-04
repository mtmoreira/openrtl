"""Provider-free tests for the bounded composed package matrix."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tools.composed_package_matrix import MATRIX, MatrixConfiguration, run_matrix, validate_matrix
from tools.verilator_canary import VerilatorToolchain


class ComposedPackageMatrixTest(unittest.TestCase):
    def test_reviewed_matrix_varies_width_depth_seed_and_non_power_of_two(self) -> None:
        validate_matrix(MATRIX)
        self.assertEqual([value.case_id for value in MATRIX], ["w4-d2-s7", "w8-d4-s33", "w16-d3-s91"])

    def test_duplicate_small_or_single_axis_matrices_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "two and eight"):
            validate_matrix((MATRIX[0],))
        with self.assertRaisesRegex(ValueError, "unique"):
            validate_matrix((MATRIX[0], MATRIX[0], MATRIX[2]))
        with self.assertRaisesRegex(ValueError, "vary width"):
            validate_matrix((MatrixConfiguration(8, 2, 1), MatrixConfiguration(8, 3, 2)))
        with self.assertRaisesRegex(ValueError, "non-power-of-two"):
            validate_matrix((MatrixConfiguration(4, 2, 1), MatrixConfiguration(8, 4, 2)))

    def test_runner_binds_each_result_to_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for relative in ("examples/fifo/rtl/sync_fifo.sv", "examples/skid_buffer/rtl/skid_buffer.sv",
                             "examples/composed_stream/rtl/fifo_skid_stream.sv"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("module fixture; endmodule\n")
            calls = []

            def fake(case_root: Path, output: Path, fifo: Path, skid: Path,
                     toolchain: VerilatorToolchain, timeout: int, width: int,
                     depth: int, seed: int) -> dict[str, Any]:
                calls.append((width, depth, seed))
                (output / "producer-run").mkdir(parents=True)
                (output / "consumer-run").mkdir()
                for relative in ("producer-run/waves.vcd", "consumer-run/waves.vcd"):
                    (output / relative).write_text("$enddefinitions $end\n")
                payload = {"status": "passed", "provider_called": False,
                           "configuration": {"width": width, "depth": depth,
                                             "capacity": depth + 1, "seed": seed},
                           "coverage": {"counts": {"max_occupancy": depth + 1}},
                           "lock_digest": f"sha256:{width:064x}"}
                (output / "evidence.json").write_text(json.dumps(payload) + "\n")
                return payload

            toolchain = VerilatorToolchain(Path("/verilator"), Path("/make"), Path("/cocotb-config"))
            summary = run_matrix(root, Path("build/matrix"), Path("fifo"), Path("skid"),
                                 toolchain, 10, case_runner=fake)
            self.assertEqual(calls, [(4, 2, 7), (8, 4, 33), (16, 3, 91)])
            self.assertEqual(summary["case_count"], 3)
            self.assertFalse(summary["provider_called"])

    def test_mismatched_case_result_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for relative in ("examples/fifo/rtl/sync_fifo.sv", "examples/skid_buffer/rtl/skid_buffer.sv",
                             "examples/composed_stream/rtl/fifo_skid_stream.sv"):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("module fixture; endmodule\n")

            def wrong(*arguments: Any) -> dict[str, Any]:
                return {"status": "passed", "provider_called": False, "configuration": {}}

            toolchain = VerilatorToolchain(Path("/verilator"), Path("/make"), Path("/cocotb-config"))
            with self.assertRaisesRegex(ValueError, "outcome"):
                run_matrix(root, Path("build/matrix"), Path("fifo"), Path("skid"),
                           toolchain, 10, case_runner=wrong)


if __name__ == "__main__":
    unittest.main()
