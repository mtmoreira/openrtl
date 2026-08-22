"""Deterministic build and learn stage planning over expert contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain import ArtifactRef, ExpertRole, InteractionMode


class WorkflowStage(str, Enum):
    DISCOVERY = "discovery"
    SPECIFICATION = "specification"
    REFERENCE_MODEL = "reference_model"
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    SIMULATION = "simulation"
    DIAGNOSIS = "diagnosis"
    SIGNOFF = "signoff"
    PACKAGE = "package"


_STAGE_ROLES = {
    WorkflowStage.DISCOVERY: (
        ExpertRole.DESIGN_LEAD,
        ExpertRole.REUSE_INTEGRATION_ARCHITECT,
    ),
    WorkflowStage.SPECIFICATION: (
        ExpertRole.DESIGN_ARCHITECT,
        ExpertRole.VERIFICATION_ARCHITECT,
    ),
    WorkflowStage.REFERENCE_MODEL: (ExpertRole.REFERENCE_MODEL_ENGINEER,),
    WorkflowStage.IMPLEMENTATION: (
        ExpertRole.RTL_ENGINEER,
        ExpertRole.ASSERTION_ENGINEER,
    ),
    WorkflowStage.VERIFICATION: (ExpertRole.DV_ENGINEER,),
    WorkflowStage.SIMULATION: (ExpertRole.DV_ENGINEER,),
    WorkflowStage.DIAGNOSIS: (ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,),
    WorkflowStage.SIGNOFF: (ExpertRole.SIGNOFF_REVIEWER,),
    WorkflowStage.PACKAGE: (
        ExpertRole.REUSE_INTEGRATION_ARCHITECT,
        ExpertRole.SIGNOFF_REVIEWER,
    ),
}


@dataclass(frozen=True)
class StageOutcome:
    stage: WorkflowStage
    passed: bool
    artifact_refs: tuple[ArtifactRef, ...]
    evidence_ids: tuple[str, ...] = ()
    failure_signature: str | None = None

    def __post_init__(self) -> None:
        if self.passed and self.failure_signature is not None:
            raise ValueError("passing stages cannot have a failure signature")
        if not self.passed and self.failure_signature is None:
            raise ValueError("failed stages require a failure signature")


@dataclass(frozen=True)
class WorkflowState:
    mode: InteractionMode
    stages: tuple[WorkflowStage, ...]
    current_index: int = 0
    outcomes: tuple[StageOutcome, ...] = ()

    @property
    def current_stage(self) -> WorkflowStage | None:
        return self.stages[self.current_index] if self.current_index < len(self.stages) else None

    @property
    def complete(self) -> bool:
        return self.current_index >= len(self.stages)


class OpenRTLWorkflow:
    def create(self, mode: InteractionMode) -> WorkflowState:
        return WorkflowState(mode, tuple(WorkflowStage))

    def roles(self, state: WorkflowState) -> tuple[ExpertRole, ...]:
        stage = state.current_stage
        if stage is None:
            return ()
        roles = _STAGE_ROLES[stage]
        if state.mode is InteractionMode.LEARN and ExpertRole.LEARNING_COACH not in roles:
            return (*roles, ExpertRole.LEARNING_COACH)
        return roles

    def advance(self, state: WorkflowState, outcome: StageOutcome) -> WorkflowState:
        if state.complete or outcome.stage is not state.current_stage:
            raise ValueError("stage outcome does not match workflow state")
        outcomes = (*state.outcomes, outcome)
        if outcome.passed:
            return WorkflowState(state.mode, state.stages, state.current_index + 1, outcomes)
        diagnosis_index = state.stages.index(WorkflowStage.DIAGNOSIS)
        if state.current_index >= diagnosis_index:
            return WorkflowState(state.mode, state.stages, state.current_index, outcomes)
        return WorkflowState(state.mode, state.stages, diagnosis_index, outcomes)
