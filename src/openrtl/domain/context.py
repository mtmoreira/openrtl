"""Project knowledge and deterministic role-specific context packs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json

from openrtl.domain._validation import digest, identifier, nonempty, relative_path
from openrtl.domain.artifacts import ArtifactGraph, ArtifactKind, ArtifactRef
from openrtl.domain.decisions import DecisionRecord
from openrtl.domain.evidence import EvidenceIndex, EvidenceRecord, RunBundle


class ExpertRole(str, Enum):
    DESIGN_LEAD = "design_lead"
    LEARNING_COACH = "learning_coach"
    DESIGN_ARCHITECT = "design_architect"
    REUSE_INTEGRATION_ARCHITECT = "reuse_integration_architect"
    REFERENCE_MODEL_ENGINEER = "reference_model_engineer"
    VERIFICATION_ARCHITECT = "verification_architect"
    RTL_ENGINEER = "rtl_engineer"
    ASSERTION_ENGINEER = "assertion_engineer"
    DV_ENGINEER = "dv_engineer"
    DIAGNOSIS_CLOSURE_ENGINEER = "diagnosis_closure_engineer"
    SIGNOFF_REVIEWER = "signoff_reviewer"


@dataclass(frozen=True, order=True)
class ContextItem:
    item_id: str
    item_type: str
    uri: str
    content_digest: str
    summary: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", identifier(self.item_id, "item_id"))
        object.__setattr__(self, "item_type", identifier(self.item_type, "item_type"))
        object.__setattr__(self, "uri", relative_path(self.uri, "uri"))
        object.__setattr__(
            self,
            "content_digest",
            digest(self.content_digest, "content_digest"),
        )
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))


@dataclass(frozen=True)
class ContextRequest:
    role: ExpertRole
    objective: str
    artifact_kinds: tuple[ArtifactKind, ...]
    requirement_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    run_id: str | None = None
    attempt: int = 1
    attached_items: tuple[ContextItem, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "objective", nonempty(self.objective, "objective"))
        kinds = tuple(self.artifact_kinds)
        requirements = tuple(identifier(value, "requirement_id") for value in self.requirement_ids)
        evidence = tuple(identifier(value, "evidence_id") for value in self.evidence_ids)
        decisions = tuple(identifier(value, "decision_id") for value in self.decision_ids)
        if len(set(kinds)) != len(kinds):
            raise ValueError("artifact_kinds must be unique")
        if len(set(requirements)) != len(requirements):
            raise ValueError("requirement_ids must be unique")
        if len(set(evidence)) != len(evidence):
            raise ValueError("evidence_ids must be unique")
        if len(set(decisions)) != len(decisions):
            raise ValueError("decision_ids must be unique")
        if self.run_id is not None:
            object.__setattr__(self, "run_id", identifier(self.run_id, "run_id"))
        if self.attempt < 1:
            raise ValueError("attempt must be positive")
        attached = tuple(self.attached_items)
        if len({value.item_id for value in attached}) != len(attached):
            raise ValueError("attached context item identifiers must be unique")
        object.__setattr__(self, "artifact_kinds", kinds)
        object.__setattr__(self, "requirement_ids", requirements)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "decision_ids", decisions)
        object.__setattr__(self, "attached_items", attached)


@dataclass(frozen=True)
class ContextPack:
    pack_id: str
    role: ExpertRole
    objective: str
    attempt: int
    items: tuple[ContextItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pack_id", identifier(self.pack_id, "pack_id"))
        if not self.items:
            raise ValueError("a context pack must contain at least one item")
        if len(set(self.items)) != len(self.items):
            raise ValueError("context items must be unique")
        if len({value.item_id for value in self.items}) != len(self.items):
            raise ValueError("context item identifiers must be unique")


class ProjectKnowledgeBase:
    def __init__(self) -> None:
        self.artifacts = ArtifactGraph()
        self.evidence = EvidenceIndex()
        self._decisions: dict[str, DecisionRecord] = {}
        self._runs: dict[str, RunBundle] = {}

    def add_decision(self, decision: DecisionRecord) -> None:
        if decision.decision_id in self._decisions:
            raise ValueError(f"decision already exists: {decision.decision_id}")
        for ref in decision.artifact_refs:
            self.artifacts.resolve(ref)
        self._decisions[decision.decision_id] = decision

    def decision(self, decision_id: str) -> DecisionRecord:
        normalized = identifier(decision_id, "decision_id")
        try:
            return self._decisions[normalized]
        except KeyError as error:
            raise KeyError(f"unknown decision: {normalized}") from error

    def add_run(self, run: RunBundle) -> None:
        if run.run_id in self._runs:
            raise ValueError(f"run already exists: {run.run_id}")
        for ref in run.artifact_refs:
            self.artifacts.resolve(ref)
        for evidence_id in run.evidence_ids:
            self.evidence.resolve(evidence_id)
        self._runs[run.run_id] = run

    def run(self, run_id: str) -> RunBundle:
        normalized = identifier(run_id, "run_id")
        try:
            return self._runs[normalized]
        except KeyError as error:
            raise KeyError(f"unknown run: {normalized}") from error


class ContextPackBuilder:
    def __init__(self, knowledge: ProjectKnowledgeBase) -> None:
        self._knowledge = knowledge

    def build(self, request: ContextRequest) -> ContextPack:
        items: list[ContextItem] = list(request.attached_items)
        for revision in self._knowledge.artifacts.latest_by_kind(request.artifact_kinds):
            if request.requirement_ids and not set(request.requirement_ids).intersection(
                revision.requirement_ids
            ):
                continue
            items.append(
                ContextItem(
                    item_id=revision.ref.key.replace("@", "-r"),
                    item_type=f"artifact.{revision.kind.value}",
                    uri=revision.uri,
                    content_digest=revision.content_digest,
                    summary=revision.summary,
                )
            )
        evidence_records: dict[str, EvidenceRecord] = {
            record.evidence_id: record
            for record in self._knowledge.evidence.for_requirements(request.requirement_ids)
        }
        for evidence_id in request.evidence_ids:
            record = self._knowledge.evidence.resolve(evidence_id)
            evidence_records[record.evidence_id] = record
        for record in sorted(evidence_records.values(), key=lambda value: value.evidence_id):
            items.append(
                ContextItem(
                    item_id=record.evidence_id,
                    item_type="evidence",
                    uri=f"evidence/{record.evidence_id}.json",
                    content_digest=_record_digest(record.evidence_id, record.summary),
                    summary=record.summary,
                )
            )
        for decision_id in request.decision_ids:
            decision = self._knowledge.decision(decision_id)
            items.append(
                ContextItem(
                    item_id=decision.decision_id,
                    item_type="decision",
                    uri=f"decisions/{decision.decision_id}.json",
                    content_digest=_record_digest(decision.decision_id, decision.rationale),
                    summary=decision.title,
                )
            )
        if request.run_id is not None:
            run = self._knowledge.run(request.run_id)
            items.append(
                ContextItem(
                    item_id=run.run_id,
                    item_type="run",
                    uri=run.log_uri,
                    content_digest=_record_digest(
                        run.run_id,
                        f"{run.status.value}:{run.failure_signature or 'none'}",
                    ),
                    summary=f"{run.status.value} run with tool profile {run.tool_profile_id}",
                )
            )
        ordered = tuple(sorted(items))
        if not ordered:
            raise ValueError("context request selected no project knowledge")
        pack_digest = _pack_digest(request, ordered)
        return ContextPack(
            pack_id=f"ctx-{pack_digest[:20]}",
            role=request.role,
            objective=request.objective,
            attempt=request.attempt,
            items=ordered,
        )


def _record_digest(identity: str, content: str) -> str:
    encoded = f"{identity}\n{content}".encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _pack_digest(request: ContextRequest, items: tuple[ContextItem, ...]) -> str:
    payload = {
        "attempt": request.attempt,
        "items": [
            {
                "digest": item.content_digest,
                "id": item.item_id,
                "type": item.item_type,
                "uri": item.uri,
            }
            for item in items
        ],
        "objective": request.objective,
        "role": request.role.value,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
