"""Transport-neutral project operations for a CLI or a local browser."""

from __future__ import annotations

import asyncio
import copy
from typing import Callable
import uuid

from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.application.design_conversation import ShownReview, approve_shown, review_payload
from openrtl.domain.design_session import JsonObject, content_digest, require, text


class DesignWorkspace:
    """One-writer session controller; callers run it on one event-loop thread.

    Submitted tasks belong to this service, not to an HTTP connection. The saved
    request digest and phase prevent retrying a provider turn after a refresh or
    process restart. Reply prose remains in memory only for this process.
    """

    def __init__(self, agent: DesignAgent) -> None:
        self.agent = agent
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._replies: dict[str, str] = {}

    def reconcile_interrupted(self) -> None:
        """Record truthful outcomes for operations left by a previous process."""
        state = self.agent.store.read()
        for identifier, row in tuple(state["workspace_operations"].items()):
            if row["phase"] == "queued":
                self._finish(identifier, "cancelled", None)
            elif row["phase"] in ("active", "cancellation_requested"):
                self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")

    def snapshot(self, cursor: int = 0) -> JsonObject:
        state = self.agent.store.read()
        events = self.agent.store.events_after(cursor)
        return {"schema": "openrtl.workspace-snapshot.v1", "state": state,
                "project_id": content_digest({"local_project_root": str(self.agent.store.root)}),
                "design_input_digest": design_input_digest(state),
                "capabilities": {"provider": self.agent.expert is not None,
                                 "simulation": self.agent.simulator is not None},
                "events": events, "next_cursor": events[-1]["sequence"] if events else cursor,
                "more_events": bool(events and events[-1]["sequence"] < state["revision"])}

    def operation(self, identifier: str) -> JsonObject:
        state = self.agent.store.read()
        require(identifier in state["workspace_operations"], "workspace_operation_unknown")
        row = copy.deepcopy(state["workspace_operations"][identifier])
        if row["phase"] in ("queued", "active", "cancellation_requested") and identifier not in self._tasks:
            row["phase"] = "reconciliation_needed"
        return {"id": identifier, **row, "reply": self._replies.get(identifier)}

    async def submit_discussion(self, message: str, *, client_operation_id: str,
                                expected_revision: int) -> JsonObject:
        require(uuid.UUID(hex=client_operation_id).hex == client_operation_id,
                "client_operation_id_invalid")
        require(type(expected_revision) is int and expected_revision >= 0,
                "expected_revision_invalid")
        selected = text(message, maximum=16000)
        request_digest = content_digest({"action": "discuss", "message": selected})
        state = self.agent.store.read()
        existing = state["workspace_operations"].get(client_operation_id)
        if existing is not None:
            require(existing["request_digest"] == request_digest,
                    "client_operation_id_conflict")
            return self.operation(client_operation_id)
        require(state["revision"] == expected_revision, "workspace_revision_stale")
        require(state["active"] is None, "interrupted_operation_requires_reconciliation")
        require(not any(row["phase"] in ("queued", "active", "cancellation_requested")
                        for row in state["workspace_operations"].values()),
                "workspace_writer_busy_or_unreconciled")
        require(state["status"] == "discovery", "discussion_requires_discovery")
        require(self.agent.expert is not None, "expert_not_configured")
        require(len(state["workspace_operations"]) < 128, "workspace_operation_limit")
        updated = copy.deepcopy(state)
        updated["workspace_operations"][client_operation_id] = {
            "request_digest": request_digest, "phase": "queued",
            "result_revision": None, "error_code": None,
        }
        self.agent.store.save(state, updated, "workspace.requested",
                              {"client_operation_id": client_operation_id,
                               "request_digest": request_digest})
        task = asyncio.create_task(self._run_discussion(client_operation_id, selected))
        self._tasks[client_operation_id] = task
        def reconcile_before_start(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                self._finish(client_operation_id, "reconciliation_needed", "operation_cancelled_uncertain")
                self._tasks.pop(client_operation_id, None)
        task.add_done_callback(reconcile_before_start)
        return self.operation(client_operation_id)

    async def _run_discussion(self, identifier: str, message: str) -> None:
        try:
            state = self.agent.store.read()
            row = state["workspace_operations"][identifier]
            if row["phase"] == "cancellation_requested":
                self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
                return
            if row["phase"] != "queued":
                return
            updated = copy.deepcopy(state)
            updated["workspace_operations"][identifier]["phase"] = "active"
            self.agent.store.save(state, updated, "workspace.active", {"client_operation_id": identifier})
            def emit_reply(value: str) -> None:
                self._replies[identifier] = value
            await self.agent.discuss(message, emit_reply=emit_reply)
            self._finish(identifier, "completed", None)
        except asyncio.CancelledError:
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
        except Exception:
            # The provider exception, message and reply are never persisted.
            self._finish(identifier, "failed", "operation_failed")
        finally:
            self._tasks.pop(identifier, None)

    def _finish(self, identifier: str, phase: str, error_code: str | None) -> None:
        state = self.agent.store.read()
        if state["workspace_operations"][identifier]["phase"] in (
                "completed", "failed", "cancelled", "reconciliation_needed"):
            return
        updated = copy.deepcopy(state)
        row = updated["workspace_operations"][identifier]
        row.update(phase=phase, error_code=error_code,
                   result_revision=state["revision"] if phase == "completed" else None)
        event = "workspace." + phase
        self.agent.store.save(state, updated, event, {"client_operation_id": identifier})

    def request_cancellation(self, identifier: str) -> JsonObject:
        state = self.agent.store.read()
        require(identifier in state["workspace_operations"], "workspace_operation_unknown")
        phase = state["workspace_operations"][identifier]["phase"]
        if phase in ("completed", "failed", "cancelled", "reconciliation_needed"):
            return self.operation(identifier)
        task = self._tasks.get(identifier)
        if task is None:
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
            return self.operation(identifier)
        if phase == "queued":
            self._finish(identifier, "cancelled", None)
            task.cancel()
            return self.operation(identifier)
        if phase != "cancellation_requested":
            updated = copy.deepcopy(state)
            updated["workspace_operations"][identifier]["phase"] = "cancellation_requested"
            self.agent.store.save(state, updated, "workspace.cancel_requested",
                                  {"client_operation_id": identifier})
        task.cancel()
        return self.operation(identifier)

    async def settle_for_shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for identifier in tuple(self._tasks):
            self.request_cancellation(identifier)
        if tasks:
            await asyncio.wait(tasks, timeout=2)

    def review(self, kind: str) -> JsonObject:
        state = self.agent.store.read()
        payload = review_payload(state, kind)
        return {"kind": kind, "revision": state["revision"],
                "state_digest": content_digest(state),
                "payload_digest": content_digest(payload), "payload": payload}

    def approve_review(self, kind: str, *, expected_revision: int,
                       state_digest: str, payload_digest: str) -> JsonObject:
        state = self.agent.store.read()
        require(state["revision"] == expected_revision, "workspace_revision_stale")
        require(content_digest(state) == state_digest, "review_state_stale")
        card = ShownReview(kind, state_digest, payload_digest)
        return approve_shown(self.agent, card, kind)

    async def discuss_direct(self, message: str, *, emit_reply: Callable[[str], None] | None = None) -> JsonObject:
        """CLI compatibility entry point over the same DesignAgent operation."""
        return await self.agent.discuss(message, emit_reply=emit_reply)
