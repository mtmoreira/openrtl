"""Allowlisted progress/support metadata; never raw exceptions or narrative text."""
from __future__ import annotations

import asyncio
import re
import time
from typing import Awaitable, Callable, TypeVar

from openrtl.domain.design_session import JsonObject, ROLES, content_digest

T = TypeVar("T")
HINTS = {
    "session_writer_already_active": "Close the other writer; read-only diagnostics remain available.",
    "session_restore_incomplete": "Preserve this incomplete directory. Retry into a new destination.",
    "export_requires_reconciled_session": "Inspect the original session and explicitly reconcile its recorded operation before exporting.",
    "interrupted_operation_requires_reconciliation": "Inspect diagnostics, then recover the exact recorded operation; do not replay it.",
    "explicit_runtime_reconciliation_required": "Select and authorize the original owned runtime before recovery; do not guess a container.",
    "explicit_session_upgrade_required": "Back up the existing project, then request an explicit session upgrade.",
    "expert_call_budget_exhausted": "The persisted call budget is exhausted; resume does not refund calls.",
    "expert_invocation_failed": "Inspect recorded call accounting and configuration before explicitly retrying.",
    "expert_output_invalid": "The response failed deterministic validation; inspect the current stage before retrying.",
    "simulation_execution_failed": "Inspect the retained operation and explicitly reconcile its original owned runtime before retrying.",
    "interrupted_operation_abandoned": "Review the retained uncertainty warning; external completion and cost may remain unknown.",
    "portable_file_changed": "The backup bytes differ from its manifest. Preserve it and select a verified backup.",
    "portable_evidence_incomplete": "Retain and locate the complete original run evidence before backing up this session.",
    "portable_history_bound": "This session exceeds the portable history limit; preserve the original project.",
    "export_review_stale": "Preview the current bytes and state again before approval.",
    "shown_review_stale": "Display the current review again before approving.",
    "show_current_review_before_approval": "Display the review in this conversation before approving.",
    "export_destination_must_be_new": "Choose an absent destination whose parent already exists.",
    "project_unavailable": "Select an existing OpenRTL project directory.",
    "session_database_missing": "Select a directory containing an OpenRTL session database.",
    "artifact_bytes_changed": "Preserve the project and recover the original digest-bound artifacts.",
    "portable_runtime_bound_evidence_required": "This legacy run cannot be portably reverified; retain its original project.",
}


def diagnostic(error: object) -> JsonObject:
    code = str(error) if type(error) is ValueError else "local_operation_failed"
    if code not in HINTS: code = "local_operation_failed"
    return {"code": code, "action": HINTS.get(code, "Check the explicit project, reviewed files and configuration. Raw details are withheld.")}


def safe_event(row: JsonObject) -> JsonObject:
    from openrtl.domain.design_events import EVENTS
    fields: JsonObject = {}
    for key, value in row["fields"].items():
        if key in ("input_tokens", "output_tokens", "elapsed_ms", "artifact_count") and type(value) is int and 0 <= value < 2**63:
            fields[key] = value
        elif key.endswith("_digest") and isinstance(value, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", value):
            fields[key] = value
        elif key in ("operation_id", "run_id") and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value):
            fields[key] = value
        elif key == "role" and value in ROLES.values(): fields[key] = value
        elif key == "error_code" and value in HINTS: fields[key] = value
    return {"sequence": row["sequence"], "event": row["event"] if row["event"] in EVENTS else "unrecognized", "fields": fields}


async def observed(work: Awaitable[T], state: JsonObject, emit: Callable[[JsonObject], None] | None,
                   *, interval: float = 5.0) -> T:
    """Observe only this invocation's work. Failed observers cannot affect its result."""
    start = time.monotonic()
    active = state["active"]
    assert active is not None
    base = {"schema": "openrtl.design-progress.v1", "operation_id": active["id"], "stage": active["stage"],
            "calls": state["calls"], "spec_digest": content_digest(state["spec"]),
            "artifacts_digest": content_digest(state["files"])}
    def show(phase: str) -> None:
        if emit is not None:
            try: emit({**base, "phase": phase, "elapsed_ms": int((time.monotonic() - start) * 1000)})
            except Exception: pass
    task = asyncio.ensure_future(work)
    try:
        show("started")
        notices = 0
        while not task.done():
            await asyncio.wait({task}, timeout=interval)
            if not task.done() and notices < 120:
                show("waiting"); notices += 1
        result = await task
        show("returned_for_validation")
        return result
    finally:
        if not task.done():
            task.cancel()
            try: await task
            except BaseException: pass
