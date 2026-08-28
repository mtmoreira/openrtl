from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    analyze_fifo_waveform,
    apply_reviewed_fifo_level_repair,
    propose_fifo_repairs,
)
from openrtl.application import RepairApproval
from openrtl.cli import main


class ReviewedRepairApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        fixture = (
            Path(__file__).resolve().parents[1]
            / "examples/fifo/faults/sync_fifo_level_fault.sv"
        ).read_text(encoding="utf-8")
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault.sv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(fixture, encoding="utf-8")
        self.trace = self.root / "build/fault/waves.vcd"
        self.trace.parent.mkdir(parents=True)
        self.trace.write_text(render_fifo_trace(level_update_fault=True), encoding="utf-8")
        report = analyze_fifo_waveform(
            self.root,
            self.trace,
            start_fs=20_000_000,
            end_fs=30_000_000,
            rtl_path=self.source.relative_to(self.root),
        )
        proposal = propose_fifo_repairs(
            report,
            report_uri="build/fault/debug-session.json",
        )
        self.debug_path = self.root / "build/fault/debug-session.json"
        self.proposal_path = self.root / "build/fault/repair-proposal.json"
        self.debug_path.write_text(
            json.dumps(report.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.proposal_path.write_text(
            json.dumps(proposal.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.proposal_id = proposal.proposal_id
        self.approval = RepairApproval(
            self.proposal_id,
            ("repair.change.level",),
            "Reviewed the linked edge and exact sequential source anchor.",
        )

    def test_exact_approval_writes_separate_candidate_and_application_report(self) -> None:
        output = self.root / "build/application/repaired-sync-fifo.sv"

        report = apply_reviewed_fifo_level_repair(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            output_path=output,
            approval=self.approval,
        )

        self.assertEqual(report.change_ids, ("repair.change.level",))
        self.assertEqual(report.changed_line_numbers, (73,))
        self.assertEqual(report.payload()["status"], "applied_to_candidate")
        self.assertIn("2'b10: count <= count + 1'b1;", output.read_text(encoding="utf-8"))
        self.assertIn("2'b10: count <= count;", self.source.read_text(encoding="utf-8"))

    def test_application_is_idempotent_only_for_the_exact_existing_candidate(self) -> None:
        output = self.root / "build/application/repaired-sync-fifo.sv"
        first = apply_reviewed_fifo_level_repair(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            output_path=output,
            approval=self.approval,
        )
        second = apply_reviewed_fifo_level_repair(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            output_path=output,
            approval=self.approval,
        )
        self.assertEqual(first, second)
        output.write_text("unowned\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized content"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                output_path=output,
                approval=self.approval,
            )

    def test_wrong_proposal_or_change_approval_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "proposal identity"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                output_path=Path("build/application/repaired.sv"),
                approval=RepairApproval(
                    "repair.wrong",
                    ("repair.change.level",),
                    "Reviewed a different proposal.",
                ),
            )
        with self.assertRaisesRegex(ValueError, "exact supported change"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                output_path=Path("build/application/repaired.sv"),
                approval=RepairApproval(
                    self.proposal_id,
                    ("repair.change.other",),
                    "Reviewed an unsupported change.",
                ),
            )

    def test_stale_source_and_tampered_debug_session_fail_closed(self) -> None:
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "// changed after review\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "source anchor no longer matches"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

        self.setUp_source_again()
        debug = json.loads(self.debug_path.read_text(encoding="utf-8"))
        debug["passed"] = True
        self.debug_path.write_text(json.dumps(debug), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "failing debug session"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def test_symlinked_input_fails_closed(self) -> None:
        linked = self.root / "build/fault/linked-source.sv"
        linked.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
            apply_reviewed_fifo_level_repair(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=linked,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def setUp_source_again(self) -> None:
        fixture = (
            Path(__file__).resolve().parents[1]
            / "examples/fifo/faults/sync_fifo_level_fault.sv"
        ).read_text(encoding="utf-8")
        self.source.write_text(fixture, encoding="utf-8")

    def test_cli_requires_explicit_approval_and_retains_report(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "repair",
                    "apply-fifo-level",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--output",
                    "build/application/repaired.sv",
                    "--application-report",
                    "build/application/application.json",
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--review-note",
                    "Reviewed exact source and waveform anchors.",
                )
            )

        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        retained = json.loads(
            (self.root / "build/application/application.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload, retained)
        self.assertEqual(payload["status"], "applied_to_candidate")

    def test_cli_report_cannot_overwrite_repair_input_or_output(self) -> None:
        original_source = self.source.read_text(encoding="utf-8")
        candidate = self.root / "build/application/repaired.sv"
        with self.assertRaisesRegex(ValueError, "report must be separate"):
            main(
                (
                    "repair",
                    "apply-fifo-level",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--output",
                    "build/application/repaired.sv",
                    "--application-report",
                    str(self.source),
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--review-note",
                    "Reviewed exact source and waveform anchors.",
                )
            )
        self.assertEqual(self.source.read_text(encoding="utf-8"), original_source)
        self.assertFalse(candidate.exists())

        with self.assertRaisesRegex(ValueError, "report must be separate"):
            main(
                (
                    "repair",
                    "apply-fifo-level",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--output",
                    str(candidate),
                    "--application-report",
                    str(candidate),
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--review-note",
                    "Reviewed exact source and waveform anchors.",
                )
            )
        self.assertFalse(candidate.exists())


if __name__ == "__main__":
    unittest.main()
