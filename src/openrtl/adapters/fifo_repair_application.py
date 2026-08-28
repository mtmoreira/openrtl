"""Fail-closed application of the reviewed FIFO level repair to a candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openrtl.application import RepairApplicationReport, RepairApproval


_MAX_JSON_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 1024 * 1024
_FAULTY_STATEMENT = "        2'b10: count <= count;"
_REPAIRED_STATEMENT = "        2'b10: count <= count + 1'b1;"


def apply_reviewed_fifo_level_repair(
    root: Path,
    *,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
    output_path: Path,
    approval: RepairApproval,
) -> RepairApplicationReport:
    """Apply one exact approved repair to a separate, contained RTL candidate."""

    resolved_root = root.resolve(strict=True)
    proposal_file = _contained_file(resolved_root, proposal_path, "repair proposal")
    debug_file = _contained_file(resolved_root, debug_session_path, "debug session")
    source_file = _contained_file(resolved_root, source_path, "repair source")
    output_file = _contained_output(resolved_root, output_path)
    if output_file == source_file:
        raise ValueError("repair output must be separate from its source")

    proposal = _read_json(proposal_file, "repair proposal")
    debug_session = _read_json(debug_file, "debug session")
    _validate_linkage(
        resolved_root,
        proposal,
        debug_session,
        source_file,
        approval,
    )

    source = _read_source(source_file)
    if source.count(_FAULTY_STATEMENT) != 1:
        raise ValueError("FIFO candidate does not contain the exact reviewed fault statement")
    repaired = source.replace(_FAULTY_STATEMENT, _REPAIRED_STATEMENT, 1)
    changed_line = source[: source.index(_FAULTY_STATEMENT)].count("\n") + 1
    before_digest = _digest(source.encode())
    after_digest = _digest(repaired.encode())

    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        if output_file.is_symlink() or not output_file.is_file():
            raise ValueError("repair output has an unsafe existing type")
        existing = output_file.read_text(encoding="utf-8")
        if existing != repaired:
            raise ValueError("repair output already contains unrecognized content")
    else:
        output_file.write_text(repaired, encoding="utf-8")

    application_token = hashlib.sha256(
        (
            f"{approval.proposal_id}:{','.join(approval.approved_change_ids)}:"
            f"{approval.review_note}:{before_digest}:{after_digest}"
        ).encode()
    ).hexdigest()[:20]
    return RepairApplicationReport(
        f"repair.application.{application_token}",
        approval.proposal_id,
        str(proposal["debug_session_id"]),
        approval.approved_change_ids,
        source_file.relative_to(resolved_root).as_posix(),
        output_file.relative_to(resolved_root).as_posix(),
        before_digest,
        after_digest,
        (changed_line,),
        approval.review_note,
    )


def _validate_linkage(
    root: Path,
    proposal: dict[str, Any],
    debug_session: dict[str, Any],
    source: Path,
    approval: RepairApproval,
) -> None:
    if (
        proposal.get("schema") != "openrtl.repair-proposal.v1"
        or proposal.get("status") != "proposed"
        or proposal.get("applies_changes") is not False
    ):
        raise ValueError("repair proposal is not a non-applying v1 proposal")
    if proposal.get("proposal_id") != approval.proposal_id:
        raise ValueError("repair approval does not match the proposal identity")
    if debug_session.get("schema") != "openrtl.debug-session.v1":
        raise ValueError("repair debug session schema is invalid")
    if debug_session.get("passed") is not False:
        raise ValueError("repair application requires a failing debug session")
    if proposal.get("debug_session_id") != debug_session.get("session_id"):
        raise ValueError("repair proposal and debug session identities differ")

    context = proposal.get("context_item")
    if not isinstance(context, dict):
        raise ValueError("repair proposal context is invalid")
    if context.get("item_id") != debug_session.get("session_id"):
        raise ValueError("repair proposal context identity differs from the debug session")
    expected_context_digest = _digest(_canonical_json(debug_session))
    if context.get("content_digest") != expected_context_digest:
        raise ValueError("repair proposal context digest differs from the debug session")

    changes = proposal.get("changes")
    if not isinstance(changes, list) or len(changes) != 1:
        raise ValueError("FIFO level repair requires exactly one proposed change")
    change = changes[0]
    if not isinstance(change, dict) or change.get("change_id") != "repair.change.level":
        raise ValueError("FIFO level repair proposal has an unsupported change")
    if tuple(approval.approved_change_ids) != ("repair.change.level",):
        raise ValueError("FIFO level repair approval must select the exact supported change")
    if change.get("artifact_kind") != "rtl":
        raise ValueError("FIFO level repair must target RTL")
    findings = debug_session.get("findings")
    if not isinstance(findings, list) or len(findings) != 1:
        raise ValueError("FIFO level repair requires exactly one debug finding")
    finding = findings[0]
    if not isinstance(finding, dict):
        raise ValueError("FIFO level repair debug finding is invalid")
    finding_ids = change.get("finding_ids")
    requirement_ids = change.get("requirement_ids")
    if (
        not isinstance(finding_ids, list)
        or not isinstance(requirement_ids, list)
        or tuple(finding_ids) != (finding.get("finding_id"),)
        or tuple(requirement_ids) != ("fifo.write",)
        or finding.get("requirement_id") != "fifo.write"
        or ".level." not in str(finding.get("finding_id", ""))
    ):
        raise ValueError("FIFO level repair does not match the linked level finding")

    source_relative = source.relative_to(root).as_posix()
    source_digest = _digest(source.read_bytes())
    anchors = change.get("source_anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError("FIFO level repair lacks source anchors")
    debug_anchors = debug_session.get("source_anchors")
    if not isinstance(debug_anchors, list):
        raise ValueError("FIFO level repair debug source anchors are invalid")
    source_lines = source.read_text(encoding="utf-8").splitlines()
    anchored_text: list[str] = []
    for anchor in anchors:
        if not isinstance(anchor, dict):
            raise ValueError("FIFO level repair source anchor is invalid")
        if anchor.get("path") != source_relative or anchor.get("content_digest") != source_digest:
            raise ValueError("FIFO level repair source anchor no longer matches its source")
        if anchor not in debug_anchors:
            raise ValueError("FIFO level repair source anchor is absent from the debug session")
        line_start = anchor.get("line_start")
        line_end = anchor.get("line_end")
        if (
            not isinstance(line_start, int)
            or isinstance(line_start, bool)
            or not isinstance(line_end, int)
            or isinstance(line_end, bool)
            or line_start < 1
            or line_end < line_start
            or line_end > len(source_lines)
        ):
            raise ValueError("FIFO level repair source anchor line range is invalid")
        anchored_text.extend(source_lines[line_start - 1 : line_end])
    if not any("always_ff @(posedge clk)" in line for line in anchored_text) or not any(
        "unique case" in line for line in anchored_text
    ):
        raise ValueError("FIFO level repair lacks the reviewed sequential anchors")


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


def _read_source(path: Path) -> str:
    content = path.read_bytes()
    if not content or len(content) > _MAX_SOURCE_BYTES:
        raise ValueError("repair source size is invalid")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("repair source is not UTF-8") from error


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _digest(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"
