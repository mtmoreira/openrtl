"""Candidate-only application of explicitly approved provider qualifications."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from openrtl.adapters.source_edit_application import (
    apply_reviewed_source_edits,
    load_source_edit_plan,
)
from openrtl.application import (
    ProviderOutputQualificationReport,
    QualifiedProviderApplicationReport,
    QualifiedProviderRepairApproval,
    RepairApplicationReport,
    SourceEditPlan,
    SourceEditPlanningReport,
    build_qualified_provider_application_report,
    provider_qualification_digest,
)


_MAX_JSON_BYTES = 1024 * 1024


def apply_qualified_provider_source_edits(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    edit_plan_path: Path,
    planning_report_path: Path,
    qualification_report_path: Path,
    output_path: Path,
    approval: QualifiedProviderRepairApproval,
) -> tuple[RepairApplicationReport, QualifiedProviderApplicationReport]:
    """Apply one exact provider-qualified plan to a separate candidate."""

    resolved_root = root.resolve(strict=True)
    qualification_payload = _read_json(
        _contained_file(
            resolved_root,
            qualification_report_path,
            "provider qualification report",
        ),
        "provider qualification report",
    )
    planning_payload = _read_json(
        _contained_file(
            resolved_root,
            planning_report_path,
            "source edit planning report",
        ),
        "source edit planning report",
    )
    qualification = parse_provider_qualification_report(qualification_payload)
    planning = _parse_planning_report(planning_payload)
    edit_plan = load_source_edit_plan(resolved_root, edit_plan_path)
    approval.require_matches(qualification)
    _validate_review_artifacts(qualification, planning, planning_payload, edit_plan)

    application = apply_reviewed_source_edits(
        resolved_root,
        proposal_path=proposal_path,
        debug_session_path=debug_session_path,
        edit_plan_path=edit_plan_path,
        output_path=output_path,
        approval=approval.repair_approval(),
    )
    receipt = build_qualified_provider_application_report(
        qualification,
        approval,
        application,
    )
    return application, receipt


def _validate_review_artifacts(
    qualification: ProviderOutputQualificationReport,
    planning: SourceEditPlanningReport,
    planning_payload: dict[str, Any],
    edit_plan: SourceEditPlan,
) -> None:
    if (
        qualification.planning_id != planning.planning_id
        or qualification.planning_report_digest
        != provider_qualification_digest(planning_payload)
        or qualification.proposal_id != planning.proposal_id
        or qualification.debug_session_id != planning.debug_session_id
        or qualification.edit_plan_id != planning.edit_plan_id
        or qualification.edit_plan_digest != planning.edit_plan_digest
        or qualification.change_ids != planning.change_ids
        or qualification.edit_ids != planning.edit_ids
    ):
        raise ValueError("planning report does not match provider qualification")
    if (
        edit_plan.edit_plan_id != qualification.edit_plan_id
        or edit_plan.content_digest != qualification.edit_plan_digest
        or edit_plan.proposal_id != qualification.proposal_id
        or edit_plan.debug_session_id != qualification.debug_session_id
        or edit_plan.source_path != qualification.source_path
        or edit_plan.source_digest != qualification.source_digest
        or tuple(dict.fromkeys(value.change_id for value in edit_plan.edits))
        != qualification.change_ids
        or tuple(value.edit_id for value in edit_plan.edits) != qualification.edit_ids
    ):
        raise ValueError("source edit plan does not match provider qualification")


def parse_provider_qualification_report(
    payload: dict[str, Any],
) -> ProviderOutputQualificationReport:
    """Reconstruct one exact canonical provider qualification receipt."""
    if set(payload) != {
        "applies_changes",
        "content_digest",
        "debug_session_id",
        "edit_plan",
        "edit_spec",
        "lineage",
        "next_gate",
        "planning",
        "proposal_id",
        "provider_output_trusted",
        "qualification_id",
        "schema",
        "source",
        "status",
    }:
        raise ValueError("provider qualification report fields are invalid")
    edit_plan = _object(payload.get("edit_plan"), "qualification edit plan")
    edit_spec = _object(payload.get("edit_spec"), "qualification edit spec")
    lineage = _object(payload.get("lineage"), "qualification lineage")
    invocation = _object(lineage.get("invocation"), "qualification invocation")
    provider_plan = _object(lineage.get("provider_plan"), "qualification provider plan")
    request = _object(lineage.get("request"), "qualification request")
    suggestion = _object(lineage.get("suggestion"), "qualification suggestion")
    planning = _object(payload.get("planning"), "qualification planning")
    source = _object(payload.get("source"), "qualification source")
    try:
        report = ProviderOutputQualificationReport(
            cast(str, payload.get("qualification_id")),
            cast(str, provider_plan.get("plan_id")),
            cast(str, provider_plan.get("content_digest")),
            cast(str, lineage.get("provider_execution_digest")),
            cast(str, request.get("request_id")),
            cast(str, request.get("content_digest")),
            cast(str, invocation.get("invocation_id")),
            cast(str, invocation.get("content_digest")),
            cast(str, suggestion.get("suggestion_id")),
            cast(str, suggestion.get("content_digest")),
            cast(str, edit_spec.get("canonical_digest")),
            cast(str, edit_spec.get("file_digest")),
            cast(str, payload.get("proposal_id")),
            cast(str, payload.get("debug_session_id")),
            cast(str, source.get("path")),
            cast(str, source.get("content_digest")),
            cast(str, edit_plan.get("edit_plan_id")),
            cast(str, edit_plan.get("content_digest")),
            cast(str, planning.get("planning_id")),
            cast(str, planning.get("content_digest")),
            tuple(_strings(lineage.get("change_ids"), "qualification change ids")),
            tuple(_strings(lineage.get("edit_ids"), "qualification edit ids")),
        )
    except (TypeError, ValueError):
        raise ValueError("provider qualification report is invalid") from None
    if report.payload() != payload:
        raise ValueError("provider qualification report is not canonical")
    return report


def _parse_planning_report(payload: dict[str, Any]) -> SourceEditPlanningReport:
    if set(payload) != {
        "applies_changes",
        "change_ids",
        "debug_session",
        "edit_ids",
        "edit_plan",
        "edit_spec_digest",
        "planning_id",
        "proposal",
        "review",
        "schema",
        "status",
    }:
        raise ValueError("source edit planning report fields are invalid")
    proposal = _object(payload.get("proposal"), "planning proposal")
    debug = _object(payload.get("debug_session"), "planning debug session")
    edit_plan = _object(payload.get("edit_plan"), "planning edit plan")
    try:
        report = SourceEditPlanningReport(
            cast(str, payload.get("planning_id")),
            cast(str, proposal.get("proposal_id")),
            cast(str, proposal.get("content_digest")),
            cast(str, debug.get("session_id")),
            cast(str, debug.get("content_digest")),
            cast(str, payload.get("edit_spec_digest")),
            cast(str, edit_plan.get("edit_plan_id")),
            cast(str, edit_plan.get("content_digest")),
            tuple(_strings(payload.get("change_ids"), "planning change ids")),
            tuple(_strings(payload.get("edit_ids"), "planning edit ids")),
        )
    except (TypeError, ValueError):
        raise ValueError("source edit planning report is invalid") from None
    normalized = json.loads(json.dumps(report.payload(), sort_keys=True))
    if normalized != payload:
        raise ValueError("source edit planning report is not canonical")
    return report


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds its byte bound")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{label} is not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _contained_file(root: Path, candidate: Path, label: str) -> Path:
    path = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ValueError(f"{label} must be a contained file") from None
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a non-empty string list")
    return cast(list[str], value)
