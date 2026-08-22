from __future__ import annotations

import unittest

from openrtl.domain.context import ExpertRole
from openrtl.domain.design import (
    ClockResetContract,
    DesignSpecification,
    InterfacePort,
    Parameter,
    PortDirection,
    Requirement,
)
from openrtl.domain.evidence import RequirementAnchor
from openrtl.domain.learning import (
    ExperienceLevel,
    LearnerProfile,
    LearningSession,
    TeachingStep,
)
from openrtl.domain.profiles import ExpertBinding, ProjectProfile, RuntimeProfile, ToolProfile


class DesignProfilesLearningTest(unittest.TestCase):
    def test_design_requires_consistent_interface_and_clock_reset(self) -> None:
        specification = DesignSpecification(
            design_id="sync.fifo",
            title="Synchronous FIFO",
            summary="Parameterized single-clock FIFO",
            requirements=(
                Requirement("req.fifo.order", "Preserve write order", "Scoreboard matches reads"),
            ),
            ports=(
                InterfacePort("clk", PortDirection.INPUT, 1),
                InterfacePort("rst_n", PortDirection.INPUT, 1),
                InterfacePort("wr_data", PortDirection.INPUT, 32, "clk"),
            ),
            parameters=(Parameter("data_width", 32, 1, 1024),),
            clock_resets=(ClockResetContract("clk", "rst_n", True, False),),
        )
        self.assertEqual(specification.design_id, "sync.fifo")

    def test_project_profile_resolves_explicit_expert_binding(self) -> None:
        profile = ProjectProfile(
            profile_id="project.default",
            runtimes=(RuntimeProfile("runtime.codex", "openai", "gpt-test", "codex.local"),),
            tool_profiles=(ToolProfile("tools.rtl", ("eda.verilator",), "verilator"),),
            experts=(
                ExpertBinding(ExpertRole.RTL_ENGINEER, "runtime.codex", "tools.rtl"),
            ),
        )
        self.assertEqual(profile.expert(ExpertRole.RTL_ENGINEER).max_turns, 8)
        with self.assertRaises(KeyError):
            profile.expert(ExpertRole.DV_ENGINEER)

    def test_learning_session_preserves_ordered_progress(self) -> None:
        session = LearningSession(
            "learn.fifo.1",
            LearnerProfile("learner.local", ExperienceLevel.BEGINNER, ("Understand FIFOs",)),
        )
        step = TeachingStep(
            step_id="step.handshake",
            objective="Understand ready/valid transfer",
            explanation="A transfer occurs only when ready and valid are both asserted.",
            action="Inspect the write interface.",
            checkpoint_question="Which cycle accepts the first word?",
            anchors=(RequirementAnchor("req.fifo.order"),),
        )
        session.add_step(step)
        self.assertEqual(session.next_step(), step)
        session.complete(step.step_id)
        self.assertIsNone(session.next_step())
        self.assertEqual(session.completed_step_ids, (step.step_id,))


if __name__ == "__main__":
    unittest.main()
