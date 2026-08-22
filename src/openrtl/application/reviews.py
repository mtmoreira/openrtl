"""Evidence-linked diagnosis, traceability, and review outputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from openrtl.domain import EvidenceRecord, SourceAnchor, WaveformAnchor
from openrtl.domain._validation import identifier, nonempty


class ReviewKind(str, Enum):
    DESIGN = "design"
    VERIFICATION = "verification"
    LOG = "log"
    WAVEFORM = "waveform"
    SIGNOFF = "signoff"


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    title: str
    explanation: str
    requirement_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...] = ()
    waveform_anchors: tuple[WaveformAnchor, ...] = ()
    blocking: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", identifier(self.finding_id, "finding_id"))
        object.__setattr__(self, "title", nonempty(self.title, "title"))
        object.__setattr__(self, "explanation", nonempty(self.explanation, "explanation"))
        for field_name, values in (
            ("requirement_id", self.requirement_ids),
            ("evidence_id", self.evidence_ids),
        ):
            normalized = tuple(identifier(value, field_name) for value in values)
            if len(set(normalized)) != len(normalized):
                raise ValueError(f"{field_name} values must be unique")
            object.__setattr__(self, f"{field_name}s", normalized)


@dataclass(frozen=True)
class ReviewReport:
    review_id: str
    kind: ReviewKind
    summary: str
    findings: tuple[ReviewFinding, ...]

    @property
    def passed(self) -> bool:
        return not any(value.blocking for value in self.findings)


@dataclass(frozen=True)
class DiagnosisReport:
    diagnosis_id: str
    failure_signature: str
    root_cause: str
    confidence_percent: int
    evidence_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    waveform_anchors: tuple[WaveformAnchor, ...]
    proposed_changes: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnosis_id", identifier(self.diagnosis_id, "diagnosis_id"))
        object.__setattr__(
            self,
            "failure_signature",
            identifier(self.failure_signature, "failure_signature"),
        )
        object.__setattr__(self, "root_cause", nonempty(self.root_cause, "root_cause"))
        if self.confidence_percent < 0 or self.confidence_percent > 100:
            raise ValueError("confidence_percent must be between zero and one hundred")
        if not self.evidence_ids or not self.proposed_changes:
            raise ValueError("diagnosis requires evidence and proposed changes")


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_id: str
    artifact_keys: tuple[str, ...]
    evidence_ids: tuple[str, ...]

    @property
    def covered(self) -> bool:
        return bool(self.artifact_keys and self.evidence_ids)


def build_requirement_coverage(
    requirement_ids: tuple[str, ...],
    evidence: tuple[EvidenceRecord, ...],
) -> tuple[RequirementCoverage, ...]:
    rows: list[RequirementCoverage] = []
    for requirement_id in sorted(requirement_ids):
        normalized = identifier(requirement_id, "requirement_id")
        selected = tuple(
            record for record in evidence if any(
                getattr(anchor, "requirement_id", None) == normalized for anchor in record.anchors
            )
        )
        rows.append(
            RequirementCoverage(
                normalized,
                tuple(sorted({ref.key for record in selected for ref in record.artifact_refs})),
                tuple(record.evidence_id for record in selected),
            )
        )
    return tuple(rows)
