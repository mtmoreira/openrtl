"""Evidence anchors, standardized runs, and safe evidence summaries."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from openrtl.domain._validation import digest, identifier, nonempty, relative_path
from openrtl.domain.artifacts import ArtifactRef


@dataclass(frozen=True)
class RequirementAnchor:
    requirement_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirement_id", identifier(self.requirement_id, "requirement_id"))


@dataclass(frozen=True)
class SourceAnchor:
    path: str
    line_start: int
    line_end: int
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", relative_path(self.path))
        object.__setattr__(self, "content_digest", digest(self.content_digest, "content_digest"))
        if self.line_start < 1 or self.line_end < self.line_start:
            raise ValueError("source line range is invalid")


@dataclass(frozen=True)
class LogAnchor:
    run_id: str
    event_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", identifier(self.run_id, "run_id"))
        object.__setattr__(self, "event_id", identifier(self.event_id, "event_id"))


@dataclass(frozen=True)
class WaveformAnchor:
    trace_id: str
    start_fs: int
    end_fs: int
    signals: tuple[str, ...]
    markers_fs: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "trace_id", identifier(self.trace_id, "trace_id"))
        if self.start_fs < 0 or self.end_fs <= self.start_fs:
            raise ValueError("waveform interval is invalid")
        signals = tuple(nonempty(value, "signal") for value in self.signals)
        markers = tuple(self.markers_fs)
        if not signals or len(set(signals)) != len(signals):
            raise ValueError("signals must be non-empty and unique")
        if len(set(markers)) != len(markers) or any(
            marker < self.start_fs or marker > self.end_fs for marker in markers
        ):
            raise ValueError("markers must be unique and inside the waveform interval")
        object.__setattr__(self, "signals", signals)
        object.__setattr__(self, "markers_fs", markers)


@dataclass(frozen=True)
class PackageAnchor:
    package_id: str
    version: str
    content_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", identifier(self.package_id, "package_id"))
        object.__setattr__(self, "version", nonempty(self.version, "version"))
        object.__setattr__(self, "content_digest", digest(self.content_digest, "content_digest"))


EvidenceAnchor: TypeAlias = RequirementAnchor | SourceAnchor | LogAnchor | WaveformAnchor | PackageAnchor


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    summary: str
    anchors: tuple[EvidenceAnchor, ...]
    artifact_refs: tuple[ArtifactRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", identifier(self.evidence_id, "evidence_id"))
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))
        anchors = tuple(self.anchors)
        refs = tuple(self.artifact_refs)
        if not anchors or len(set(anchors)) != len(anchors):
            raise ValueError("anchors must be non-empty and unique")
        if len(set(refs)) != len(refs):
            raise ValueError("artifact_refs must be unique")
        object.__setattr__(self, "anchors", anchors)
        object.__setattr__(self, "artifact_refs", refs)


class EvidenceIndex:
    def __init__(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}

    def add(self, record: EvidenceRecord) -> None:
        if record.evidence_id in self._records:
            raise ValueError(f"evidence already exists: {record.evidence_id}")
        self._records[record.evidence_id] = record

    def resolve(self, evidence_id: str) -> EvidenceRecord:
        normalized = identifier(evidence_id, "evidence_id")
        try:
            return self._records[normalized]
        except KeyError as error:
            raise KeyError(f"unknown evidence: {normalized}") from error

    def for_requirements(self, requirement_ids: tuple[str, ...]) -> tuple[EvidenceRecord, ...]:
        selected = {identifier(value, "requirement_id") for value in requirement_ids}
        return tuple(
            record
            for record in sorted(self._records.values(), key=lambda value: value.evidence_id)
            if any(
                isinstance(anchor, RequirementAnchor) and anchor.requirement_id in selected
                for anchor in record.anchors
            )
        )


class RunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class RunBundle:
    run_id: str
    status: RunStatus
    tool_profile_id: str
    seed: int
    artifact_refs: tuple[ArtifactRef, ...]
    evidence_ids: tuple[str, ...]
    log_uri: str
    trace_uri: str | None = None
    failure_signature: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", identifier(self.run_id, "run_id"))
        object.__setattr__(self, "tool_profile_id", identifier(self.tool_profile_id, "tool_profile_id"))
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        refs = tuple(self.artifact_refs)
        evidence = tuple(identifier(value, "evidence_id") for value in self.evidence_ids)
        if not refs or len(set(refs)) != len(refs):
            raise ValueError("artifact_refs must be non-empty and unique")
        if len(set(evidence)) != len(evidence):
            raise ValueError("evidence_ids must be unique")
        object.__setattr__(self, "artifact_refs", refs)
        object.__setattr__(self, "evidence_ids", evidence)
        object.__setattr__(self, "log_uri", relative_path(self.log_uri, "log_uri"))
        if self.trace_uri is not None:
            object.__setattr__(self, "trace_uri", relative_path(self.trace_uri, "trace_uri"))
        if self.status is RunStatus.FAILED:
            object.__setattr__(
                self,
                "failure_signature",
                identifier(self.failure_signature or "", "failure_signature"),
            )
        elif self.failure_signature is not None:
            raise ValueError("only failed runs may have a failure_signature")
