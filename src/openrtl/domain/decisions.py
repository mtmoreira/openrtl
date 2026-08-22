"""Reviewable project decisions and assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain._validation import identifier, nonempty
from openrtl.domain.artifacts import ArtifactRef


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    title: str
    rationale: str
    status: DecisionStatus
    owner_role: str
    artifact_refs: tuple[ArtifactRef, ...] = ()
    requirement_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", identifier(self.decision_id, "decision_id"))
        object.__setattr__(self, "title", nonempty(self.title, "title"))
        object.__setattr__(self, "rationale", nonempty(self.rationale, "rationale"))
        object.__setattr__(self, "owner_role", identifier(self.owner_role, "owner_role"))
        refs = tuple(self.artifact_refs)
        requirements = tuple(identifier(value, "requirement_id") for value in self.requirement_ids)
        if len(set(refs)) != len(refs):
            raise ValueError("artifact_refs must be unique")
        if len(set(requirements)) != len(requirements):
            raise ValueError("requirement_ids must be unique")
        object.__setattr__(self, "artifact_refs", refs)
        object.__setattr__(self, "requirement_ids", requirements)
