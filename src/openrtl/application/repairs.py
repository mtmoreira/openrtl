"""Reviewable repair proposals derived from immutable debug evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from openrtl.application.debugging import DebugSessionReport
from openrtl.domain import ArtifactKind, ContextItem, ExpertRole, SourceAnchor, WaveformAnchor
from openrtl.domain._validation import identifier, nonempty, unique_identifiers


@dataclass(frozen=True)
class RepairChange:
    change_id: str
    artifact_kind: ArtifactKind
    summary: str
    rationale: str
    finding_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    source_anchors: tuple[SourceAnchor, ...]
    waveform_anchors: tuple[WaveformAnchor, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_id", identifier(self.change_id, "change_id"))
        object.__setattr__(self, "summary", nonempty(self.summary, "summary"))
        object.__setattr__(self, "rationale", nonempty(self.rationale, "rationale"))
        finding_ids = unique_identifiers(self.finding_ids, "finding_id")
        requirement_ids = unique_identifiers(self.requirement_ids, "requirement_id")
        source_anchors = tuple(self.source_anchors)
        waveform_anchors = tuple(self.waveform_anchors)
        if not finding_ids or not requirement_ids:
            raise ValueError("repair change requires findings and requirements")
        if not source_anchors or len(set(source_anchors)) != len(source_anchors):
            raise ValueError("repair change requires unique source anchors")
        if not waveform_anchors or len(set(waveform_anchors)) != len(waveform_anchors):
            raise ValueError("repair change requires unique waveform anchors")
        object.__setattr__(self, "finding_ids", finding_ids)
        object.__setattr__(self, "requirement_ids", requirement_ids)
        object.__setattr__(self, "source_anchors", source_anchors)
        object.__setattr__(self, "waveform_anchors", waveform_anchors)

    def payload(self) -> dict[str, Any]:
        return {
            "artifact_kind": self.artifact_kind.value,
            "change_id": self.change_id,
            "finding_ids": self.finding_ids,
            "rationale": self.rationale,
            "requirement_ids": self.requirement_ids,
            "source_anchors": [_source_anchor_payload(value) for value in self.source_anchors],
            "summary": self.summary,
            "waveform_anchors": [
                _waveform_anchor_payload(value) for value in self.waveform_anchors
            ],
        }


@dataclass(frozen=True)
class RepairProposal:
    proposal_id: str
    design_id: str
    debug_session_id: str
    failure_signature: str
    confidence_percent: int
    context_item: ContextItem
    changes: tuple[RepairChange, ...]
    validation_steps: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "proposal_id",
            "design_id",
            "debug_session_id",
            "failure_signature",
        ):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        if self.confidence_percent < 0 or self.confidence_percent > 100:
            raise ValueError("confidence_percent must be between zero and one hundred")
        changes = tuple(self.changes)
        if not changes or len({value.change_id for value in changes}) != len(changes):
            raise ValueError("repair proposal requires uniquely identified changes")
        steps = tuple(nonempty(value, "validation step") for value in self.validation_steps)
        if not steps or len(set(steps)) != len(steps):
            raise ValueError("repair proposal requires unique validation steps")
        if self.context_item.item_type != "debug.session":
            raise ValueError("repair proposal context must be a debug session")
        if self.context_item.item_id != self.debug_session_id:
            raise ValueError("repair proposal context must match its debug session")
        object.__setattr__(self, "changes", changes)
        object.__setattr__(self, "validation_steps", steps)

    @property
    def expert_role(self) -> ExpertRole:
        return ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "changes": [value.payload() for value in self.changes],
            "confidence_percent": self.confidence_percent,
            "context_item": {
                "content_digest": self.context_item.content_digest,
                "item_id": self.context_item.item_id,
                "item_type": self.context_item.item_type,
                "summary": self.context_item.summary,
                "uri": self.context_item.uri,
            },
            "debug_session_id": self.debug_session_id,
            "design_id": self.design_id,
            "expert_role": self.expert_role.value,
            "failure_signature": self.failure_signature,
            "proposal_id": self.proposal_id,
            "schema": "openrtl.repair-proposal.v1",
            "status": "proposed",
            "validation_steps": self.validation_steps,
        }


def build_repair_proposal(
    report: DebugSessionReport,
    *,
    report_uri: str,
    changes: tuple[RepairChange, ...],
    validation_steps: tuple[str, ...],
    confidence_percent: int,
) -> RepairProposal:
    """Bind proposed changes to every finding in one failed debug session."""

    if report.passed:
        raise ValueError("repair proposal requires a failing debug session")
    selected_changes = tuple(changes)
    known_findings = {value.finding_id: value for value in report.findings}
    linked_findings = tuple(
        finding_id for change in selected_changes for finding_id in change.finding_ids
    )
    covered_findings = set(linked_findings)
    if covered_findings != set(known_findings):
        raise ValueError("repair changes must cover every debug finding exactly by identity")
    if len(linked_findings) != len(covered_findings):
        raise ValueError("each debug finding must be covered by exactly one repair change")
    report_sources = set(report.source_anchors)
    report_waveforms = {value.waveform_anchor for value in report.findings}
    for change in selected_changes:
        if not set(change.source_anchors).issubset(report_sources):
            raise ValueError("repair source anchor is absent from the debug session")
        if not set(change.waveform_anchors).issubset(report_waveforms):
            raise ValueError("repair waveform anchor is absent from the debug session")
        linked_requirements = {
            known_findings[value].requirement_id for value in change.finding_ids
        }
        if set(change.requirement_ids) != linked_requirements:
            raise ValueError("repair requirements must match linked debug findings")

    report_payload = report.payload()
    report_digest = _payload_digest(report_payload)
    context_item = ContextItem(
        report.session_id,
        "debug.session",
        report_uri,
        report_digest,
        f"Failed {report.design_id} debug session with {len(report.findings)} findings.",
    )
    signature_digest = hashlib.sha256(
        "\n".join(
            f"{value.finding_id}:{value.expected}:{value.observed}"
            for value in report.findings
        ).encode()
    ).hexdigest()[:20]
    proposal_digest = hashlib.sha256(
        json.dumps(
            {
                "changes": [value.payload() for value in selected_changes],
                "debug_session": report.session_id,
                "report_digest": report_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:20]
    return RepairProposal(
        f"repair.{proposal_digest}",
        report.design_id,
        report.session_id,
        f"debug.failure.{signature_digest}",
        confidence_percent,
        context_item,
        selected_changes,
        validation_steps,
    )


def _payload_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _source_anchor_payload(anchor: SourceAnchor) -> dict[str, Any]:
    return {
        "content_digest": anchor.content_digest,
        "line_end": anchor.line_end,
        "line_start": anchor.line_start,
        "path": anchor.path,
    }


def _waveform_anchor_payload(anchor: WaveformAnchor) -> dict[str, Any]:
    return {
        "end_fs": anchor.end_fs,
        "markers_fs": anchor.markers_fs,
        "signals": anchor.signals,
        "start_fs": anchor.start_fs,
        "trace_id": anchor.trace_id,
    }
