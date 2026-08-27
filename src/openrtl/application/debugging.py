"""Reviewable, evidence-linked debug-session contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from openrtl.domain import SourceAnchor, WaveformAnchor
from openrtl.domain._validation import digest, identifier, nonempty, relative_path


class DebugSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class DebugObservation:
    observation_id: str
    timestamp_fs: int
    event: str
    summary: str
    requirement_ids: tuple[str, ...]
    signal_values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_id",
            identifier(self.observation_id, "observation_id"),
        )
        object.__setattr__(self, "event", identifier(self.event, "event"))
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))
        if self.timestamp_fs < 0:
            raise ValueError("debug observation timestamp must not be negative")
        requirements = tuple(
            identifier(value, "requirement_id") for value in self.requirement_ids
        )
        if not requirements or len(set(requirements)) != len(requirements):
            raise ValueError("debug observation requirements must be non-empty and unique")
        values = tuple(
            (nonempty(name, "signal name"), nonempty(value, "signal value"))
            for name, value in self.signal_values
        )
        if not values or len({name for name, _ in values}) != len(values):
            raise ValueError("debug observation signal values must be non-empty and unique")
        object.__setattr__(self, "requirement_ids", requirements)
        object.__setattr__(self, "signal_values", values)

    def payload(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "observation_id": self.observation_id,
            "requirement_ids": self.requirement_ids,
            "signal_values": dict(self.signal_values),
            "summary": self.summary,
            "timestamp_fs": self.timestamp_fs,
        }


@dataclass(frozen=True)
class DebugFinding:
    finding_id: str
    severity: DebugSeverity
    requirement_id: str
    summary: str
    expected: str
    observed: str
    next_action: str
    waveform_anchor: WaveformAnchor

    def __post_init__(self) -> None:
        object.__setattr__(self, "finding_id", identifier(self.finding_id, "finding_id"))
        object.__setattr__(
            self,
            "requirement_id",
            identifier(self.requirement_id, "requirement_id"),
        )
        for field_name in ("summary", "expected", "observed", "next_action"):
            object.__setattr__(
                self,
                field_name,
                nonempty(getattr(self, field_name), field_name),
            )

    def payload(self) -> dict[str, Any]:
        return {
            "expected": self.expected,
            "finding_id": self.finding_id,
            "next_action": self.next_action,
            "observed": self.observed,
            "requirement_id": self.requirement_id,
            "severity": self.severity.value,
            "summary": self.summary,
            "waveform_anchor": _anchor_payload(self.waveform_anchor),
        }


@dataclass(frozen=True)
class DebugSessionReport:
    session_id: str
    design_id: str
    trace_uri: str
    trace_digest: str
    timescale_fs: int
    waveform_anchor: WaveformAnchor
    source_anchors: tuple[SourceAnchor, ...]
    metadata: tuple[tuple[str, str], ...]
    observations: tuple[DebugObservation, ...]
    findings: tuple[DebugFinding, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", identifier(self.session_id, "session_id"))
        object.__setattr__(self, "design_id", identifier(self.design_id, "design_id"))
        object.__setattr__(self, "trace_uri", relative_path(self.trace_uri, "trace_uri"))
        object.__setattr__(
            self,
            "trace_digest",
            digest(self.trace_digest, "trace_digest"),
        )
        if self.timescale_fs < 1:
            raise ValueError("debug-session timescale must be positive")
        metadata = tuple(
            (identifier(name, "metadata key"), nonempty(value, "metadata value"))
            for name, value in self.metadata
        )
        if len({name for name, _ in metadata}) != len(metadata):
            raise ValueError("debug-session metadata keys must be unique")
        source_anchors = tuple(self.source_anchors)
        if len(set(source_anchors)) != len(source_anchors):
            raise ValueError("debug-session source anchors must be unique")
        observations = tuple(self.observations)
        findings = tuple(self.findings)
        if not observations:
            raise ValueError("debug session requires observations")
        if len({value.observation_id for value in observations}) != len(observations):
            raise ValueError("debug observation identifiers must be unique")
        if len({value.finding_id for value in findings}) != len(findings):
            raise ValueError("debug finding identifiers must be unique")
        if any(
            value.timestamp_fs < self.waveform_anchor.start_fs
            or value.timestamp_fs > self.waveform_anchor.end_fs
            for value in observations
        ):
            raise ValueError("debug observations must lie inside the waveform anchor")
        object.__setattr__(self, "metadata", metadata)
        object.__setattr__(self, "source_anchors", source_anchors)
        object.__setattr__(self, "observations", observations)
        object.__setattr__(self, "findings", findings)

    @property
    def passed(self) -> bool:
        return not any(value.severity is DebugSeverity.ERROR for value in self.findings)

    def payload(self) -> dict[str, Any]:
        return {
            "design_id": self.design_id,
            "findings": [value.payload() for value in self.findings],
            "metadata": dict(self.metadata),
            "observations": [value.payload() for value in self.observations],
            "passed": self.passed,
            "schema": "openrtl.debug-session.v1",
            "session_id": self.session_id,
            "source_anchors": [
                {
                    "content_digest": value.content_digest,
                    "line_end": value.line_end,
                    "line_start": value.line_start,
                    "path": value.path,
                }
                for value in self.source_anchors
            ],
            "timescale_fs": self.timescale_fs,
            "trace_digest": self.trace_digest,
            "trace_uri": self.trace_uri,
            "waveform_anchor": _anchor_payload(self.waveform_anchor),
        }


def _anchor_payload(anchor: WaveformAnchor) -> dict[str, Any]:
    return {
        "end_fs": anchor.end_fs,
        "markers_fs": anchor.markers_fs,
        "signals": anchor.signals,
        "start_fs": anchor.start_fs,
        "trace_id": anchor.trace_id,
    }
