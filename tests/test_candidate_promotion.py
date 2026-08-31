from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from openrtl.adapters import (
    plan_qualified_provider_candidate_promotion,
    promote_qualified_provider_candidate,
)
from openrtl.application import (
    CandidatePromotionApproval,
    ProviderOutputQualificationReport,
    QualifiedProviderApplicationReport,
    RepairApplicationReport,
)
from openrtl.cli import main


def _digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class CandidatePromotionPlanningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.target = self._bytes("examples/fifo/fault.sv", b"assign level = 0;\n")
        self.candidate = self._bytes(
            "build/repair/candidate/fault.sv", b"assign level = 1;\n"
        )
        common = "sha256:" + "1" * 64
        self.qualification = ProviderOutputQualificationReport(
            "repair.provider-qualification.test",
            "repair.provider-plan.test",
            common,
            "sha256:" + "2" * 64,
            "repair.request.test",
            "sha256:" + "3" * 64,
            "repair.invocation.test",
            "sha256:" + "4" * 64,
            "repair.suggestion.test",
            "sha256:" + "5" * 64,
            "sha256:" + "6" * 64,
            "sha256:" + "7" * 64,
            "repair.proposal.test",
            "debug.session.test",
            self.target.relative_to(self.root).as_posix(),
            _digest(self.target.read_bytes()),
            "repair.edit-plan.test",
            "sha256:" + "8" * 64,
            "repair.planning.test",
            "sha256:" + "9" * 64,
            ("repair.change.level",),
            ("repair.edit.level",),
        )
        self.application = RepairApplicationReport(
            "repair.application.test",
            self.qualification.proposal_id,
            self.qualification.debug_session_id,
            self.qualification.edit_plan_id,
            self.qualification.edit_plan_digest,
            self.qualification.change_ids,
            self.qualification.edit_ids,
            self.target.relative_to(self.root).as_posix(),
            self.candidate.relative_to(self.root).as_posix(),
            _digest(self.target.read_bytes()),
            _digest(self.candidate.read_bytes()),
            (1,),
            "Reviewed exact candidate bytes and renewed evidence.",
        )
        self.qualified = QualifiedProviderApplicationReport(
            "repair.qualified-application.test",
            self.qualification.qualification_id,
            self.qualification.content_digest,
            "sha256:" + "a" * 64,
            self.application,
        )
        self.qualification_path = self._json(
            "build/repair/provider-output-qualification.json",
            self.qualification.payload(),
        )
        self.application_path = self._json(
            "build/repair/application.json", self.application.payload()
        )
        self.qualified_path = self._json(
            "build/repair/qualified-provider-application.json",
            self.qualified.payload(),
        )
        self.before_results = self._bytes(
            "build/repair/before/results.xml", b"<testsuite failures='1'/>\n"
        )
        self.before_waveform = self._bytes(
            "build/repair/before/waves.vcd", b"$timescale 1fs $end\n0!\n"
        )
        self.results = self._bytes(
            "build/repair/repaired/results.xml", b"<testsuite/>\n"
        )
        self.waveform = self._bytes(
            "build/repair/repaired/waves.vcd", b"$timescale 1fs $end\n1!\n"
        )
        self.comparison_payload: dict[str, Any] = {
            "after": {"finding_ids": [], "passed": True, "waveform": self._relative(self.waveform)},
            "application_id": self.application.application_id,
            "before": {
                "finding_ids": ["debug.finding.level"],
                "passed": False,
                "waveform": self._relative(self.before_waveform),
            },
            "proposal_id": self.application.proposal_id,
            "schema": "openrtl.repair-comparison.v2",
            "status": "validated",
            "visual_evidence": {
                "before": {"level_at_focus_end": 0, "level_at_marker": 0, "trace_end_fs": 20_000_000},
                "focus": {"end_fs": 15_001_000, "marker_fs": 10_000_000, "signals": ["sync_fifo.level"], "start_fs": 4_999_000},
                "repaired": {"level_at_focus_end": 1, "level_at_marker": 1, "trace_end_fs": 20_000_000},
                "schema": "openrtl.repair-visual-comparison.v1",
                "status": "visibly_distinct",
            },
        }
        self.comparison_path = self._json(
            "build/repair/comparison.json", self.comparison_payload
        )
        self.evidence_path = self._json(
            "build/repair/evidence.json", self._evidence_payload()
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _bytes(self, relative: str, content: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _json(self, relative: str, payload: dict[str, Any]) -> Path:
        return self._bytes(
            relative, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        )

    def _artifact(self, path: Path) -> dict[str, Any]:
        content = path.read_bytes()
        return {
            "path": self._relative(path),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
        }

    def _evidence_payload(self) -> dict[str, Any]:
        artifacts = {
            "application": self._artifact(self.application_path),
            "before_results": self._artifact(self.before_results),
            "before_waveform": self._artifact(self.before_waveform),
            "comparison": self._artifact(self.comparison_path),
            "provider_output_qualification": self._artifact(self.qualification_path),
            "qualified_provider_application": self._artifact(self.qualified_path),
            "repaired_results": self._artifact(self.results),
            "repaired_source": self._artifact(self.candidate),
            "repaired_waveform": self._artifact(self.waveform),
        }
        return {
            "artifacts": artifacts,
            "authorization_boundary": {
                "candidate_only": True,
                "gui_launched": False,
                "production_rtl_modified": False,
                "real_credential_resolved": False,
                "real_provider_called": False,
                "remote_operations": False,
                "synthetic_provider_adapter_calls": 1,
            },
            "qualified_application_id": self.application.application_id,
            "qualified_edit_plan_digest": self.application.edit_plan_digest,
            "qualified_expert_suggestion_id": "repair.suggestion.test",
            "qualified_provider_application_id": self.qualified.qualified_application_id,
            "qualified_provider_output_id": self.qualification.qualification_id,
            "schema": "openrtl.repair-application-evidence.v9",
            "status": "passed",
            "toolchain": {"cocotb_config": "/tool/cocotb", "make": "/tool/make", "verilator": "/tool/verilator"},
        }

    def _plan(self) -> Any:
        return plan_qualified_provider_candidate_promotion(
            self.root,
            qualification_report_path=self.qualification_path,
            application_report_path=self.application_path,
            qualified_application_report_path=self.qualified_path,
            candidate_path=self.candidate,
            target_path=self.target,
            comparison_path=self.comparison_path,
            evidence_path=self.evidence_path,
        )

    def _approval(self, plan: Any) -> CandidatePromotionApproval:
        return CandidatePromotionApproval(
            plan.promotion_plan_id,
            plan.content_digest,
            plan.target_path,
            plan.target_digest,
            plan.candidate_digest,
            "Independently reviewed the exact plan, source pair, and renewed evidence.",
        )

    def test_exact_lineage_builds_non_applying_review_gate(self) -> None:
        target_before = self.target.read_bytes()
        plan = self._plan()
        payload = plan.payload()
        self.assertEqual(payload["status"], "awaiting_promotion_approval")
        self.assertFalse(payload["applies_changes"])
        self.assertEqual(payload["candidate"]["content_digest"], _digest(self.candidate.read_bytes()))
        self.assertEqual(payload["target"]["content_digest"], _digest(target_before))
        self.assertTrue(payload["next_gate"]["explicit_production_promotion_required"])
        self.assertEqual(
            payload["validation"]["before_waveform"]["path"],
            self._relative(self.before_waveform),
        )
        self.assertEqual(self.target.read_bytes(), target_before)

    def test_stale_candidate_target_and_evidence_fail_closed(self) -> None:
        original_candidate = self.candidate.read_bytes()
        self.candidate.write_bytes(original_candidate + b"// stale\n")
        with self.assertRaisesRegex(ValueError, "reviewed application"):
            self._plan()
        self.candidate.write_bytes(original_candidate)

        original_target = self.target.read_bytes()
        self.target.write_bytes(original_target + b"// stale\n")
        with self.assertRaisesRegex(ValueError, "reviewed application"):
            self._plan()
        self.target.write_bytes(original_target)

        evidence = json.loads(self.evidence_path.read_text())
        evidence["authorization_boundary"]["production_rtl_modified"] = True
        self._json("build/repair/evidence.json", evidence)
        with self.assertRaisesRegex(ValueError, "authorization boundary"):
            self._plan()
        self.assertEqual(self.target.read_bytes(), original_target)

    def test_comparison_waveforms_must_match_evidence(self) -> None:
        comparison = json.loads(self.comparison_path.read_text())
        comparison["after"]["waveform"] = self._relative(self.before_waveform)
        self._json("build/repair/comparison.json", comparison)
        evidence = self._evidence_payload()
        self._json("build/repair/evidence.json", evidence)
        with self.assertRaisesRegex(ValueError, "repaired waveform differs"):
            self._plan()

    def test_cli_writes_only_the_separate_plan(self) -> None:
        output = self.root / "build/repair/promotion-plan.json"
        target_before = self.target.read_bytes()
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(
                main(
                    (
                        "repair",
                        "plan-qualified-provider-candidate-promotion",
                        "--root",
                        str(self.root),
                        "--qualification-report",
                        str(self.qualification_path),
                        "--application-report",
                        str(self.application_path),
                        "--qualified-application-report",
                        str(self.qualified_path),
                        "--candidate",
                        str(self.candidate),
                        "--target-source",
                        str(self.target),
                        "--comparison",
                        str(self.comparison_path),
                        "--evidence",
                        str(self.evidence_path),
                        "--promotion-plan-output",
                        str(output),
                    )
                ),
                0,
            )
        summary = json.loads(captured.getvalue())
        self.assertEqual(summary["status"], "awaiting_promotion_approval")
        self.assertFalse(summary["applies_changes"])
        self.assertEqual(json.loads(output.read_text())["content_digest"], summary["promotion_plan_digest"])
        self.assertEqual(self.target.read_bytes(), target_before)

    def test_exact_independent_approval_promotes_candidate_bytes(self) -> None:
        plan = self._plan()
        plan_path = self._json("build/repair/promotion-plan.json", plan.payload())
        receipt = promote_qualified_provider_candidate(
            self.root,
            promotion_plan_path=plan_path,
            candidate_path=self.candidate,
            target_path=self.target,
            approval=self._approval(plan),
        )
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())
        self.assertEqual(receipt.payload()["status"], "promoted_to_production")
        self.assertTrue(receipt.payload()["applies_changes"])
        self.assertEqual(receipt.target_digest_after, _digest(self.target.read_bytes()))

    def test_stale_or_mismatched_promotion_fails_before_target_write(self) -> None:
        plan = self._plan()
        plan_path = self._json("build/repair/promotion-plan.json", plan.payload())
        target_before = self.target.read_bytes()
        wrong = CandidatePromotionApproval(
            plan.promotion_plan_id,
            "sha256:" + "f" * 64,
            plan.target_path,
            plan.target_digest,
            plan.candidate_digest,
            "Independent signoff with the wrong plan digest.",
        )
        with self.assertRaisesRegex(ValueError, "does not match exact plan"):
            promote_qualified_provider_candidate(
                self.root,
                promotion_plan_path=plan_path,
                candidate_path=self.candidate,
                target_path=self.target,
                approval=wrong,
            )
        self.assertEqual(self.target.read_bytes(), target_before)

        self.candidate.write_bytes(self.candidate.read_bytes() + b"// stale\n")
        with self.assertRaisesRegex(ValueError, "candidate bytes differ"):
            promote_qualified_provider_candidate(
                self.root,
                promotion_plan_path=plan_path,
                candidate_path=self.candidate,
                target_path=self.target,
                approval=self._approval(plan),
            )
        self.assertEqual(self.target.read_bytes(), target_before)

    def test_cli_promotes_and_writes_separate_receipt(self) -> None:
        plan = self._plan()
        plan_path = self._json("build/repair/promotion-plan.json", plan.payload())
        receipt_path = self.root / "build/repair/promotion-receipt.json"
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(
                main(
                    (
                        "repair",
                        "promote-qualified-provider-candidate",
                        "--root",
                        str(self.root),
                        "--promotion-plan",
                        str(plan_path),
                        "--candidate",
                        str(self.candidate),
                        "--target-source",
                        str(self.target),
                        "--promotion-receipt-output",
                        str(receipt_path),
                        "--approve-promotion-plan-id",
                        plan.promotion_plan_id,
                        "--approve-promotion-plan-digest",
                        plan.content_digest,
                        "--approve-target-path",
                        plan.target_path,
                        "--approve-target-digest",
                        plan.target_digest,
                        "--approve-candidate-digest",
                        plan.candidate_digest,
                        "--signoff-note",
                        "Independently reviewed the exact promotion plan and evidence.",
                    )
                ),
                0,
            )
        summary = json.loads(captured.getvalue())
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual(summary["status"], "promoted_to_production")
        self.assertEqual(receipt["status"], "promoted_to_production")
        self.assertEqual(self.target.read_bytes(), self.candidate.read_bytes())


if __name__ == "__main__":
    unittest.main()
