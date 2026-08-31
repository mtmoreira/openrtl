"""Fail-closed planning for promotion of validated repair candidates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from openrtl.adapters.qualified_provider_application import (
    parse_provider_qualification_report,
)
from openrtl.application import (
    CandidatePromotionPlan,
    QualifiedProviderApplicationReport,
    RepairApplicationReport,
    build_candidate_promotion_plan,
    canonical_payload_digest,
    qualified_provider_application_digest,
)


_MAX_JSON_BYTES = 2 * 1024 * 1024


def plan_qualified_provider_candidate_promotion(
    root: Path,
    *,
    qualification_report_path: Path,
    application_report_path: Path,
    qualified_application_report_path: Path,
    candidate_path: Path,
    target_path: Path,
    comparison_path: Path,
    evidence_path: Path,
) -> CandidatePromotionPlan:
    """Build a non-applying promotion plan from one exact validated lineage."""

    resolved_root = root.resolve(strict=True)
    qualification_file = _contained_file(
        resolved_root, qualification_report_path, "provider qualification report"
    )
    application_file = _contained_file(
        resolved_root, application_report_path, "repair application report"
    )
    qualified_file = _contained_file(
        resolved_root,
        qualified_application_report_path,
        "qualified application report",
    )
    selected_candidate = _contained_file(
        resolved_root, candidate_path, "repair candidate"
    )
    selected_target = _contained_file(resolved_root, target_path, "promotion target")
    comparison_file = _contained_file(
        resolved_root, comparison_path, "repair comparison"
    )
    evidence_file = _contained_file(resolved_root, evidence_path, "repair evidence")

    qualification_payload = _read_json(qualification_file, "provider qualification")
    application_payload = _read_json(application_file, "repair application")
    qualified_payload = _read_json(qualified_file, "qualified application")
    comparison = _read_json(comparison_file, "repair comparison")
    evidence = _read_json(evidence_file, "repair evidence")

    qualification = parse_provider_qualification_report(qualification_payload)
    application = _parse_application(application_payload)
    qualified = _parse_qualified_application(
        qualified_payload,
        qualification_id=qualification.qualification_id,
        qualification_digest=qualification.content_digest,
        application=application,
    )
    candidate_relative = selected_candidate.relative_to(resolved_root).as_posix()
    target_relative = selected_target.relative_to(resolved_root).as_posix()
    candidate_digest = _file_digest(selected_candidate)
    target_digest = _file_digest(selected_target)
    if (
        application.output_path != candidate_relative
        or application.source_path != target_relative
        or application.source_digest_after != candidate_digest
        or application.source_digest_before != target_digest
    ):
        raise ValueError("candidate or target bytes differ from the reviewed application")

    comparison_digest, before_waveform, repaired_waveform = _validate_comparison(
        comparison,
        application=application,
    )
    artifacts = _validate_evidence(
        resolved_root,
        evidence,
        application=application,
        qualified=qualified,
        qualification_id=qualification.qualification_id,
        candidate=selected_candidate,
        comparison=comparison_file,
        application_file=application_file,
        qualified_file=qualified_file,
        qualification_file=qualification_file,
        before_waveform=before_waveform,
        repaired_waveform=repaired_waveform,
    )
    return build_candidate_promotion_plan(
        qualified_application_id=qualified.qualified_application_id,
        qualified_application_digest=qualified_provider_application_digest(
            qualified.payload()
        ),
        application_id=application.application_id,
        application_digest=canonical_payload_digest(application.payload()),
        qualification_id=qualification.qualification_id,
        qualification_digest=qualification.content_digest,
        proposal_id=application.proposal_id,
        edit_plan_digest=application.edit_plan_digest,
        change_ids=application.change_ids,
        candidate_path=candidate_relative,
        candidate_digest=candidate_digest,
        target_path=target_relative,
        target_digest=target_digest,
        comparison_path=comparison_file.relative_to(resolved_root).as_posix(),
        comparison_digest=comparison_digest,
        evidence_path=evidence_file.relative_to(resolved_root).as_posix(),
        evidence_digest=_file_digest(evidence_file),
        before_results_path=artifacts["before_results"][0],
        before_results_digest=artifacts["before_results"][1],
        before_waveform_path=artifacts["before_waveform"][0],
        before_waveform_digest=artifacts["before_waveform"][1],
        repaired_results_path=artifacts["repaired_results"][0],
        repaired_results_digest=artifacts["repaired_results"][1],
        repaired_waveform_path=artifacts["repaired_waveform"][0],
        repaired_waveform_digest=artifacts["repaired_waveform"][1],
    )


def _parse_application(payload: dict[str, Any]) -> RepairApplicationReport:
    if set(payload) != {
        "application_id",
        "authorization",
        "changed_line_numbers",
        "debug_session_id",
        "edit_ids",
        "edit_plan_digest",
        "edit_plan_id",
        "output_path",
        "proposal_id",
        "schema",
        "source_digest_after",
        "source_digest_before",
        "source_path",
        "status",
    }:
        raise ValueError("repair application report fields are invalid")
    authorization = _object(payload.get("authorization"), "repair authorization")
    try:
        report = RepairApplicationReport(
            cast(str, payload.get("application_id")),
            cast(str, payload.get("proposal_id")),
            cast(str, payload.get("debug_session_id")),
            cast(str, payload.get("edit_plan_id")),
            cast(str, payload.get("edit_plan_digest")),
            tuple(_strings(authorization.get("approved_change_ids"), "approved changes")),
            tuple(_strings(payload.get("edit_ids"), "application edit ids")),
            cast(str, payload.get("source_path")),
            cast(str, payload.get("output_path")),
            cast(str, payload.get("source_digest_before")),
            cast(str, payload.get("source_digest_after")),
            tuple(_integers(payload.get("changed_line_numbers"), "changed lines")),
            cast(str, authorization.get("review_note")),
        )
    except (TypeError, ValueError):
        raise ValueError("repair application report is invalid") from None
    if json.loads(json.dumps(report.payload(), sort_keys=True)) != payload:
        raise ValueError("repair application report is not canonical")
    return report


def _parse_qualified_application(
    payload: dict[str, Any],
    *,
    qualification_id: str,
    qualification_digest: str,
    application: RepairApplicationReport,
) -> QualifiedProviderApplicationReport:
    if set(payload) != {
        "application",
        "authorization",
        "qualification",
        "qualified_application_id",
        "schema",
        "status",
    }:
        raise ValueError("qualified application report fields are invalid")
    authorization = _object(payload.get("authorization"), "qualified authorization")
    try:
        report = QualifiedProviderApplicationReport(
            cast(str, payload.get("qualified_application_id")),
            qualification_id,
            qualification_digest,
            cast(str, authorization.get("approval_digest")),
            application,
        )
    except (TypeError, ValueError):
        raise ValueError("qualified application report is invalid") from None
    if report.payload() != payload:
        raise ValueError("qualified application report is not canonical")
    return report


def _validate_comparison(
    payload: dict[str, Any],
    *,
    application: RepairApplicationReport,
) -> tuple[str, str, str]:
    if set(payload) != {
        "after",
        "application_id",
        "before",
        "proposal_id",
        "schema",
        "status",
        "visual_evidence",
    }:
        raise ValueError("repair comparison fields are invalid")
    before = _object(payload.get("before"), "comparison before")
    after = _object(payload.get("after"), "comparison after")
    visual = _object(payload.get("visual_evidence"), "visual evidence")
    visual_before = _object(visual.get("before"), "visual before")
    visual_after = _object(visual.get("repaired"), "visual repaired")
    before_findings = before.get("finding_ids")
    after_findings = after.get("finding_ids")
    before_waveform = before.get("waveform")
    repaired_waveform = after.get("waveform")
    if (
        payload.get("schema") != "openrtl.repair-comparison.v2"
        or payload.get("status") != "validated"
        or payload.get("application_id") != application.application_id
        or payload.get("proposal_id") != application.proposal_id
        or before.get("passed") is not False
        or after.get("passed") is not True
        or not isinstance(before_findings, list)
        or not before_findings
        or any(not isinstance(value, str) for value in before_findings)
        or after_findings != []
        or not isinstance(before_waveform, str)
        or not isinstance(repaired_waveform, str)
        or visual.get("status") != "visibly_distinct"
        or visual_before.get("level_at_marker") != 0
        or visual_after.get("level_at_marker") != 1
    ):
        raise ValueError("repair comparison did not prove exact visible closure")
    return canonical_payload_digest(payload), before_waveform, repaired_waveform


def _validate_evidence(
    root: Path,
    payload: dict[str, Any],
    *,
    application: RepairApplicationReport,
    qualified: QualifiedProviderApplicationReport,
    qualification_id: str,
    candidate: Path,
    comparison: Path,
    application_file: Path,
    qualified_file: Path,
    qualification_file: Path,
    before_waveform: str,
    repaired_waveform: str,
) -> dict[str, tuple[str, str]]:
    if set(payload) != {
        "artifacts",
        "authorization_boundary",
        "qualified_application_id",
        "qualified_edit_plan_digest",
        "qualified_expert_suggestion_id",
        "qualified_provider_application_id",
        "qualified_provider_output_id",
        "schema",
        "status",
        "toolchain",
    }:
        raise ValueError("repair evidence fields are invalid")
    boundary = _object(payload.get("authorization_boundary"), "authorization boundary")
    if (
        payload.get("schema") != "openrtl.repair-application-evidence.v9"
        or payload.get("status") != "passed"
        or payload.get("qualified_application_id") != application.application_id
        or payload.get("qualified_provider_application_id")
        != qualified.qualified_application_id
        or payload.get("qualified_provider_output_id") != qualification_id
        or payload.get("qualified_edit_plan_digest") != application.edit_plan_digest
        or boundary
        != {
            "candidate_only": True,
            "gui_launched": False,
            "production_rtl_modified": False,
            "real_credential_resolved": False,
            "real_provider_called": False,
            "remote_operations": False,
            "synthetic_provider_adapter_calls": 1,
        }
    ):
        raise ValueError("repair evidence crossed its authorization boundary")
    artifacts = _object(payload.get("artifacts"), "repair evidence artifacts")
    expected = {
        "application": application_file,
        "comparison": comparison,
        "provider_output_qualification": qualification_file,
        "qualified_provider_application": qualified_file,
        "repaired_source": candidate,
    }
    validated: dict[str, tuple[str, str]] = {}
    for name, expected_path in expected.items():
        relative, content_digest = _validate_artifact(root, artifacts, name)
        if (root / relative).resolve(strict=True) != expected_path:
            raise ValueError(f"repair evidence {name} path differs from selected input")
        validated[name] = (relative, content_digest)
    for name in (
        "before_results",
        "before_waveform",
        "repaired_results",
        "repaired_waveform",
    ):
        relative, content_digest = _validate_artifact(root, artifacts, name)
        validated[name] = (relative, content_digest)
    if validated["before_waveform"][0] != before_waveform:
        raise ValueError("repair comparison before waveform differs from evidence")
    if validated["repaired_waveform"][0] != repaired_waveform:
        raise ValueError("repair comparison repaired waveform differs from evidence")
    return validated


def _validate_artifact(
    root: Path,
    artifacts: dict[str, Any],
    name: str,
) -> tuple[str, str]:
    entry = _object(artifacts.get(name), f"repair evidence artifact {name}")
    if set(entry) != {"path", "sha256", "size_bytes"}:
        raise ValueError(f"repair evidence artifact {name} fields are invalid")
    relative = entry.get("path")
    raw_digest = entry.get("sha256")
    size = entry.get("size_bytes")
    if (
        not isinstance(relative, str)
        or not isinstance(raw_digest, str)
        or len(raw_digest) != 64
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
    ):
        raise ValueError(f"repair evidence artifact {name} is invalid")
    path = _contained_file(root, Path(relative), f"repair evidence artifact {name}")
    if path.stat().st_size != size or _file_digest(path) != f"sha256:{raw_digest}":
        raise ValueError(f"repair evidence artifact {name} bytes changed")
    return path.relative_to(root).as_posix(), f"sha256:{raw_digest}"


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if path.stat().st_size > _MAX_JSON_BYTES:
        raise ValueError(f"{label} exceeds its byte bound")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError(f"{label} is not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return cast(dict[str, Any], value)


def _contained_file(root: Path, candidate: Path, label: str) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    if selected.is_symlink():
        raise ValueError(f"{label} must be a regular file")
    try:
        resolved = selected.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ValueError(f"{label} must be a contained file") from None
    if not resolved.is_file():
        raise ValueError(f"{label} must be a regular file")
    return resolved


def _file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _strings(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a non-empty string list")
    return cast(list[str], value)


def _integers(value: object, label: str) -> list[int]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, int) or isinstance(item, bool) for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty integer list")
    return cast(list[int], value)
