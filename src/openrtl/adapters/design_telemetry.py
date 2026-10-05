"""Bind AgentRig's generic observation ports to explicit private project capture."""

from __future__ import annotations

from typing import Any

from openrtl.application.design_agent import DesignTraceRecorder


def private_capture(store: DesignTraceRecorder | None, operation_id: str) -> Any:
    if store is None or not store.status().get("enabled", False):
        return None
    from agentrig.core import PrivateTraceCapture

    class Recorder:
        def record(self, record: Any) -> None:
            # Select the public envelope only: no SDK objects or authentication.
            store.record(operation_id, "provider_trace", {
                "kind": record.kind, "record_id": str(record.record_id),
                "occurred_at": str(record.occurred_at), "run_id": str(record.run_id),
                "parent_run_id": str(record.parent_run_id) if record.parent_run_id else None,
                "correlation": dict(record.correlation),
                "content": record.content, "content_bytes": record.content_bytes,
                "truncated": record.truncated, "metadata": dict(record.metadata),
            })

    return PrivateTraceCapture(recorder=Recorder(), max_content_bytes=262144,
                               include_reasoning=True, include_process=True)


def event_sink(store: DesignTraceRecorder | None, operation_id: str) -> Any:
    from agentrig.core.observability import NOOP_EVENT_SINK
    if store is None or not store.status().get("enabled", False):
        return NOOP_EVENT_SINK

    class Sink:
        def emit(self, event: Any) -> None:
            store.record(operation_id, "runtime_event", event.to_data())

    return Sink()
