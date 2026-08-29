"""Fail-closed ingestion of provider-neutral expert source-edit output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openrtl.application.expert_edits import (
    ExpertSourceEditReport,
    ExpertSourceEditRequest,
    build_expert_source_edit_report,
    build_expert_source_edit_request,
    context_pack_payload,
)
from openrtl.application.repair_execution import canonical_payload_digest
from openrtl.domain import (
    ContextItem,
    ContextPack,
    ContextPackBuilder,
    ContextRequest,
    ExpertRole,
    ProjectKnowledgeBase,
)


_MAX_JSON_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 1024 * 1024


def prepare_expert_source_edit_request(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
) -> ExpertSourceEditRequest:
    """Build a digest-bound expert request without invoking a provider."""

    resolved_root = root.resolve(strict=True)
    proposal_file = _contained_file(resolved_root, proposal_path, "repair proposal")
    debug_file = _contained_file(resolved_root, debug_session_path, "debug session")
    source_file = _contained_file(resolved_root, source_path, "repair source")
    proposal = _read_json(proposal_file, "repair proposal")
    debug_session = _read_json(debug_file, "debug session")
    source = _read_source(source_file)
    proposal_digest = _digest(_canonical_json(proposal))
    debug_digest = _digest(_canonical_json(debug_session))
    source_digest = _digest(source)
    source_relative = source_file.relative_to(resolved_root).as_posix()
    proposal_id, debug_session_id, change_ids = _validate_request_evidence(
        proposal,
        debug_session,
        source_relative,
        source_digest,
        debug_digest,
    )
    context = ContextPackBuilder(ProjectKnowledgeBase()).build(
        ContextRequest(
            role=ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER,
            objective="Propose exact, reviewable source edits for the linked repair changes.",
            artifact_kinds=(),
            attached_items=(
                ContextItem(
                    proposal_id,
                    "repair.proposal",
                    proposal_file.relative_to(resolved_root).as_posix(),
                    proposal_digest,
                    "Non-applying repair proposal linked to a failed debug session.",
                ),
                ContextItem(
                    debug_session_id,
                    "debug.session",
                    debug_file.relative_to(resolved_root).as_posix(),
                    debug_digest,
                    "Failed debug session with waveform and source anchors.",
                ),
                ContextItem(
                    f"source.{source_digest[7:27]}",
                    "source.rtl",
                    source_relative,
                    source_digest,
                    "Digest-pinned RTL source constrained by proposal anchors.",
                ),
            ),
        )
    )
    return build_expert_source_edit_request(
        context_pack=context,
        proposal_id=proposal_id,
        proposal_digest=proposal_digest,
        debug_session_id=debug_session_id,
        debug_session_digest=debug_digest,
        source_path=source_relative,
        source_digest=source_digest,
        change_ids=change_ids,
    )


def accept_expert_source_edit_output(
    root: Path,
    *,
    request_path: Path,
    response_path: Path,
) -> tuple[dict[str, Any], ExpertSourceEditReport]:
    """Accept strict expert output only as an untrusted specification candidate."""

    resolved_root = root.resolve(strict=True)
    request_file = _contained_file(resolved_root, request_path, "expert source edit request")
    response_file = _contained_file(resolved_root, response_path, "expert source edit response")
    request = _parse_request(_read_json(request_file, "expert source edit request"))
    response_bytes = response_file.read_bytes()
    response = _read_json(response_file, "expert source edit response")
    edits = _validate_response(request, response)
    edit_spec: dict[str, Any] = {
        "edits": edits,
        "schema": "openrtl.source-edit-spec.v1",
    }
    edit_spec_digest = canonical_payload_digest(edit_spec)
    change_ids = tuple(dict.fromkeys(str(value["change_id"]) for value in edits))
    edit_ids = tuple(str(value["edit_id"]) for value in edits)
    report = build_expert_source_edit_report(
        request,
        response_digest=_digest(response_bytes),
        edit_spec_digest=edit_spec_digest,
        change_ids=change_ids,
        edit_ids=edit_ids,
    )
    return edit_spec, report


def _validate_request_evidence(
    proposal: dict[str, Any],
    debug_session: dict[str, Any],
    source_path: str,
    source_digest: str,
    debug_digest: str,
) -> tuple[str, str, tuple[str, ...]]:
    if (
        proposal.get("schema") != "openrtl.repair-proposal.v1"
        or proposal.get("status") != "proposed"
        or proposal.get("applies_changes") is not False
    ):
        raise ValueError("expert request requires a non-applying v1 repair proposal")
    if debug_session.get("schema") != "openrtl.debug-session.v1" or debug_session.get("passed") is not False:
        raise ValueError("expert request requires a failing v1 debug session")
    proposal_id = proposal.get("proposal_id")
    debug_session_id = debug_session.get("session_id")
    if not isinstance(proposal_id, str) or not isinstance(debug_session_id, str):
        raise ValueError("expert request evidence identities are invalid")
    if proposal.get("debug_session_id") != debug_session_id:
        raise ValueError("expert request proposal and debug session identities differ")
    context = proposal.get("context_item")
    if not isinstance(context, dict) or context.get("item_id") != debug_session_id or context.get("content_digest") != debug_digest:
        raise ValueError("expert request proposal context differs from the debug session")
    changes = proposal.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("expert request proposal requires changes")
    change_ids: list[str] = []
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("change_id"), str):
            raise ValueError("expert request proposal change is invalid")
        if change.get("artifact_kind") != "rtl":
            raise ValueError("expert source edits require RTL repair changes")
        anchors = change.get("source_anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError("expert request repair change lacks source anchors")
        if any(
            not isinstance(anchor, dict)
            or anchor.get("path") != source_path
            or anchor.get("content_digest") != source_digest
            for anchor in anchors
        ):
            raise ValueError("expert request source differs from proposal anchors")
        change_ids.append(str(change["change_id"]))
    if len(set(change_ids)) != len(change_ids):
        raise ValueError("expert request repair change identities must be unique")
    return proposal_id, debug_session_id, tuple(change_ids)


def _validate_response(
    request: ExpertSourceEditRequest,
    response: dict[str, Any],
) -> list[dict[str, str]]:
    expected_fields = {
        "applies_changes",
        "change_ids",
        "context_pack_digest",
        "context_pack_id",
        "debug_session_id",
        "edits",
        "expert_role",
        "proposal_id",
        "request_digest",
        "request_id",
        "schema",
        "source",
        "status",
    }
    if set(response) != expected_fields:
        raise ValueError("expert source edit response fields are invalid")
    expected_bindings = {
        "applies_changes": False,
        "change_ids": list(request.change_ids),
        "context_pack_digest": request.context_pack_digest,
        "context_pack_id": request.context_pack.pack_id,
        "debug_session_id": request.debug_session_id,
        "expert_role": ExpertRole.DIAGNOSIS_CLOSURE_ENGINEER.value,
        "proposal_id": request.proposal_id,
        "request_digest": request.content_digest,
        "request_id": request.request_id,
        "schema": "openrtl.expert-source-edit-output.v1",
        "source": {
            "content_digest": request.source_digest,
            "path": request.source_path,
        },
        "status": "proposed_untrusted",
    }
    for field_name, expected in expected_bindings.items():
        if response.get(field_name) != expected:
            raise ValueError(f"expert source edit response {field_name} binding is invalid")
    edits = response.get("edits")
    edit_fields = {"change_id", "edit_id", "expected_before", "operation", "replacement"}
    if not isinstance(edits, list) or not edits:
        raise ValueError("expert source edit response requires edits")
    parsed: list[dict[str, str]] = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != edit_fields or not all(isinstance(value, str) for value in edit.values()):
            raise ValueError("expert source edit response edit is invalid")
        if edit["operation"] != "replace_exact_bytes":
            raise ValueError("expert source edit operation is not allowlisted")
        if not edit["expected_before"] or edit["expected_before"] == edit["replacement"]:
            raise ValueError("expert source edit must replace distinct non-empty bytes")
        if edit["change_id"] not in request.change_ids:
            raise ValueError("expert source edit references an absent requested change")
        parsed.append({str(key): str(value) for key, value in edit.items()})
    edit_ids = [value["edit_id"] for value in parsed]
    if len(set(edit_ids)) != len(edit_ids):
        raise ValueError("expert source edit identities must be unique")
    if tuple(dict.fromkeys(value["change_id"] for value in parsed)) != request.change_ids:
        raise ValueError("expert source edits must cover every requested change in order")
    return parsed


def _parse_request(payload: dict[str, Any]) -> ExpertSourceEditRequest:
    expected_fields = {
        "applies_changes",
        "change_ids",
        "context_pack",
        "debug_session",
        "expert_role",
        "output_contract",
        "proposal",
        "request_id",
        "schema",
        "source",
        "status",
    }
    if set(payload) != expected_fields or payload.get("schema") != "openrtl.expert-source-edit-request.v1" or payload.get("status") != "awaiting_expert_output" or payload.get("applies_changes") is not False:
        raise ValueError("expert source edit request is not canonical v1")
    context_binding = payload.get("context_pack")
    proposal = payload.get("proposal")
    debug_session = payload.get("debug_session")
    source = payload.get("source")
    if not all(isinstance(value, dict) for value in (context_binding, proposal, debug_session, source)):
        raise ValueError("expert source edit request bindings are invalid")
    assert isinstance(context_binding, dict)
    assert isinstance(proposal, dict)
    assert isinstance(debug_session, dict)
    assert isinstance(source, dict)
    context_payload = context_binding.get("payload")
    if not isinstance(context_payload, dict) or context_binding.get("content_digest") != _digest(_canonical_json(context_payload)):
        raise ValueError("expert source edit request context digest is invalid")
    context = _parse_context_pack(context_payload)
    try:
        request = ExpertSourceEditRequest(
            payload["request_id"],
            context,
            proposal["proposal_id"],
            proposal["content_digest"],
            debug_session["session_id"],
            debug_session["content_digest"],
            source["path"],
            source["content_digest"],
            tuple(payload["change_ids"]),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("expert source edit request values are invalid") from error
    rebuilt = build_expert_source_edit_request(
        context_pack=context,
        proposal_id=request.proposal_id,
        proposal_digest=request.proposal_digest,
        debug_session_id=request.debug_session_id,
        debug_session_digest=request.debug_session_digest,
        source_path=request.source_path,
        source_digest=request.source_digest,
        change_ids=request.change_ids,
    )
    if rebuilt.request_id != request.request_id:
        raise ValueError("expert source edit request identity is invalid")
    if request.payload() != payload:
        raise ValueError("expert source edit request is not in canonical form")
    return request


def _parse_context_pack(payload: dict[str, Any]) -> ContextPack:
    if set(payload) != {"attempt", "items", "objective", "pack_id", "role"}:
        raise ValueError("expert source edit context fields are invalid")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("expert source edit context items are invalid")
    parsed_items: list[ContextItem] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"content_digest", "item_id", "item_type", "summary", "uri"}:
            raise ValueError("expert source edit context item is invalid")
        try:
            parsed_items.append(ContextItem(item["item_id"], item["item_type"], item["uri"], item["content_digest"], item["summary"]))
        except (KeyError, TypeError) as error:
            raise ValueError("expert source edit context item values are invalid") from error
    try:
        context = ContextPack(payload["pack_id"], ExpertRole(payload["role"]), payload["objective"], payload["attempt"], tuple(parsed_items))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("expert source edit context values are invalid") from error
    rebuilt = ContextPackBuilder(ProjectKnowledgeBase()).build(
        ContextRequest(
            role=context.role,
            objective=context.objective,
            artifact_kinds=(),
            attempt=context.attempt,
            attached_items=context.items,
        )
    )
    if rebuilt.pack_id != context.pack_id:
        raise ValueError("expert source edit context identity is invalid")
    if context_pack_payload(context) != payload:
        raise ValueError("expert source edit context is not canonical")
    return context


def _contained_file(root: Path, candidate: Path, label: str) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    lexical = selected.absolute()
    try:
        relative = lexical.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} must be a contained regular file") from error
    if ".." in relative.parts:
        raise ValueError(f"{label} must be a contained regular file")
    current = root
    for part in relative.parts:
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


def _read_source(path: Path) -> bytes:
    content = path.read_bytes()
    if not content or len(content) > _MAX_SOURCE_BYTES:
        raise ValueError("repair source size is invalid")
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("repair source is not UTF-8") from error
    return content


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
