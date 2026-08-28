from __future__ import annotations

import io
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from examples.fifo.faults import render_fifo_trace
from openrtl.adapters import (
    analyze_fifo_waveform,
    apply_reviewed_source_edits,
    draft_source_edit_plan,
    propose_fifo_repairs,
)
from openrtl.application import RepairApproval, build_source_edit_plan
from openrtl.cli import main


class ReviewedRepairApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        spec = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "examples/fifo/faults/level_update_edit_spec.json"
            ).read_text(encoding="utf-8")
        )
        self.edit_spec = spec["edits"][0]
        fixture = (
            Path(__file__).resolve().parents[1]
            / "examples/fifo/faults/sync_fifo_level_fault.sv"
        ).read_text(encoding="utf-8")
        self.fixture_source = fixture.replace(
            self.edit_spec["expected_before"],
            self.edit_spec["expected_before"] + " // unit-test-anchor",
        )
        self.source = self.root / "examples/fifo/faults/sync_fifo_level_fault.sv"
        self.source.parent.mkdir(parents=True)
        self.source.write_text(self.fixture_source, encoding="utf-8")
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
        self.edit_spec_path = self.root / "build/fault/edit-spec.json"
        self.edit_spec_path.write_text(
            json.dumps(spec, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.edit_plan, self.planning_report = draft_source_edit_plan(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            edit_spec_path=self.edit_spec_path,
        )
        self.edit_plan_path = self.root / "build/fault/edit-plan.json"
        self.edit_plan_path.write_text(
            json.dumps(self.edit_plan.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.proposal_id = proposal.proposal_id
        self.approval = RepairApproval(
            self.proposal_id,
            ("repair.change.level",),
            self.edit_plan.content_digest,
            "Reviewed the linked edge and exact sequential source anchor.",
        )

    def test_draft_plan_is_evidence_bound_and_requires_separate_review(self) -> None:
        payload = self.planning_report.payload()

        self.assertEqual(payload["status"], "awaiting_review")
        self.assertFalse(payload["applies_changes"])
        self.assertTrue(payload["review"]["approval_required"])
        self.assertEqual(
            payload["edit_plan"]["content_digest"],
            self.edit_plan.content_digest,
        )
        self.assertEqual(payload["proposal"]["proposal_id"], self.proposal_id)
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_draft_rejects_absent_change_and_unanchored_edit(self) -> None:
        spec = json.loads(self.edit_spec_path.read_text(encoding="utf-8"))
        spec["edits"][0]["change_id"] = "repair.change.absent"
        self.edit_spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "absent proposal change"):
            draft_source_edit_plan(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                edit_spec_path=self.edit_spec_path,
            )

        spec["edits"][0].update(
            {
                "change_id": "repair.change.level",
                "edit_id": "repair.edit.unanchored-module-name",
                "expected_before": "module sync_fifo",
                "replacement": "module changed_fifo",
            }
        )
        self.edit_spec_path.write_text(json.dumps(spec), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "outside its reviewed source anchors"):
            draft_source_edit_plan(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                source_path=self.source,
                edit_spec_path=self.edit_spec_path,
            )

    def test_planning_report_binds_exact_edit_spec_file_bytes(self) -> None:
        original_plan_digest = self.edit_plan.content_digest
        original_spec_digest = self.planning_report.edit_spec_digest
        spec = json.loads(self.edit_spec_path.read_text(encoding="utf-8"))
        self.edit_spec_path.write_text(json.dumps(spec) + "\n", encoding="utf-8")

        plan, planning = draft_source_edit_plan(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            source_path=self.source,
            edit_spec_path=self.edit_spec_path,
        )

        self.assertEqual(plan.content_digest, original_plan_digest)
        self.assertNotEqual(planning.edit_spec_digest, original_spec_digest)
        self.assertNotEqual(planning.planning_id, self.planning_report.planning_id)

    def test_exact_approval_writes_separate_candidate_and_application_report(self) -> None:
        output = self.root / "build/application/repaired-sync-fifo.sv"

        report = apply_reviewed_source_edits(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            edit_plan_path=self.edit_plan_path,
            output_path=output,
            approval=self.approval,
        )

        self.assertEqual(report.change_ids, ("repair.change.level",))
        self.assertEqual(report.edit_ids, ("repair.edit.level.accepted-write",))
        self.assertEqual(report.edit_plan_digest, self.edit_plan.content_digest)
        self.assertEqual(report.changed_line_numbers, (73,))
        self.assertEqual(report.payload()["status"], "applied_to_candidate")
        self.assertIn(self.edit_spec["replacement"], output.read_text(encoding="utf-8"))
        self.assertIn(
            self.edit_spec["expected_before"],
            self.source.read_text(encoding="utf-8"),
        )

    def test_approval_binds_every_edit_plan_byte(self) -> None:
        payload = json.loads(self.edit_plan_path.read_text(encoding="utf-8"))
        payload["edits"][0]["replacement"] += " "
        payload["edits"][0]["replacement_digest"] = "sha256:" + hashlib.sha256(
            payload["edits"][0]["replacement"].encode()
        ).hexdigest()
        self.edit_plan_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "plan digest"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def test_unknown_edit_operation_fails_closed(self) -> None:
        payload = json.loads(self.edit_plan_path.read_text(encoding="utf-8"))
        payload["edits"][0]["operation"] = "execute_script"
        self.edit_plan_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "operation is not allowlisted"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def test_edit_outside_reviewed_source_anchors_fails_closed(self) -> None:
        plan = build_source_edit_plan(
            proposal_id=self.proposal_id,
            debug_session_id=self.edit_plan.debug_session_id,
            source_path=self.source.relative_to(self.root).as_posix(),
            source=self.source.read_bytes(),
            edit_specs=(
                {
                    "change_id": "repair.change.level",
                    "edit_id": "repair.edit.unanchored-module-name",
                    "expected_before": "module sync_fifo",
                    "operation": "replace_exact_bytes",
                    "replacement": "module changed_fifo",
                },
            ),
        )
        self.edit_plan_path.write_text(
            json.dumps(plan.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "outside its reviewed source anchors"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=RepairApproval(
                    self.proposal_id,
                    ("repair.change.level",),
                    plan.content_digest,
                    "Reviewed a deliberately unanchored test edit.",
                ),
            )

    def test_multiple_edits_on_one_reviewed_line_report_one_changed_line(self) -> None:
        plan = build_source_edit_plan(
            proposal_id=self.proposal_id,
            debug_session_id=self.edit_plan.debug_session_id,
            source_path=self.source.relative_to(self.root).as_posix(),
            source=self.source.read_bytes(),
            edit_specs=(
                {
                    "change_id": "repair.change.level",
                    "edit_id": "repair.edit.level.expression",
                    "expected_before": self.edit_spec["expected_before"],
                    "operation": "replace_exact_bytes",
                    "replacement": self.edit_spec["replacement"],
                },
                {
                    "change_id": "repair.change.level",
                    "edit_id": "repair.edit.level.test-comment",
                    "expected_before": "unit-test-anchor",
                    "operation": "replace_exact_bytes",
                    "replacement": "unit-test-anchor-reviewed",
                },
            ),
        )
        self.edit_plan_path.write_text(
            json.dumps(plan.payload(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        report = apply_reviewed_source_edits(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            edit_plan_path=self.edit_plan_path,
            output_path=Path("build/application/repaired.sv"),
            approval=RepairApproval(
                self.proposal_id,
                ("repair.change.level",),
                plan.content_digest,
                "Reviewed two non-overlapping edits on the same anchored line.",
            ),
        )
        self.assertEqual(report.changed_line_numbers, (73,))
        self.assertEqual(len(report.edit_ids), 2)

    def test_application_is_idempotent_only_for_the_exact_existing_candidate(self) -> None:
        output = self.root / "build/application/repaired-sync-fifo.sv"
        first = apply_reviewed_source_edits(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            edit_plan_path=self.edit_plan_path,
            output_path=output,
            approval=self.approval,
        )
        second = apply_reviewed_source_edits(
            self.root,
            proposal_path=self.proposal_path,
            debug_session_path=self.debug_path,
            edit_plan_path=self.edit_plan_path,
            output_path=output,
            approval=self.approval,
        )
        self.assertEqual(first, second)
        output.write_text("unowned\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized content"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=output,
                approval=self.approval,
            )

    def test_wrong_proposal_or_change_approval_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "proposal identity"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=RepairApproval(
                    "repair.wrong",
                    ("repair.change.level",),
                    self.edit_plan.content_digest,
                    "Reviewed a different proposal.",
                ),
            )
        with self.assertRaisesRegex(ValueError, "exact approval"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=RepairApproval(
                    self.proposal_id,
                    ("repair.change.other",),
                    self.edit_plan.content_digest,
                    "Reviewed an unsupported change.",
                ),
            )

    def test_stale_source_and_tampered_debug_session_fail_closed(self) -> None:
        self.source.write_text(
            self.source.read_text(encoding="utf-8") + "// changed after review\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "source anchor no longer matches"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

        self.setUp_source_again()
        debug = json.loads(self.debug_path.read_text(encoding="utf-8"))
        debug["passed"] = True
        self.debug_path.write_text(json.dumps(debug), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "failing debug session"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=self.edit_plan_path,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def test_symlinked_input_fails_closed(self) -> None:
        linked = self.root / "build/fault/linked-edit-plan.json"
        linked.symlink_to(self.edit_plan_path)
        with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
            apply_reviewed_source_edits(
                self.root,
                proposal_path=self.proposal_path,
                debug_session_path=self.debug_path,
                edit_plan_path=linked,
                output_path=Path("build/application/repaired.sv"),
                approval=self.approval,
            )

    def setUp_source_again(self) -> None:
        self.source.write_text(self.fixture_source, encoding="utf-8")

    def test_cli_requires_explicit_approval_and_retains_report(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "repair",
                    "apply-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--edit-plan",
                    str(self.edit_plan_path),
                    "--output",
                    "build/application/repaired.sv",
                    "--application-report",
                    "build/application/application.json",
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--approve-edit-plan-digest",
                    self.edit_plan.content_digest,
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

    def test_cli_drafts_plan_without_applying_it(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(
                (
                    "repair",
                    "draft-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--edit-spec",
                    str(self.edit_spec_path),
                    "--edit-plan-output",
                    "build/planning/edit-plan.json",
                    "--planning-report",
                    "build/planning/report.json",
                )
            )

        self.assertEqual(result, 0)
        summary = json.loads(output.getvalue())
        report = json.loads(
            (self.root / "build/planning/report.json").read_text(encoding="utf-8")
        )
        plan = json.loads(
            (self.root / "build/planning/edit-plan.json").read_text(encoding="utf-8")
        )
        self.assertEqual(summary["status"], "awaiting_review")
        self.assertEqual(report["status"], "awaiting_review")
        self.assertEqual(
            report["edit_plan"]["content_digest"],
            self.edit_plan.content_digest,
        )
        self.assertEqual(plan, self.edit_plan.payload())
        self.assertFalse((self.root / "build/application/repaired.sv").exists())

    def test_cli_planning_outputs_cannot_overwrite_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "outputs must be separate"):
            main(
                (
                    "repair",
                    "draft-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--edit-spec",
                    str(self.edit_spec_path),
                    "--edit-plan-output",
                    str(self.edit_spec_path),
                    "--planning-report",
                    "build/planning/report.json",
                )
            )

    def test_cli_planning_is_idempotent_only_for_exact_outputs(self) -> None:
        arguments = (
            "repair",
            "draft-source-edits",
            "--root",
            str(self.root),
            "--proposal",
            str(self.proposal_path),
            "--debug-session",
            str(self.debug_path),
            "--source",
            str(self.source),
            "--edit-spec",
            str(self.edit_spec_path),
            "--edit-plan-output",
            "build/planning/edit-plan.json",
            "--planning-report",
            "build/planning/report.json",
        )
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(arguments), 0)
            self.assertEqual(main(arguments), 0)
        report = self.root / "build/planning/report.json"
        report.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unrecognized content"):
            main(arguments)

    def test_cli_planning_rejects_symlinked_output(self) -> None:
        target = self.root / "build/planning-target.json"
        target.write_text("protected\n", encoding="utf-8")
        link = self.root / "build/planning-link.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
            main(
                (
                    "repair",
                    "draft-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--source",
                    str(self.source),
                    "--edit-spec",
                    str(self.edit_spec_path),
                    "--edit-plan-output",
                    str(link),
                    "--planning-report",
                    "build/planning-report.json",
                )
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "protected\n")

    def test_cli_report_cannot_overwrite_repair_input_or_output(self) -> None:
        original_source = self.source.read_text(encoding="utf-8")
        candidate = self.root / "build/application/repaired.sv"
        with self.assertRaisesRegex(ValueError, "report must be separate"):
            main(
                (
                    "repair",
                    "apply-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--edit-plan",
                    str(self.edit_plan_path),
                    "--output",
                    "build/application/repaired.sv",
                    "--application-report",
                    str(self.source),
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--approve-edit-plan-digest",
                    self.edit_plan.content_digest,
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
                    "apply-source-edits",
                    "--root",
                    str(self.root),
                    "--proposal",
                    str(self.proposal_path),
                    "--debug-session",
                    str(self.debug_path),
                    "--edit-plan",
                    str(self.edit_plan_path),
                    "--output",
                    str(candidate),
                    "--application-report",
                    str(candidate),
                    "--approve-proposal",
                    self.proposal_id,
                    "--approve-change",
                    "repair.change.level",
                    "--approve-edit-plan-digest",
                    self.edit_plan.content_digest,
                    "--review-note",
                    "Reviewed exact source and waveform anchors.",
                )
            )
        self.assertFalse(candidate.exists())


if __name__ == "__main__":
    unittest.main()
