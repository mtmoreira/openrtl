from __future__ import annotations

import unittest

from openrtl.application import (
    EXPERT_DEFINITIONS,
    ExpertRegistry,
    OpenRTLWorkflow,
    StageOutcome,
    WorkflowStage,
)
from openrtl.domain import (
    ArtifactKind,
    ArtifactRef,
    ArtifactRevision,
    ExpertBinding,
    ExpertRole,
    InteractionMode,
    ProjectKnowledgeBase,
    ProjectProfile,
    RuntimeProfile,
    ToolProfile,
)


class ExpertWorkflowTest(unittest.TestCase):
    def test_every_role_has_one_stable_definition(self) -> None:
        self.assertEqual(
            {value.role for value in EXPERT_DEFINITIONS},
            set(ExpertRole),
        )

    def test_learn_mode_adds_coach_and_failed_build_routes_to_diagnosis(self) -> None:
        workflow = OpenRTLWorkflow()
        state = workflow.create(InteractionMode.LEARN)
        self.assertEqual(state.current_stage, WorkflowStage.DISCOVERY)
        self.assertIn(ExpertRole.LEARNING_COACH, workflow.roles(state))

        failed = StageOutcome(
            WorkflowStage.DISCOVERY,
            False,
            (ArtifactRef("requirements", 1),),
            failure_signature="reuse.search.failed",
        )
        state = workflow.advance(state, failed)
        self.assertEqual(state.current_stage, WorkflowStage.DIAGNOSIS)
        self.assertEqual(
            workflow.roles(state),
            (ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER, ExpertRole.LEARNING_COACH),
        )

    def test_passing_stages_advance_in_order(self) -> None:
        workflow = OpenRTLWorkflow()
        state = workflow.create(InteractionMode.BUILD)
        for stage in tuple(WorkflowStage):
            self.assertIs(state.current_stage, stage)
            state = workflow.advance(
                state,
                StageOutcome(stage, True, (ArtifactRef("artifact", 1),)),
            )
        self.assertTrue(state.complete)
        self.assertEqual(workflow.roles(state), ())

    def test_diagnosis_plan_receives_validated_shared_artifacts_and_selected_model(self) -> None:
        knowledge = ProjectKnowledgeBase()
        for index, kind in enumerate(
            (ArtifactKind.SPECIFICATION, ArtifactKind.RTL, ArtifactKind.DV, ArtifactKind.RUN),
            start=1,
        ):
            knowledge.artifacts.add(
                ArtifactRevision(
                    ArtifactRef(f"fifo.{kind.value}", 1),
                    kind,
                    f"artifacts/{kind.value}.md",
                    "sha256:" + str(index) * 64,
                    f"FIFO {kind.value}",
                )
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

        plan = ExpertRegistry().plan(
            ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
            "Root-cause the FIFO mismatch.",
            profile,
            knowledge,
        )

        self.assertEqual(plan.model, "gpt-selected")
        self.assertEqual(plan.runtime_binding_id, "codex.local")
        self.assertEqual(len(plan.context.items), 4)
        self.assertEqual(
            {item.item_type for item in plan.context.items},
            {"artifact.specification", "artifact.rtl", "artifact.dv", "artifact.run"},
        )


if __name__ == "__main__":
    unittest.main()
