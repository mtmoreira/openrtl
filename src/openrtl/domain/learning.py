"""Learning-mode progress and evidence-linked teaching checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain._validation import identifier, nonempty
from openrtl.domain.evidence import EvidenceAnchor


class InteractionMode(str, Enum):
    BUILD = "build"
    LEARN = "learn"


class ExperienceLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


@dataclass(frozen=True)
class LearnerProfile:
    learner_id: str
    level: ExperienceLevel
    goals: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "learner_id", identifier(self.learner_id, "learner_id"))
        goals = tuple(nonempty(value, "goal") for value in self.goals)
        if not goals or len(set(goals)) != len(goals):
            raise ValueError("goals must be non-empty and unique")
        object.__setattr__(self, "goals", goals)


@dataclass(frozen=True)
class TeachingStep:
    step_id: str
    objective: str
    explanation: str
    action: str
    checkpoint_question: str
    anchors: tuple[EvidenceAnchor, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", identifier(self.step_id, "step_id"))
        object.__setattr__(self, "objective", nonempty(self.objective, "objective"))
        object.__setattr__(self, "explanation", nonempty(self.explanation, "explanation"))
        object.__setattr__(self, "action", nonempty(self.action, "action"))
        object.__setattr__(
            self,
            "checkpoint_question",
            nonempty(self.checkpoint_question, "checkpoint_question"),
        )
        anchors = tuple(self.anchors)
        if len(set(anchors)) != len(anchors):
            raise ValueError("anchors must be unique")
        object.__setattr__(self, "anchors", anchors)


class LearningSession:
    def __init__(self, session_id: str, profile: LearnerProfile) -> None:
        self.session_id = identifier(session_id, "session_id")
        self.profile = profile
        self._steps: dict[str, TeachingStep] = {}
        self._completed: list[str] = []

    def add_step(self, step: TeachingStep) -> None:
        if step.step_id in self._steps:
            raise ValueError(f"teaching step already exists: {step.step_id}")
        self._steps[step.step_id] = step

    def complete(self, step_id: str) -> None:
        normalized = identifier(step_id, "step_id")
        if normalized not in self._steps:
            raise KeyError(f"unknown teaching step: {normalized}")
        if normalized in self._completed:
            raise ValueError(f"teaching step already completed: {normalized}")
        self._completed.append(normalized)

    @property
    def completed_step_ids(self) -> tuple[str, ...]:
        return tuple(self._completed)

    def next_step(self) -> TeachingStep | None:
        for step_id, step in self._steps.items():
            if step_id not in self._completed:
                return step
        return None
