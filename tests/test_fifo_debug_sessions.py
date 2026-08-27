from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    analyze_fifo_waveform,
    fifo_repair_focus,
    propose_fifo_repairs,
)
from openrtl.application import ExpertRegistry, build_repair_proposal
from openrtl.cli import main
from openrtl.domain import (
    ExpertBinding,
    ExpertRole,
    ProjectKnowledgeBase,
    ProjectProfile,
    RuntimeProfile,
    SourceAnchor,
    ToolProfile,
)


class FifoDebugSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.trace = self.root / "waves.vcd"
        rtl = self.root / "examples/fifo/rtl/sync_fifo.sv"
        rtl.parent.mkdir(parents=True)
        rtl.write_text(
            "module sync_fifo;\n"
            "assign read_accepted = rd_valid && rd_ready;\n"
            "assign wr_ready = !full || read_accepted;\n"
            "assign write_accepted = wr_valid && wr_ready;\n"
            "always_ff @(posedge clk) begin\n"
            "  unique case ({write_accepted, read_accepted})\n"
            "  endcase\n"
            "end\n"
            "endmodule\n",
            encoding="utf-8",
        )

    def test_passing_trace_explains_transfers_backpressure_and_wrap(self) -> None:
        self.trace.write_text(render_fifo_trace(), encoding="utf-8")

        report = analyze_fifo_waveform(self.root, self.trace)

        self.assertTrue(report.passed)
        self.assertEqual(dict(report.metadata)["depth"], "2")
        self.assertEqual(
            [value.event for value in report.observations],
            [
                "write-transfer",
                "simultaneous-transfer",
                "write-transfer",
                "write-blocked",
                "simultaneous-transfer",
            ],
        )
        final = report.observations[-1]
        self.assertEqual(dict(final.signal_values)["rd_data"], "0xb")
        self.assertEqual(dict(final.signal_values)["level_after"], "2")
        self.assertEqual(report.waveform_anchor.markers_fs, (5_000_000, 15_000_000, 25_000_000, 35_000_000, 45_000_000))

    def test_level_bug_produces_requirement_linked_waveform_finding(self) -> None:
        self.trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")

        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
        )

        self.assertFalse(report.passed)
        level = next(value for value in report.findings if ".level." in value.finding_id)
        self.assertEqual(level.requirement_id, "fifo.write")
        self.assertEqual(level.expected, "2")
        self.assertEqual(level.observed, "1")
        self.assertEqual(level.waveform_anchor.markers_fs, (25_000_000,))

    def test_reset_edge_is_explained_as_reset_even_with_a_handshake_request(self) -> None:
        reset_trace = render_fifo_trace().replace(
            '#0\n0!\n1"\n1#',
            '#0\n0!\n0"\n1#',
            1,
        )
        self.trace.write_text(reset_trace, encoding="utf-8")

        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=0,
            end_fs=5_000_000,
        )

        self.assertEqual(report.observations[0].event, "reset-edge")
        self.assertEqual(report.observations[0].requirement_ids, ("fifo.reset",))

    def test_cli_writes_reviewable_debug_session_and_returns_finding_status(self) -> None:
        self.trace.write_text(render_fifo_trace(), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "waveform",
                    "diagnose-fifo",
                    "waves.vcd",
                    "--root",
                    str(self.root),
                    "--output",
                    "build/debug/session.json",
                )
            )

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        retained = json.loads(
            (self.root / "build/debug/session.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload, retained)
        self.assertEqual(payload["schema"], "openrtl.debug-session.v1")
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["waveform_anchor"]["trace_id"], f"{payload['session_id']}.trace")
        self.assertGreaterEqual(len(payload["source_anchors"]), 5)

    def test_unknown_signal_and_edge_free_window_fail_closed(self) -> None:
        self.trace.write_text(render_fifo_trace(), encoding="utf-8")
        with self.assertRaisesRegex(KeyError, "unknown waveform signal"):
            analyze_fifo_waveform(self.root, self.trace, hierarchy="other")
        with self.assertRaisesRegex(ValueError, "no rising"):
            analyze_fifo_waveform(
                self.root,
                self.trace,
                start_fs=1_000_000,
                end_fs=4_000_000,
            )

    def test_faulty_trace_produces_non_applying_evidence_complete_repair(self) -> None:
        self.trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")
        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
            rtl_path=Path("examples/fifo/rtl/sync_fifo.sv"),
        )

        proposal = propose_fifo_repairs(
            report,
            report_uri="build/debug/debug-session.json",
        )

        payload = proposal.payload()
        self.assertEqual(payload["schema"], "openrtl.repair-proposal.v1")
        self.assertFalse(payload["applies_changes"])
        self.assertEqual(payload["expert_role"], "diagnosis_closure_engineer")
        self.assertEqual(proposal.confidence_percent, 90)
        self.assertEqual(len(proposal.changes), 1)
        change = proposal.changes[0]
        self.assertEqual(change.change_id, "repair.change.level")
        self.assertEqual(change.requirement_ids, ("fifo.write",))
        self.assertTrue(change.source_anchors)
        self.assertEqual(change.waveform_anchors[0].markers_fs, (25_000_000,))
        focus = fifo_repair_focus(report)
        self.assertEqual((focus.start_fs, focus.end_fs), (24_000_000, 26_000_000))
        self.assertIn("sync_fifo.level", focus.signals)

    def test_repair_context_is_attached_to_diagnosis_engineer_plan(self) -> None:
        self.trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")
        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
            rtl_path=Path("examples/fifo/rtl/sync_fifo.sv"),
        )
        proposal = propose_fifo_repairs(
            report,
            report_uri="build/debug/debug-session.json",
        )
        profile = ProjectProfile(
            "local",
            (RuntimeProfile("reasoning", "openai", "gpt-selected", "codex.local"),),
            (
                ToolProfile(
                    "eda",
                    ("eda.simulate", "waveform.inspect"),
                    simulator="verilator",
                    waveform_viewer="surfer",
                ),
            ),
            (
                ExpertBinding(
                    ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
                    "reasoning",
                    "eda",
                ),
            ),
        )

        invocation = ExpertRegistry().plan(
            ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
            "Review the evidence-linked FIFO repair proposal.",
            profile,
            ProjectKnowledgeBase(),
            context_items=(proposal.context_item,),
        )

        self.assertEqual(invocation.context.items, (proposal.context_item,))
        self.assertEqual(invocation.context.items[0].item_type, "debug.session")

    def test_repair_cli_retains_session_proposal_and_surfer_focus(self) -> None:
        self.trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "waveform",
                    "propose-fifo-repair",
                    "waves.vcd",
                    "--root",
                    str(self.root),
                    "--start-fs",
                    "20000000",
                    "--end-fs",
                    "30000000",
                    "--output-directory",
                    "build/repair",
                )
            )

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        retained = json.loads(
            (self.root / "build/repair/repair-proposal.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(payload, retained)
        self.assertTrue((self.root / "build/repair/debug-session.json").is_file())
        commands = (self.root / "build/repair/focus.sucl").read_text(encoding="utf-8")
        self.assertIn("variable_add sync_fifo.level", commands)
        self.assertIn("zoom_to 24000000fs 26000000fs", commands)

    def test_passing_trace_cannot_be_mislabeled_as_a_repair(self) -> None:
        self.trace.write_text(render_fifo_trace(), encoding="utf-8")
        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            rtl_path=Path("examples/fifo/rtl/sync_fifo.sv"),
        )

        with self.assertRaisesRegex(ValueError, "requires a failing"):
            propose_fifo_repairs(
                report,
                report_uri="build/debug/debug-session.json",
            )

    def test_repair_rejects_missing_findings_and_unverified_source_anchors(self) -> None:
        self.trace.write_text(
            render_fifo_trace(level_update_fault=True),
            encoding="utf-8",
        )
        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
            rtl_path=Path("examples/fifo/rtl/sync_fifo.sv"),
        )
        proposal = propose_fifo_repairs(
            report,
            report_uri="build/debug/debug-session.json",
        )
        with self.assertRaisesRegex(ValueError, "cover every debug finding"):
            build_repair_proposal(
                report,
                report_uri="build/debug/debug-session.json",
                changes=(),
                validation_steps=("Re-run deterministic validation.",),
                confidence_percent=90,
            )

        unverified = replace(
            proposal.changes[0],
            source_anchors=(
                SourceAnchor(
                    "examples/fifo/rtl/unverified.sv",
                    1,
                    1,
                    "sha256:" + "0" * 64,
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "absent from the debug session"):
            build_repair_proposal(
                report,
                report_uri="build/debug/debug-session.json",
                changes=(unverified,),
                validation_steps=("Re-run deterministic validation.",),
                confidence_percent=90,
            )

        duplicate = replace(
            proposal.changes[0],
            change_id="repair.change.duplicate-level",
        )
        with self.assertRaisesRegex(ValueError, "exactly one repair change"):
            build_repair_proposal(
                report,
                report_uri="build/debug/debug-session.json",
                changes=(proposal.changes[0], duplicate),
                validation_steps=("Re-run deterministic validation.",),
                confidence_percent=90,
            )


if __name__ == "__main__":
    unittest.main()
