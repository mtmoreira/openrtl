"""Stable expert contracts and explicit model/tool invocation plans."""

from __future__ import annotations

from dataclasses import dataclass

from openrtl.domain import (
    ArtifactKind,
    ContextItem,
    ContextPack,
    ContextPackBuilder,
    ContextRequest,
    ExpertRole,
    ProjectKnowledgeBase,
    ProjectProfile,
)


@dataclass(frozen=True)
class ExpertDefinition:
    role: ExpertRole
    purpose: str
    reads: tuple[ArtifactKind, ...]
    produces: tuple[ArtifactKind, ...]
    required_tools: tuple[str, ...] = ()


EXPERT_DEFINITIONS = (
    ExpertDefinition(
        ExpertRole.DESIGN_LEAD,
        "Own scope, stage gates, requirement traceability, and escalation.",
        tuple(ArtifactKind),
        (ArtifactKind.IMPLEMENTATION_PLAN, ArtifactKind.REVIEW),
    ),
    ExpertDefinition(
        ExpertRole.LEARNING_COACH,
        "Turn engineering progress into evidence-linked teaching steps.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.RTL, ArtifactKind.DV, ArtifactKind.RUN),
        (ArtifactKind.REVIEW,),
    ),
    ExpertDefinition(
        ExpertRole.DESIGN_ARCHITECT,
        "Convert requirements into interfaces, behavior, and microarchitecture.",
        (ArtifactKind.REQUIREMENTS, ArtifactKind.DESIGN_PACKAGE),
        (ArtifactKind.SPECIFICATION, ArtifactKind.MICROARCHITECTURE, ArtifactKind.INTEGRATION_PLAN),
    ),
    ExpertDefinition(
        ExpertRole.REUSE_INTEGRATION_ARCHITECT,
        "Find compatible local packages and define hierarchical integration.",
        (ArtifactKind.REQUIREMENTS, ArtifactKind.DESIGN_PACKAGE),
        (ArtifactKind.INTEGRATION_PLAN,),
        ("catalog.search",),
    ),
    ExpertDefinition(
        ExpertRole.REFERENCE_MODEL_ENGINEER,
        "Create an executable bit-accurate behavioral oracle and tests.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.TEST_PLAN),
        (ArtifactKind.REFERENCE_MODEL,),
    ),
    ExpertDefinition(
        ExpertRole.VERIFICATION_ARCHITECT,
        "Define coverage, checking, test, and trace-comparison strategy.",
        (ArtifactKind.REQUIREMENTS, ArtifactKind.SPECIFICATION),
        (ArtifactKind.VERIFICATION_PLAN, ArtifactKind.TEST_PLAN),
    ),
    ExpertDefinition(
        ExpertRole.RTL_ENGINEER,
        "Implement readable synthesizable SystemVerilog from approved design artifacts.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.MICROARCHITECTURE, ArtifactKind.IMPLEMENTATION_PLAN),
        (ArtifactKind.RTL,),
        ("eda.lint",),
    ),
    ExpertDefinition(
        ExpertRole.ASSERTION_ENGINEER,
        "Encode simulation assertions linked to requirements.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.RTL, ArtifactKind.VERIFICATION_PLAN),
        (ArtifactKind.ASSERTIONS,),
    ),
    ExpertDefinition(
        ExpertRole.DV_ENGINEER,
        "Implement cocotb stimulus, scoreboards, coverage, and standardized logs.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.REFERENCE_MODEL, ArtifactKind.TEST_PLAN),
        (ArtifactKind.DV,),
        ("eda.simulate",),
    ),
    ExpertDefinition(
        ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
        "Correlate requirements, RTL, logs, waveforms, and model traces to root cause.",
        (ArtifactKind.SPECIFICATION, ArtifactKind.RTL, ArtifactKind.DV, ArtifactKind.RUN),
        (ArtifactKind.DIAGNOSIS,),
        ("eda.simulate", "waveform.inspect"),
    ),
    ExpertDefinition(
        ExpertRole.SIGNOFF_REVIEWER,
        "Independently verify requirement coverage and evidence sufficiency.",
        tuple(ArtifactKind),
        (ArtifactKind.REVIEW, ArtifactKind.DESIGN_PACKAGE),
    ),
)


@dataclass(frozen=True)
class ExpertInvocationPlan:
    role: ExpertRole
    objective: str
    runtime_binding_id: str
    provider: str
    model: str
    tool_ids: tuple[str, ...]
    max_turns: int
    timeout_seconds: int
    context: ContextPack


class ExpertRegistry:
    def __init__(self, definitions: tuple[ExpertDefinition, ...] = EXPERT_DEFINITIONS) -> None:
        self._definitions = {value.role: value for value in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("expert roles must be unique")

    def definition(self, role: ExpertRole) -> ExpertDefinition:
        try:
            return self._definitions[role]
        except KeyError as error:
            raise KeyError(f"unknown expert role: {role.value}") from error

    def plan(
        self,
        role: ExpertRole,
        objective: str,
        profile: ProjectProfile,
        knowledge: ProjectKnowledgeBase,
        *,
        requirement_ids: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
        run_id: str | None = None,
        attempt: int = 1,
        context_items: tuple[ContextItem, ...] = (),
    ) -> ExpertInvocationPlan:
        definition = self.definition(role)
        binding = profile.expert(role)
        runtime = next(
            value for value in profile.runtimes if value.profile_id == binding.runtime_profile_id
        )
        tools = next(
            value for value in profile.tool_profiles if value.profile_id == binding.tool_profile_id
        )
        missing = set(definition.required_tools) - set(tools.tool_ids)
        if missing:
            raise ValueError(f"expert tool profile is missing: {','.join(sorted(missing))}")
        context = ContextPackBuilder(knowledge).build(
            ContextRequest(
                role=role,
                objective=objective,
                artifact_kinds=definition.reads,
                requirement_ids=requirement_ids,
                evidence_ids=evidence_ids,
                run_id=run_id,
                attempt=attempt,
                attached_items=context_items,
            )
        )
        return ExpertInvocationPlan(
            role,
            objective,
            runtime.binding_id,
            runtime.provider,
            runtime.model,
            tools.tool_ids,
            binding.max_turns,
            binding.timeout_seconds,
            context,
        )
