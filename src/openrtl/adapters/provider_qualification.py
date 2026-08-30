"""Fail-closed qualification of provider-produced source edit artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from agentrig.capabilities import GenerationUsage
from openrtl.adapters.provider_invocation import load_expert_provider_invocation_plan
from openrtl.adapters.source_edit_application import draft_source_edit_plan
from openrtl.application import (
    ExpertInvocationReport,
    ExpertProviderExecutionReport,
    ExpertProviderInvocationPlan,
    ExpertSourceEditReport,
    ProviderOutputQualificationReport,
    SourceEditPlan,
    SourceEditPlanningReport,
    build_provider_output_qualification_report,
    canonical_payload_digest,
    provider_invocation_digest,
    provider_qualification_digest,
)


_MAX_JSON_BYTES = 1024 * 1024


def qualify_provider_source_edits(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
    provider_plan_path: Path,
    provider_execution_report_path: Path,
    invocation_report_path: Path,
    suggestion_report_path: Path,
    edit_spec_path: Path,
) -> tuple[
    SourceEditPlan,
    SourceEditPlanningReport,
    ProviderOutputQualificationReport,
]:
    """Bind exact provider lifecycle evidence to deterministic qualification."""

    resolved_root = root.resolve(strict=True)
    plan = load_expert_provider_invocation_plan(resolved_root, provider_plan_path)
    execution_file = _contained_file(
        resolved_root,
        provider_execution_report_path,
        "provider execution report",
    )
    invocation_file = _contained_file(
        resolved_root,
        invocation_report_path,
        "provider invocation report",
    )
    suggestion_file = _contained_file(
        resolved_root,
        suggestion_report_path,
        "expert suggestion report",
    )
    edit_spec_file = _contained_file(
        resolved_root,
        edit_spec_path,
        "source edit specification",
    )
    execution = _read_json(execution_file, "provider execution report")
    invocation = _read_json(invocation_file, "provider invocation report")
    suggestion = _read_json(suggestion_file, "expert suggestion report")
    edit_spec = _read_json(edit_spec_file, "source edit specification")
    bindings = _validate_provider_lineage(
        plan,
        execution,
        invocation,
        suggestion,
        edit_spec,
    )

    edit_plan, planning = draft_source_edit_plan(
        resolved_root,
        proposal_path=proposal_path,
        debug_session_path=debug_session_path,
        source_path=source_path,
        edit_spec_path=edit_spec_path,
    )
    change_ids = tuple(cast(list[str], bindings["change_ids"]))
    edit_ids = tuple(cast(list[str], bindings["edit_ids"]))
    if tuple(dict.fromkeys(value.change_id for value in edit_plan.edits)) != change_ids:
        raise ValueError("provider suggestion changes differ from the qualified edit plan")
    if tuple(value.edit_id for value in edit_plan.edits) != edit_ids:
        raise ValueError("provider suggestion edits differ from the qualified edit plan")
    if (
        planning.proposal_id != bindings["proposal_id"]
        or planning.debug_session_id != bindings["debug_session_id"]
        or edit_plan.source_path != bindings["source_path"]
        or edit_plan.source_digest != bindings["source_digest"]
    ):
        raise ValueError("provider suggestion evidence differs from deterministic qualification")

    planning_payload = planning.payload()
    qualification = build_provider_output_qualification_report(
        provider_plan_id=plan.plan_id,
        provider_plan_digest=plan.content_digest,
        provider_execution_digest=provider_qualification_digest(execution),
        request_id=cast(str, bindings["request_id"]),
        request_digest=cast(str, bindings["request_digest"]),
        invocation_id=cast(str, bindings["invocation_id"]),
        invocation_report_digest=provider_invocation_digest(invocation),
        suggestion_id=cast(str, bindings["suggestion_id"]),
        suggestion_report_digest=provider_invocation_digest(suggestion),
        edit_spec_digest=canonical_payload_digest(edit_spec),
        edit_spec_file_digest=_file_digest(edit_spec_file),
        proposal_id=planning.proposal_id,
        debug_session_id=planning.debug_session_id,
        source_path=edit_plan.source_path,
        source_digest=edit_plan.source_digest,
        edit_plan_id=edit_plan.edit_plan_id,
        edit_plan_digest=edit_plan.content_digest,
        planning_id=planning.planning_id,
        planning_report_digest=provider_qualification_digest(planning_payload),
        change_ids=change_ids,
        edit_ids=edit_ids,
    )
    return edit_plan, planning, qualification


def _validate_provider_lineage(
    plan: ExpertProviderInvocationPlan,
    execution: dict[str, Any],
    invocation: dict[str, Any],
    suggestion: dict[str, Any],
    edit_spec: dict[str, Any],
) -> dict[str, object]:
    plan_payload = plan.payload()
    if set(execution) != {
        "applies_changes",
        "authorization",
        "invocation",
        "plan",
        "provider_output_trusted",
        "schema",
        "status",
    }:
        raise ValueError("provider execution report fields are invalid")
    authorization = _object(execution.get("authorization"), "provider authorization")
    execution_invocation = _object(execution.get("invocation"), "provider invocation binding")
    execution_plan = _object(execution.get("plan"), "provider plan binding")
    try:
        execution_model = ExpertProviderExecutionReport(
            cast(str, execution_plan.get("plan_id")),
            cast(str, execution_plan.get("content_digest")),
            cast(str, execution_invocation.get("invocation_id")),
            cast(str, execution_invocation.get("content_digest")),
            cast(str, authorization.get("review_note_digest")),
        )
    except (TypeError, ValueError):
        raise ValueError("provider execution report authority is invalid") from None
    if (
        execution.get("schema") != "openrtl.expert-provider-execution-report.v1"
        or execution.get("status") != "awaiting_qualification"
        or execution.get("applies_changes") is not False
        or execution.get("provider_output_trusted") is not False
        or authorization.get("credential_value_persisted") is not False
        or authorization.get("explicit_plan_digest_matched") is not True
        or authorization.get("provider_call_count") != 1
        or execution_model.payload() != execution
        or execution_plan
        != {"content_digest": plan.content_digest, "plan_id": plan.plan_id}
    ):
        raise ValueError("provider execution report authority is invalid")

    if set(invocation) != {
        "applies_changes",
        "envelope_digest",
        "finish_reason",
        "invocation_id",
        "next_gate",
        "provider_output_trusted",
        "request",
        "response_digest",
        "run_id",
        "runtime",
        "schema",
        "status",
        "suggestion",
        "usage",
    }:
        raise ValueError("provider invocation report fields are invalid")
    invocation_request = _object(invocation.get("request"), "provider invocation request")
    invocation_suggestion = _object(
        invocation.get("suggestion"),
        "provider invocation suggestion",
    )
    invocation_usage = _object(invocation.get("usage"), "provider invocation usage")
    try:
        invocation_model = ExpertInvocationReport(
            cast(str, invocation.get("invocation_id")),
            cast(str, invocation.get("run_id")),
            cast(str, invocation_request.get("request_id")),
            cast(str, invocation_request.get("content_digest")),
            cast(str, invocation.get("envelope_digest")),
            cast(str, invocation.get("response_digest")),
            plan.policy,
            GenerationUsage(
                input_tokens=cast(int | None, invocation_usage.get("input_tokens")),
                output_tokens=cast(int | None, invocation_usage.get("output_tokens")),
            ),
            cast(str, invocation_suggestion.get("suggestion_id")),
            cast(str, invocation_suggestion.get("content_digest")),
        )
    except (TypeError, ValueError):
        raise ValueError("provider invocation report lineage is invalid") from None
    if (
        invocation_model.payload() != invocation
        or invocation.get("runtime") != plan_payload.get("runtime")
        or invocation_request != plan_payload.get("request")
        or invocation.get("invocation_id") != execution_invocation.get("invocation_id")
        or provider_invocation_digest(invocation)
        != execution_invocation.get("content_digest")
    ):
        raise ValueError("provider invocation report lineage is invalid")

    if set(suggestion) != {
        "applies_changes",
        "change_ids",
        "context_pack",
        "debug_session_id",
        "edit_ids",
        "edit_spec_digest",
        "expert_role",
        "next_gate",
        "proposal_id",
        "request",
        "response_digest",
        "schema",
        "source",
        "status",
        "suggestion_id",
        "trusted",
    }:
        raise ValueError("expert suggestion report fields are invalid")
    suggestion_request = _object(suggestion.get("request"), "expert suggestion request")
    suggestion_source = _object(suggestion.get("source"), "expert suggestion source")
    suggestion_context = _object(suggestion.get("context_pack"), "expert suggestion context")
    edits = edit_spec.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("provider edit specification is invalid")
    change_ids = [value.get("change_id") for value in edits if isinstance(value, dict)]
    edit_ids = [value.get("edit_id") for value in edits if isinstance(value, dict)]
    try:
        suggestion_model = ExpertSourceEditReport(
            cast(str, suggestion.get("suggestion_id")),
            cast(str, suggestion_request.get("request_id")),
            cast(str, suggestion_request.get("content_digest")),
            cast(str, suggestion_context.get("pack_id")),
            cast(str, suggestion_context.get("content_digest")),
            cast(str, suggestion.get("proposal_id")),
            cast(str, suggestion.get("debug_session_id")),
            cast(str, suggestion_source.get("path")),
            cast(str, suggestion_source.get("content_digest")),
            cast(str, suggestion.get("response_digest")),
            cast(str, suggestion.get("edit_spec_digest")),
            tuple(cast(list[str], suggestion.get("change_ids"))),
            tuple(cast(list[str], suggestion.get("edit_ids"))),
        )
    except (TypeError, ValueError):
        raise ValueError("expert suggestion report lineage is invalid") from None
    if (
        suggestion_model.payload() != suggestion
        or suggestion_request != plan_payload.get("request")
        or suggestion.get("response_digest") != invocation.get("response_digest")
        or suggestion.get("suggestion_id") != invocation_suggestion.get("suggestion_id")
        or provider_invocation_digest(suggestion)
        != invocation_suggestion.get("content_digest")
        or suggestion.get("edit_spec_digest") != canonical_payload_digest(edit_spec)
        or suggestion.get("change_ids") != change_ids
        or suggestion.get("edit_ids") != edit_ids
    ):
        raise ValueError("expert suggestion report lineage is invalid")
    if not all(isinstance(value, str) for value in (*change_ids, *edit_ids)):
        raise ValueError("expert suggestion edit identities are invalid")
    return {
        "change_ids": cast(list[str], change_ids),
        "debug_session_id": suggestion.get("debug_session_id"),
        "edit_ids": cast(list[str], edit_ids),
        "invocation_id": invocation.get("invocation_id"),
        "proposal_id": suggestion.get("proposal_id"),
        "request_digest": suggestion_request.get("content_digest"),
        "request_id": suggestion_request.get("request_id"),
        "source_digest": suggestion_source.get("content_digest"),
        "source_path": suggestion_source.get("path"),
        "suggestion_id": suggestion.get("suggestion_id"),
    }


def _contained_file(root: Path, candidate: Path, label: str) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    lexical = selected.absolute()
    if not lexical.is_relative_to(root) or ".." in lexical.relative_to(root).parts:
        raise ValueError(f"{label} must be a contained regular file")
    current = root
    for part in lexical.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"{label} must not traverse symlinks")
    resolved = selected.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} must be a contained regular file")
    return resolved


def _read_json(path: Path, label: str) -> dict[str, Any]:
    content = path.read_bytes()
    if not content or len(content) > _MAX_JSON_BYTES:
        raise ValueError(f"{label} size is invalid")
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return payload


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _file_digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
