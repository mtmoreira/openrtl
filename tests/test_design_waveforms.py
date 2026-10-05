"""Synthetic run evidence for browser waveform contracts; no live simulation."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path
import tempfile
import unittest
import uuid

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.application.design_waveforms import DesignWaveforms
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import STAGES, content_digest
from tests.test_design_agent import FakeExpert, specification


TRACE = ("$timescale 1 ns $end\n$scope module wire_top $end\n"
         "$var wire 1 ! valid $end\n$var wire 4 # data $end\n$upscope $end\n"
         "$enddefinitions $end\n#0\nx!\nbzzzz #\n#5\n0!\nb0011 #\n"
         "#10\n1!\nb1010 #\n#15\nz!\nb1111 #\n")


class DesignWaveformTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        self.store = DesignSessionStore(self.project, create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.agent = DesignAgent(self.store, self.expert)
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        for _ in STAGES:
            asyncio.run(self.agent.advance())
        self.viewer = DesignWaveforms(self.store)

    def record(self, *, passed: bool = False) -> tuple[str, str]:
        run_id = uuid.uuid4().hex
        evidence = self.project / "runs" / run_id / "evidence"
        evidence.mkdir(parents=True)
        data = TRACE.encode()
        (evidence / "waves.vcd").write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        before = self.store.read()
        report = {"schema": "openrtl.design-simulation.v2", "run_id": run_id,
                  "input_digest": design_input_digest(before),
                  "status": "passed" if passed else "failed", "evidence_kind": "synthetic_fixture_only",
                  "tests": ["transfer"] if passed else [], "model_tests": 1,
                  "artifacts": {"waves.vcd": {"path": "runs/" + run_id + "/evidence/waves.vcd",
                                                "bytes": len(data), "sha256": digest}},
                  "diagnostics": "synthetic fixture", "error_code": None}
        updated = copy.deepcopy(before)
        updated.update(simulation=report, status="needs_signoff" if passed else "needs_repair")
        self.store.save(before, updated, "simulation.completed", {"run_id": run_id})
        return run_id, "sha256:" + digest

    def test_failed_run_query_keeps_unknowns_high_impedance_and_bounded_window(self) -> None:
        run_id, digest = self.record()
        runs = self.viewer.runs()["runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["status"], runs[0]["trace_status"]), ("failed", "available"))
        catalog = self.viewer.catalog(run_id, "data")
        self.assertEqual(catalog["signal_names"], ["wire_top.data"])
        self.assertEqual(catalog["timescale_fs"], 1_000_000)
        window = self.viewer.query(run_id, digest, ["wire_top.valid", "wire_top.data"],
                                   0, 15_000_000, limit=2)
        valid, data = window["selected_signals"]
        self.assertEqual(valid["value_at_start"], "x")
        self.assertEqual([row["value"] for row in valid["transitions"]], ["x", "0"])
        self.assertTrue(valid["truncated"])
        self.assertEqual(data["value_at_start"], "zzzz")
        self.assertTrue(data["truncated"])
        narrowed = self.viewer.query(run_id, digest, ["wire_top.data"], 5_000_000, 10_000_000)
        self.assertEqual(narrowed["selected_signals"][0]["value_at_start"], "0011")
        self.assertEqual(narrowed["selected_signals"][0]["transitions"][-1]["value"], "1010")

    def test_exact_digest_and_containment_gate_every_view_and_attachment(self) -> None:
        run_id, digest = self.record(passed=True)
        selected = {"kind": "waveform", "run_id": run_id, "trace_digest": digest,
                    "signals": ["wire_top.valid"], "start_fs": 0, "end_fs": 10_000_000}
        self.assertEqual(self.viewer.attachment(selected)["run_id"], run_id)
        with self.assertRaisesRegex(ValueError, "identity_stale"):
            self.viewer.query(run_id, "sha256:" + "0"*64, selected["signals"], 0, 10_000_000)
        with self.assertRaisesRegex(ValueError, "signals_invalid"):
            self.viewer.query(run_id, digest, ["wire_top.valid", "wire_top.valid"], 0, 10_000_000)
        with self.assertRaisesRegex(ValueError, "window_invalid"):
            self.viewer.query(run_id, digest, selected["signals"], True, 10_000_000)
        path = self.project / "runs" / run_id / "evidence" / "waves.vcd"
        path.write_text(TRACE.replace("b0011 #", "b0001 #"))
        with self.assertRaisesRegex(ValueError, "trace_changed"):
            self.viewer.catalog(run_id)
        path.write_text(TRACE + "#20\n0!\n")
        with self.assertRaisesRegex(ValueError, "trace_changed"):
            self.viewer.catalog(run_id)

    def test_waveform_attachment_rechecks_run_and_stays_out_of_saved_prompt(self) -> None:
        run_id, digest = self.record()
        workspace = DesignWorkspace(self.agent)
        attachment = {"kind": "waveform", "run_id": run_id, "trace_digest": digest,
                      "signals": ["wire_top.valid"], "start_fs": 0, "end_fs": 10_000_000}
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await workspace.submit_question("PRIVATE WAVE QUESTION", client_operation_id=identifier,
                                            expected_revision=self.store.read()["revision"], attachment=attachment)
            await workspace._tasks[identifier]
            self.assertEqual(workspace.operation(identifier)["phase"], "completed")
        asyncio.run(scenario())
        self.assertIn(run_id, str(self.expert.seen[-1]))
        self.assertNotIn("PRIVATE WAVE QUESTION", str(self.store.read()))
        self.assertNotIn("PRIVATE WAVE QUESTION", str(self.store.events()))


if __name__ == "__main__":
    unittest.main()
