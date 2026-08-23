from __future__ import annotations

import io
from pathlib import Path
import subprocess
import tempfile
from typing import BinaryIO, cast
import unittest
from unittest.mock import patch

from tools import validate
from tools.verilator_canary import VerilatorToolchain, run_verilator_canary


class VerilatorCanaryAutomationTest(unittest.TestCase):
    def test_default_validation_does_not_select_external_toolchain(self) -> None:
        output = io.StringIO()
        with (
            patch.object(validate, "_validate_text_files"),
            patch.object(validate, "_validate_architecture"),
            patch("tools.validate.compileall.compile_dir", return_value=True),
            patch.object(validate, "_run_tests"),
            patch.object(validate, "run_verilator_canary") as run_canary,
            patch("sys.stdout", output),
        ):
            self.assertEqual(validate.main(()), 0)
        run_canary.assert_not_called()
        self.assertIn("CHECKPOINT verilator_cocotb_canary not_selected", output.getvalue())

    def test_tool_options_require_explicit_opt_in(self) -> None:
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            validate._parse_arguments(("--verilator-executable", "/opt/verilator"))

    def test_selected_canary_uses_exact_command_and_retains_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            rtl = root / "examples/fifo/rtl/sync_fifo.sv"
            makefile = root / "examples/fifo/dv/Makefile"
            test_module = root / "examples/fifo/dv/test_sync_fifo.py"
            for fixture_path in (rtl, makefile, test_module):
                fixture_path.parent.mkdir(parents=True, exist_ok=True)
                fixture_path.write_text("fixture\n", encoding="utf-8")
            bin_directory = root / "bin"
            bin_directory.mkdir()
            executables: dict[str, Path] = {}
            for name in ("verilator", "make", "cocotb-config"):
                executable = bin_directory / name
                executable.write_text("#!/bin/sh\n", encoding="utf-8")
                executable.chmod(0o700)
                executables[name] = executable
            toolchain = VerilatorToolchain(
                executables["verilator"],
                executables["make"],
                executables["cocotb-config"],
            )
            output_directory = root / "build/verilator-fifo-canary"
            calls: list[tuple[list[str], dict[str, object]]] = []

            def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
                calls.append((command, kwargs))
                log_stream = cast(BinaryIO, kwargs["stdout"])
                log_stream.write(b"TESTS=1 PASS=1 FAIL=0 SKIP=0\n")
                (output_directory / "results.xml").write_text(
                    '<testsuites><testsuite><testcase name="fifo" /></testsuite></testsuites>\n',
                    encoding="utf-8",
                )
                (output_directory / "waves.vcd").write_text(
                    "$timescale 1 ns $end\n$enddefinitions $end\n",
                    encoding="utf-8",
                )
                (output_directory / "sim_build").mkdir()
                (output_directory / "sim_build/Vtop").write_bytes(b"fixture")
                return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

            artifacts = run_verilator_canary(
                root,
                output_directory,
                toolchain,
                runner=runner,
            )

            self.assertEqual(len(calls), 1)
            command, keyword_arguments = calls[0]
            self.assertEqual(command[0], str(executables["make"]))
            self.assertIn("SIM=verilator", command)
            self.assertIn(f"SIM_BUILD={artifacts.simulation_build}", command)
            self.assertIn(f"COCOTB_RESULTS_FILE={artifacts.results}", command)
            self.assertIn(f"SIM_ARGS=--trace --trace-file {artifacts.waveform}", command)
            self.assertEqual(
                set(cast(dict[str, str], keyword_arguments["env"])),
                {"PATH", "PYTHONPATH", "RANDOM_SEED", "TMPDIR"},
            )
            self.assertTrue(artifacts.log.is_file())
            self.assertTrue(artifacts.results.is_file())
            self.assertTrue(artifacts.waveform.is_file())
            self.assertTrue((output_directory / ".complete").is_file())

    def test_unowned_output_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for relative_path in (
                "examples/fifo/rtl/sync_fifo.sv",
                "examples/fifo/dv/Makefile",
                "examples/fifo/dv/test_sync_fifo.py",
            ):
                fixture_path = root / relative_path
                fixture_path.parent.mkdir(parents=True, exist_ok=True)
                fixture_path.write_text("fixture\n", encoding="utf-8")
            output_directory = root / "build/verilator-fifo-canary"
            output_directory.mkdir(parents=True)
            (output_directory / "notes.txt").write_text("user data\n", encoding="utf-8")
            executable = root / "tool"
            executable.write_text("#!/bin/sh\n", encoding="utf-8")
            executable.chmod(0o700)
            toolchain = VerilatorToolchain(executable, executable, executable)

            with self.assertRaisesRegex(RuntimeError, "unowned entries"):
                run_verilator_canary(root, output_directory, toolchain)


if __name__ == "__main__":
    unittest.main()
