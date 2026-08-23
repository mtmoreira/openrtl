from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import tempfile
from typing import BinaryIO, cast
import unittest
from unittest.mock import patch

from openrtl.adapters import load_fifo_canary_evidence
from openrtl.application import FIFO_RUN_REF, FIFO_SOURCE_REFS, run_scripted_fifo
from openrtl.cli import main
from openrtl.domain import InteractionMode, RunStatus, TrustLevel, WaveformAnchor
from tools.verilator_canary import VerilatorToolchain, run_verilator_canary


class CanaryEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name).resolve()
        fixtures = {
            "examples/fifo/spec.md": "# FIFO\n",
            "examples/fifo/model.py": "# model\n",
            "examples/fifo/rtl/sync_fifo.sv": "module sync_fifo;\nendmodule\n",
            "examples/fifo/dv/Makefile": "# make\n",
            "examples/fifo/dv/test_sync_fifo.py": "# dv\n",
        }
        for relative_path, content in fixtures.items():
            fixture = self.root / relative_path
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_text(content, encoding="utf-8")
        executable = self.root / "tool"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o700)
        toolchain = VerilatorToolchain(executable, executable, executable)
        self.output = self.root / "build/verilator-fifo-canary"
        run_verilator_canary(self.root, self.output, toolchain, runner=self._runner)

    def _runner(
        self,
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        log_stream = cast(BinaryIO, kwargs["stdout"])
        events = (
            {"level": 1, "read": False, "write": True},
            {"level": 0, "read": True, "write": False},
            {"level": 1, "read": True, "write": True},
        )
        for timestamp, fields in enumerate(events, start=1):
            log_stream.write(
                (
                    json.dumps(
                        {
                            "component": "fifo.scoreboard",
                            "event": "transfer.accepted",
                            "fields": fields,
                            "level": "info",
                            "message": "FIFO transfer checked",
                            "requirement_ids": ["fifo.order"],
                            "timestamp_fs": timestamp * 1_000_000,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    + "\n"
                ).encode()
            )
        log_stream.write(b"TESTS=1 PASS=1 FAIL=0 SKIP=0\n")
        (self.output / "results.xml").write_text(
            '<testsuites><testsuite><testcase name="randomized_fifo_scoreboard" /></testsuite></testsuites>\n',
            encoding="utf-8",
        )
        (self.output / "waves.vcd").write_text(
            "$timescale 1 ns $end\n"
            "$scope module sync_fifo $end\n"
            "$var wire 1 ! wr_valid $end\n"
            "$var wire 1 \" rd_valid $end\n"
            "$var wire 3 # level [2:0] $end\n"
            "$upscope $end\n"
            "$enddefinitions $end\n"
            "#0\n0!\n0\"\nb000 #\n"
            "#1\n1!\nb001 #\n"
            "#2\n1\"\nb000 #\n",
            encoding="utf-8",
        )
        (self.output / "sim_build").mkdir()
        (self.output / "sim_build/Vtop").write_bytes(b"fixture")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    def _load(self) -> object:
        return load_fifo_canary_evidence(
            self.root,
            self.output / "evidence.json",
            (*FIFO_SOURCE_REFS, FIFO_RUN_REF),
        )

    def test_verified_canary_drives_package_and_learning_evidence(self) -> None:
        verified = load_fifo_canary_evidence(
            self.root,
            self.output / "evidence.json",
            (*FIFO_SOURCE_REFS, FIFO_RUN_REF),
        )
        build = run_scripted_fifo(self.root, InteractionMode.BUILD, verified)
        learn = run_scripted_fifo(self.root, InteractionMode.LEARN, verified)

        self.assertEqual(verified.run.status, RunStatus.PASSED)
        self.assertEqual(build.package.trust, TrustLevel.SIMULATION_VERIFIED)
        self.assertTrue(build.package.publication_ready)
        self.assertTrue(all(row.covered for row in build.coverage))
        self.assertEqual(build.knowledge.run(verified.run.run_id), verified.run)
        self.assertIsNotNone(learn.learning)
        self.assertTrue(
            any(isinstance(anchor, WaveformAnchor) for anchor in verified.evidence.anchors)
        )

        output = io.StringIO()
        with patch("sys.stdout", output):
            result = main(
                (
                    "verified-canary",
                    "--root",
                    str(self.root),
                    "--manifest",
                    "build/verilator-fifo-canary/evidence.json",
                )
            )
        self.assertEqual(result, 0)
        report = json.loads(output.getvalue())
        self.assertEqual(report["run_status"], "passed")
        self.assertTrue(report["publication_ready"])

    def test_changed_or_missing_collateral_is_rejected(self) -> None:
        waveform = self.output / "waves.vcd"
        waveform.write_text(waveform.read_text(encoding="utf-8") + "#3\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "digest or size mismatch"):
            self._load()

        run_verilator_canary(
            self.root,
            self.output,
            VerilatorToolchain(self.root / "tool", self.root / "tool", self.root / "tool"),
            runner=self._runner,
        )
        (self.output / "results.xml").unlink()
        with self.assertRaisesRegex(ValueError, "results is missing"):
            self._load()

    def test_symlinked_or_failed_collateral_is_rejected(self) -> None:
        log = self.output / "canary.log"
        retained_log = self.output / "retained.log"
        log.rename(retained_log)
        log.symlink_to(retained_log)
        with self.assertRaisesRegex(ValueError, "log path contains a symlink"):
            self._load()

        log.unlink()
        retained_log.rename(log)
        manifest = self.output / "evidence.json"
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["status"] = "failed"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a passing supported schema"):
            self._load()


if __name__ == "__main__":
    unittest.main()
