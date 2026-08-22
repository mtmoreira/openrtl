from __future__ import annotations

import unittest

from openrtl.domain import (
    ArtifactKind,
    ArtifactRef,
    ArtifactRevision,
    ContextPackBuilder,
    ContextRequest,
    DecisionRecord,
    DecisionStatus,
    EvidenceRecord,
    ExpertRole,
    LogAnchor,
    ProjectKnowledgeBase,
    RequirementAnchor,
    RunBundle,
    RunStatus,
)


DIGEST = "sha256:" + "c" * 64


class ContextPackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.knowledge = ProjectKnowledgeBase()
        self.requirements = ArtifactRevision(
            ref=ArtifactRef("requirements", 1),
            kind=ArtifactKind.REQUIREMENTS,
            uri="design/requirements.md",
            content_digest=DIGEST,
            summary="FIFO ordering requirements",
            requirement_ids=("req.fifo.order",),
        )
        self.knowledge.artifacts.add(self.requirements)
        self.knowledge.evidence.add(
            EvidenceRecord(
                evidence_id="ev.order.failure",
                summary="Read data diverges from the model",
                anchors=(
                    RequirementAnchor("req.fifo.order"),
                    LogAnchor("run.fifo.1", "scoreboard.mismatch.1"),
                ),
                artifact_refs=(self.requirements.ref,),
            )
        )
        self.knowledge.add_decision(
            DecisionRecord(
                decision_id="dec.pointer.scheme",
                title="Use extended binary pointers",
                rationale="The extra bit disambiguates full from empty.",
                status=DecisionStatus.ACCEPTED,
                owner_role="rtl_engineer",
                artifact_refs=(self.requirements.ref,),
                requirement_ids=("req.fifo.order",),
            )
        )
        self.knowledge.add_run(
            RunBundle(
                run_id="run.fifo.1",
                status=RunStatus.FAILED,
                tool_profile_id="verilator.default",
                seed=7,
                artifact_refs=(self.requirements.ref,),
                evidence_ids=("ev.order.failure",),
                log_uri="runs/run.fifo.1/events.jsonl",
                trace_uri="runs/run.fifo.1/waves.vcd",
                failure_signature="scoreboard.order.mismatch",
            )
        )

    def test_diagnosis_pack_is_deterministic_and_evidence_scoped(self) -> None:
        request = ContextRequest(
            role=ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
            objective="Root-cause the FIFO ordering mismatch",
            artifact_kinds=(ArtifactKind.REQUIREMENTS,),
            requirement_ids=("req.fifo.order",),
            evidence_ids=("ev.order.failure",),
            decision_ids=("dec.pointer.scheme",),
            run_id="run.fifo.1",
        )
        builder = ContextPackBuilder(self.knowledge)
        first = builder.build(request)
        second = builder.build(request)

        self.assertEqual(first, second)
        self.assertEqual(first.pack_id, "ctx-297228b371e6ac115b81")
        self.assertEqual(
            tuple(item.item_type for item in first.items),
            ("decision", "evidence", "artifact.requirements", "run"),
        )

    def test_empty_selection_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "selected no project knowledge"):
            ContextPackBuilder(self.knowledge).build(
                ContextRequest(
                    role=ExpertRole.RTL_ENGINEER,
                    objective="Implement unrelated logic",
                    artifact_kinds=(ArtifactKind.DV,),
                )
            )


if __name__ == "__main__":
    unittest.main()
