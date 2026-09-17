"""Shared bounded event format for persistence, portable restore and diagnostics."""
from __future__ import annotations

from openrtl.domain.design_session import JsonObject, object_value, require, text

EVENTS = frozenset(("spec.proposed spec.approved spec.revised operation.started operation.completed "
    "operation.received operation.failed simulation.completed simulation.failed review.completed "
    "project.accepted detail.changed session.upgraded limits.bound delegation.granted batch.step_started "
    "operation.abandoned warning.reviewed imports.recorded baseline.approved change.approved stage.reused "
    "pace.changed change.proposed analysis.recorded session.restored workspace.requested "
    "workspace.active workspace.completed workspace.failed workspace.cancelled workspace.cancel_requested "
    "workspace.reconciliation_needed").split())
FIELDS = frozenset(("operation_id role context_digest output_digest error_code input_tokens output_tokens "
    "elapsed_ms artifact_count spec_digest provider model evidence_kind run_id authority warning_id "
    "delegation_digest requirements_digest artifacts_digest client_operation_id "
    "request_digest").split())


def event(value: object, sequence: int) -> JsonObject:
    row = object_value(value, {"schema", "sequence", "timestamp_ns", "event", "fields"})
    require(row["schema"] == "openrtl.design-event.v1" and type(row["sequence"]) is int and row["sequence"] == sequence,
            "portable_event_sequence_invalid")
    require(type(row["timestamp_ns"]) is int and 0 <= row["timestamp_ns"] < 2**63 and row["event"] in EVENTS,
            "portable_event_invalid")
    fields = row["fields"]
    require(isinstance(fields, dict) and set(fields) <= FIELDS, "portable_event_fields_invalid")
    for value in fields.values():
        require(type(value) in (str, int, bool) and len(str(value)) <= 256, "portable_event_field_invalid")
        if isinstance(value, str): text(value, maximum=256)
    return row
