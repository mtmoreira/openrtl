"""Fail-closed application of approved, evidence-bound source edits."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openrtl.application import (
    RepairApplicationReport,
    RepairApproval,
    SourceEdit,
    SourceEditPlan,
    SourceEditPlanningReport,
    build_source_edit_plan,
    canonical_payload_digest,
)


_MAX_JSON_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 1024 * 1024


def load_source_edit_plan(root: Path, path: Path) -> SourceEditPlan:
    """Load and validate a contained canonical source edit plan without applying it."""

    resolved_root = root.resolve(strict=True)
    plan_file = _contained_file(resolved_root, path, "source edit plan")
    return _parse_edit_plan(_read_json(plan_file, "source edit plan"))


def draft_source_edit_plan(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
    edit_spec_path: Path,
) -> tuple[SourceEditPlan, SourceEditPlanningReport]:
    """Qualify untrusted exact replacements into a typed, review-required plan."""

    resolved_root = root.resolve(strict=True)
    proposal_file = _contained_file(resolved_root, proposal_path, "repair proposal")
    debug_file = _contained_file(resolved_root, debug_session_path, "debug session")
    source_file = _contained_file(resolved_root, source_path, "repair source")
    edit_spec_file = _contained_file(resolved_root, edit_spec_path, "source edit specification")
    proposal = _read_json(proposal_file, "repair proposal")
    debug_session = _read_json(debug_file, "debug session")
    edit_spec = _read_json(edit_spec_file, "source edit specification")
    edit_specs = _parse_edit_specs(edit_spec)
    source = _read_source(source_file)
    proposal_id = proposal.get("proposal_id")
    debug_session_id = debug_session.get("session_id")
    if not isinstance(proposal_id, str) or not isinstance(debug_session_id, str):
        raise ValueError("repair evidence identities are invalid")
    plan = build_source_edit_plan(
        proposal_id=proposal_id,
        debug_session_id=debug_session_id,
        source_path=source_file.relative_to(resolved_root).as_posix(),
        source=source,
        edit_specs=edit_specs,
    )
    change_ids = tuple(dict.fromkeys(value.change_id for value in plan.edits))
    _validate_linkage(
        resolved_root,
        proposal,
        debug_session,
        plan,
        source_file,
        change_ids,
    )
    proposal_digest = _digest(_canonical_json(proposal))
    debug_digest = _digest(_canonical_json(debug_session))
    edit_spec_digest = _digest(edit_spec_file.read_bytes())
    plan_digest = plan.content_digest
    token = hashlib.sha256(
        (
            f"{proposal_digest}:{debug_digest}:{edit_spec_digest}:"
            f"{plan_digest}"
        ).encode()
    ).hexdigest()[:20]
    return plan, SourceEditPlanningReport(
        f"repair.planning.{token}",
        proposal_id,
        proposal_digest,
        debug_session_id,
        debug_digest,
        edit_spec_digest,
        plan.edit_plan_id,
        plan_digest,
        change_ids,
        tuple(value.edit_id for value in plan.edits),
    )


def apply_reviewed_source_edits(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    edit_plan_path: Path,
    output_path: Path,
    approval: RepairApproval,
) -> RepairApplicationReport:
    """Apply an approved typed edit plan to a separate contained candidate."""

    resolved_root = root.resolve(strict=True)
    proposal_file = _contained_file(resolved_root, proposal_path, "repair proposal")
    debug_file = _contained_file(resolved_root, debug_session_path, "debug session")
    plan_file = _contained_file(resolved_root, edit_plan_path, "source edit plan")
    proposal = _read_json(proposal_file, "repair proposal")
    debug_session = _read_json(debug_file, "debug session")
    plan_payload = _read_json(plan_file, "source edit plan")
    plan = _parse_edit_plan(plan_payload)
    plan_digest = canonical_payload_digest(plan_payload)
    if plan_digest != approval.edit_plan_digest:
        raise ValueError("repair approval does not match the source edit plan digest")
    if proposal.get("proposal_id") != approval.proposal_id:
        raise ValueError("repair approval does not match the proposal identity")
    planned = tuple(dict.fromkeys(value.change_id for value in plan.edits))
    if planned != approval.approved_change_ids:
        raise ValueError("source edit plan changes do not match the exact approval")

    source_file = _contained_file(resolved_root, Path(plan.source_path), "repair source")
    output_file = _contained_output(resolved_root, output_path)
    if output_file == source_file:
        raise ValueError("repair output must be separate from its source")
    _validate_linkage(
        resolved_root,
        proposal,
        debug_session,
        plan,
        source_file,
        approval.approved_change_ids,
    )

    source = _read_source(source_file)
    if _digest(source) != plan.source_digest:
        raise ValueError("source edit plan no longer matches its source")
    repaired = bytearray(source)
    changed_lines: list[int] = []
    for edit in reversed(plan.edits):
        expected = edit.expected_before.encode("utf-8")
        if bytes(repaired[edit.start_byte : edit.end_byte]) != expected:
            raise ValueError("source edit expected bytes no longer match")
        changed_lines.append(source[: edit.start_byte].count(b"\n") + 1)
        repaired[edit.start_byte : edit.end_byte] = edit.replacement.encode("utf-8")
    repaired_bytes = bytes(repaired)
    repaired_bytes.decode("utf-8")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        if output_file.is_symlink() or not output_file.is_file():
            raise ValueError("repair output has an unsafe existing type")
        if output_file.read_bytes() != repaired_bytes:
            raise ValueError("repair output already contains unrecognized content")
    else:
        output_file.write_bytes(repaired_bytes)

    before_digest = _digest(source)
    after_digest = _digest(repaired_bytes)
    application_token = hashlib.sha256(
        (
            f"{approval.proposal_id}:{','.join(approval.approved_change_ids)}:"
            f"{approval.edit_plan_digest}:{approval.review_note}:"
            f"{before_digest}:{after_digest}"
        ).encode()
    ).hexdigest()[:20]
    return RepairApplicationReport(
        f"repair.application.{application_token}",
        approval.proposal_id,
        str(proposal["debug_session_id"]),
        plan.edit_plan_id,
        plan_digest,
        approval.approved_change_ids,
        tuple(value.edit_id for value in plan.edits),
        source_file.relative_to(resolved_root).as_posix(),
        output_file.relative_to(resolved_root).as_posix(),
        before_digest,
        after_digest,
        tuple(sorted(set(changed_lines))),
        approval.review_note,
    )


def _validate_linkage(
    root: Path,
    proposal: dict[str, Any],
    debug_session: dict[str, Any],
    plan: SourceEditPlan,
    source: Path,
    change_ids: tuple[str, ...],
) -> None:
    if (
        proposal.get("schema") != "openrtl.repair-proposal.v1"
        or proposal.get("status") != "proposed"
        or proposal.get("applies_changes") is not False
    ):
        raise ValueError("repair proposal is not a non-applying v1 proposal")
    if plan.proposal_id != proposal.get("proposal_id"):
        raise ValueError("source edit plan does not match the proposal identity")
    if debug_session.get("schema") != "openrtl.debug-session.v1":
        raise ValueError("repair debug session schema is invalid")
    if debug_session.get("passed") is not False:
        raise ValueError("repair planning and application require a failing debug session")
    if proposal.get("debug_session_id") != debug_session.get("session_id"):
        raise ValueError("repair proposal and debug session identities differ")
    if plan.debug_session_id != debug_session.get("session_id"):
        raise ValueError("source edit plan and debug session identities differ")

    context = proposal.get("context_item")
    if not isinstance(context, dict):
        raise ValueError("repair proposal context is invalid")
    if context.get("item_id") != debug_session.get("session_id"):
        raise ValueError("repair proposal context identity differs from the debug session")
    if context.get("content_digest") != _digest(_canonical_json(debug_session)):
        raise ValueError("repair proposal context digest differs from the debug session")

    changes = proposal.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("repair proposal requires changes")
    indexed_changes: dict[str, dict[str, Any]] = {}
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("change_id"), str):
            raise ValueError("repair proposal change is invalid")
        change_id = str(change["change_id"])
        if change_id in indexed_changes:
            raise ValueError("repair proposal change identities must be unique")
        indexed_changes[change_id] = change
    planned = tuple(dict.fromkeys(value.change_id for value in plan.edits))
    if planned != change_ids:
        raise ValueError("source edit plan changes differ from selected changes")
    if any(value not in indexed_changes for value in change_ids):
        raise ValueError("source edit plan references an absent proposal change")

    findings = debug_session.get("findings")
    debug_anchors = debug_session.get("source_anchors")
    if not isinstance(findings, list) or not isinstance(debug_anchors, list):
        raise ValueError("repair debug session evidence is invalid")
    indexed_findings = {
        value.get("finding_id"): value
        for value in findings
        if isinstance(value, dict) and isinstance(value.get("finding_id"), str)
    }
    source_relative = source.relative_to(root).as_posix()
    source_bytes = _read_source(source)
    source_digest = _digest(source_bytes)
    line_ranges = _line_byte_ranges(source_bytes)
    anchor_ranges: dict[str, list[tuple[int, int]]] = {}
    for change_id in change_ids:
        change = indexed_changes[change_id]
        if change.get("artifact_kind") != "rtl":
            raise ValueError("source edit plan must target RTL changes")
        finding_ids = change.get("finding_ids")
        requirement_ids = change.get("requirement_ids")
        if not isinstance(finding_ids, list) or not isinstance(requirement_ids, list):
            raise ValueError("repair proposal finding linkage is invalid")
        linked_requirements = {
            indexed_findings[value].get("requirement_id")
            for value in finding_ids
            if value in indexed_findings
        }
        if len(linked_requirements) != len(set(requirement_ids)) or linked_requirements != set(
            requirement_ids
        ):
            raise ValueError("repair proposal requirements differ from linked findings")
        anchors = change.get("source_anchors")
        if not isinstance(anchors, list) or not anchors:
            raise ValueError("repair proposal lacks source anchors")
        selected_ranges: list[tuple[int, int]] = []
        for anchor in anchors:
            if not isinstance(anchor, dict):
                raise ValueError("repair proposal source anchor is invalid")
            if anchor.get("path") != source_relative or anchor.get("content_digest") != source_digest:
                raise ValueError("repair source anchor no longer matches its source")
            if anchor not in debug_anchors:
                raise ValueError("repair source anchor is absent from the debug session")
            line_start = anchor.get("line_start")
            line_end = anchor.get("line_end")
            if (
                not isinstance(line_start, int)
                or isinstance(line_start, bool)
                or not isinstance(line_end, int)
                or isinstance(line_end, bool)
                or line_start < 1
                or line_end < line_start
                or line_end > len(line_ranges)
            ):
                raise ValueError("repair source anchor line range is invalid")
            selected_ranges.append((line_ranges[line_start - 1][0], line_ranges[line_end - 1][1]))
        anchor_ranges[change_id] = selected_ranges

    for edit in plan.edits:
        if not any(
            start <= edit.start_byte and edit.end_byte <= end
            for start, end in anchor_ranges[edit.change_id]
        ):
            raise ValueError("source edit byte range is outside its reviewed source anchors")


def _parse_edit_specs(payload: dict[str, Any]) -> tuple[dict[str, str], ...]:
    if set(payload) != {"edits", "schema"}:
        raise ValueError("source edit specification fields are invalid")
    if payload.get("schema") != "openrtl.source-edit-spec.v1":
        raise ValueError("source edit specification schema is invalid")
    edits = payload.get("edits")
    expected_fields = {
        "change_id",
        "edit_id",
        "expected_before",
        "operation",
        "replacement",
    }
    if (
        not isinstance(edits, list)
        or not edits
        or any(not isinstance(value, dict) or set(value) != expected_fields for value in edits)
        or any(not all(isinstance(item, str) for item in value.values()) for value in edits)
    ):
        raise ValueError("source edit specification edits are invalid")
    return tuple(
        {str(key): str(item) for key, item in value.items()}
        for value in edits
    )


def _parse_edit_plan(payload: dict[str, Any]) -> SourceEditPlan:
    if set(payload) != {
        "applies_changes",
        "debug_session_id",
        "edit_plan_id",
        "edits",
        "proposal_id",
        "schema",
        "source",
        "status",
    }:
        raise ValueError("source edit plan fields are invalid")
    if (
        payload.get("schema") != "openrtl.source-edit-plan.v1"
        or payload.get("status") != "proposed"
        or payload.get("applies_changes") is not False
    ):
        raise ValueError("source edit plan is not a non-applying v1 plan")
    source = payload.get("source")
    edits = payload.get("edits")
    if not isinstance(source, dict) or set(source) != {"content_digest", "path"}:
        raise ValueError("source edit plan source is invalid")
    if not isinstance(edits, list) or not edits:
        raise ValueError("source edit plan edits are invalid")
    parsed_edits: list[SourceEdit] = []
    edit_fields = {
        "change_id",
        "edit_id",
        "end_byte",
        "expected_before",
        "expected_before_digest",
        "operation",
        "replacement",
        "replacement_digest",
        "start_byte",
    }
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != edit_fields:
            raise ValueError("source edit fields are invalid")
        try:
            parsed_edits.append(
                SourceEdit(
                    edit["edit_id"],
                    edit["change_id"],
                    edit["operation"],
                    edit["start_byte"],
                    edit["end_byte"],
                    edit["expected_before"],
                    edit["expected_before_digest"],
                    edit["replacement"],
                    edit["replacement_digest"],
                )
            )
        except (KeyError, TypeError) as error:
            raise ValueError("source edit values are invalid") from error
    try:
        plan = SourceEditPlan(
            payload["edit_plan_id"],
            payload["proposal_id"],
            payload["debug_session_id"],
            source["path"],
            source["content_digest"],
            tuple(parsed_edits),
        )
    except (KeyError, TypeError) as error:
        raise ValueError("source edit plan values are invalid") from error
    if plan.payload() != payload:
        raise ValueError("source edit plan is not in canonical form")
    return plan


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


def _contained_output(root: Path, candidate: Path) -> Path:
    selected = candidate if candidate.is_absolute() else root / candidate
    lexical = selected.absolute()
    if not lexical.is_relative_to(root) or ".." in lexical.relative_to(root).parts:
        raise ValueError("repair output must be contained by its root")
    current = root
    for part in lexical.relative_to(root).parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise ValueError("repair output must not traverse symlinks")
    return lexical


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


def _line_byte_ranges(content: bytes) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for line in content.splitlines(keepends=True):
        ranges.append((cursor, cursor + len(line)))
        cursor += len(line)
    if cursor < len(content):
        ranges.append((cursor, len(content)))
    return tuple(ranges)


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
