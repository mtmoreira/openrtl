"""Transport-neutral project operations for a CLI or a local browser."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Callable
import uuid

from openrtl.application.design_agent import DesignAgent, design_input_digest
from openrtl.application.design_conversation import ShownReview, approve_shown, review_payload
from openrtl.application.design_workbench import DesignWorkbench
from openrtl.domain.design_session import JsonObject, ROLES, STAGES, canonical, content_digest, require, text
from openrtl.domain.provider_failures import EXPERT_OPERATION_ERROR_CODES


class DesignWorkspace:
    """One-writer session controller; callers run it on one event-loop thread.

    Submitted tasks belong to this service, not to an HTTP connection. The saved
    request digest and phase prevent retrying a provider turn after a refresh or
    process restart. Reply prose is persisted only with explicit detail capture.
    """

    def __init__(self, agent: DesignAgent) -> None:
        self.agent = agent
        self.workbench = DesignWorkbench(agent.store)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._replies: dict[str, str] = {}
        self._progress: JsonObject | None = None

    def _on_progress(self, value: JsonObject) -> None:
        if (isinstance(value, dict) and value.get("schema") == "openrtl.design-progress.v1" and
                value.get("stage") in {*ROLES, "simulation"} and
                value.get("phase") in ("started", "waiting", "returned_for_validation") and
                type(value.get("elapsed_ms")) is int):
            self._progress = {"stage": value["stage"], "phase": value["phase"],
                              "elapsed_ms": value["elapsed_ms"],
                              "operation_id": value["operation_id"]}

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
                "progress": self._progress,
                "events": events, "next_cursor": events[-1]["sequence"] if events else cursor,
                "more_events": bool(events and events[-1]["sequence"] < state["revision"])}

    def operation(self, identifier: str) -> JsonObject:
        state = self.agent.store.read()
        require(identifier in state["workspace_operations"], "workspace_operation_unknown")
        row = copy.deepcopy(state["workspace_operations"][identifier])
        if row["phase"] in ("queued", "active", "cancellation_requested") and identifier not in self._tasks:
            row["phase"] = "reconciliation_needed"
        reply = self._replies.get(identifier)
        if reply is None:
            for item in self._details({identifier}):
                if item["category"] == "conversation_output":
                    reply = item["payload"].get("text")
        return {"id": identifier, **row, "reply": reply}

    def _capture(self, identifier: str, category: str, payload: JsonObject) -> None:
        if self.agent.trace_store is not None:
            self.agent.trace_store.record(identifier, category, payload)

    def _details(self, identifiers: set[str]) -> list[JsonObject]:
        if self.agent.trace_store is None:
            return []
        return self.agent.trace_store.records(identifiers)

    def _capture_status(self) -> JsonObject:
        if self.agent.trace_store is None:
            return {"enabled": False, "available": False, "exported": False,
                    "omitted_records": 0, "limits": {}}
        return self.agent.trace_store.status()

    def conversation(self) -> JsonObject:
        """Read only explicitly captured displayed messages; never reissue a call."""
        identifiers = set(self.agent.store.read()["workspace_operations"])
        messages = []
        for item in self._details(identifiers):
            if item["category"] not in ("conversation_input", "conversation_output"):
                continue
            value = item["payload"].get("text")
            if isinstance(value, str):
                messages.append({"id": item["id"], "operation_id": item["operation_id"],
                                 "kind": "user" if item["category"] == "conversation_input" else "agent",
                                 "text": value, "timestamp_ns": item["timestamp_ns"],
                                 "truncated": item["truncated"]})
        return {"schema": "openrtl.conversation.v1", "messages": messages,
                "capture": self._capture_status()}

    def history(self, sequence: int) -> JsonObject:
        """Join explicit invocation identities and inspect saved, untrusted details."""
        require(type(sequence) is int and sequence >= 1, "history_sequence_invalid")
        events = self.agent.store.events()
        matches = [row for row in events if row["sequence"] == sequence]
        require(len(matches) == 1, "history_event_unknown")
        selected = matches[0]
        identifiers = {(name, selected["fields"][name])
                       for name in ("operation_id", "client_operation_id", "run_id")
                       if name in selected["fields"]}
        trace = [selected]
        # One workspace request may contain several provider invocations. Follow
        # explicit edges only; neighboring timestamps never establish identity.
        while identifiers:
            matching = [row for row in events if any(row["fields"].get(name) == value
                                                     for name, value in identifiers)]
            expanded = identifiers | {(name, row["fields"][name]) for row in matching
                                      for name in ("operation_id", "client_operation_id", "run_id")
                                      if name in row["fields"]}
            trace = matching
            require(len(trace) <= 256 and len(expanded) <= 256, "history_trace_limit")
            if expanded == identifiers:
                break
            identifiers = expanded
        historical = self.agent.store.historical_state(sequence)
        terminal = self.agent.store.historical_state(trace[-1]["sequence"])
        initial_revision = max(0, trace[0]["sequence"] - 1)
        initial = self.agent.store.historical_state(initial_revision)
        provider = historical["provider"]
        summary: JsonObject = {
            "revision": historical["revision"], "status": historical["status"],
            "stage": historical["stage"], "calls": historical["calls"],
            "repairs": historical["repairs"], "last_error": historical["last_error"],
            "active": historical["active"],
            "provider": {"model": provider["model"],
                         "spent_nano_usd": provider["spent_nano_usd"],
                         "limit_nano_usd": provider["limit_nano_usd"],
                         "uncertain": provider["uncertain"]},
        }
        run_ids = {str(value) for name, value in identifiers
                   if name in ("operation_id", "run_id")}
        simulation = terminal["simulation"]
        evidence = None
        if simulation is not None and simulation["run_id"] in run_ids:
            evidence = {name: simulation[name] for name in
                        ("run_id", "status", "evidence_kind", "tests", "model_tests",
                         "diagnostics", "error_code")}
        started = next((row for row in trace if row["event"] == "operation.started"), None)
        tool_calls = started["fields"].get("tool_calls") if started else None
        shell_commands = started["fields"].get("shell_commands") if started else None
        details = self._details({str(value) for _, value in identifiers})
        categories = {row["category"] for row in details}
        provider_kinds = {row["payload"]["kind"] for row in details if row["category"] == "provider_trace"
                          and isinstance(row["payload"].get("kind"), str)}
        reasoning_visibility = "not_recorded"
        process_requests = sum(row["category"] == "provider_trace" and
                               row["payload"].get("kind") == "process.request" for row in details)
        process_responses = sum(row["category"] == "provider_trace" and
                                row["payload"].get("kind") == "process.response" for row in details)
        process_output = "not_recorded"
        for row in details:
            payload = row["payload"]
            if row["category"] != "provider_trace" or payload.get("kind") not in ("provider.response", "process.response"):
                continue
            if row["truncated"] or payload.get("truncated"):
                if payload.get("kind") == "process.response":
                    process_output = "process_output_truncated"
                elif reasoning_visibility != "captured":
                    reasoning_visibility = "unavailable_due_to_truncation"
                continue
            try:
                content = json.loads(payload.get("content", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(content, dict):
                if payload.get("kind") == "process.response":
                    if content.get("output_truncated"):
                        process_output = "process_output_truncated"
                    elif any(isinstance(content.get(name), str) for name in ("stdout", "stderr")):
                        if process_output != "process_output_truncated":
                            process_output = "process_output_captured"
                    elif process_output == "not_recorded":
                        process_output = "process_output_not_returned"
                elif any(isinstance(content.get(name), str) and content[name].strip()
                       for name in ("thinking", "reasoning_summary")):
                    reasoning_visibility = "captured"
                elif reasoning_visibility == "not_recorded":
                    reasoning_visibility = "not_returned"
        files = []
        for path in sorted(set(initial["files"]) | set(terminal["files"])):
            before, after = initial["files"].get(path), terminal["files"].get(path)
            if before == after:
                continue
            files.append({"path": path, "change": "created" if before is None else "deleted" if after is None else "modified",
                          "revision": terminal["revision"] if after is not None else initial_revision,
                          "digest": after if after is not None else before,
                          "previous_revision": initial_revision, "previous_digest": before,
                          "untrusted": True})
        calls: dict[str, JsonObject] = {}
        metric_names = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                        "elapsed_ms", "estimated_cost_nano_usd", "total_duration_ns",
                        "load_duration_ns", "prompt_eval_duration_ns", "eval_duration_ns")
        expert_ids = {row["fields"].get("operation_id") for row in trace
                      if row["event"] == "operation.started" and row["fields"].get("role") in ROLES.values()}
        for row in trace:
            fields = row["fields"]
            identifier = fields.get("operation_id")
            if identifier is None or identifier not in expert_ids:
                continue
            call = calls.setdefault(identifier, {"operation_id": identifier,
                "provider": None, "model": None, "finish_reason": None,
                **dict.fromkeys(metric_names)})
            for name in (*metric_names, "provider", "model", "finish_reason"):
                if name in fields:
                    call[name] = fields[name]
        for row in details:
            if row["operation_id"] not in calls:
                continue
            call = calls[row["operation_id"]]
            if row["category"] == "provider_metrics":
                payload = row["payload"]
            elif row["category"] == "runtime_event" and row["payload"].get("kind") in (
                    "usage.reported", "provider_call.completed"):
                payload = row["payload"].get("attributes")
                if not isinstance(payload, dict):
                    continue
            else:
                continue
            metadata = payload.get("provider_metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            for name in metric_names:
                value = payload.get(name, metadata.get(name))
                if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
                    value = int(value)
                if call[name] is None and type(value) is int and 0 <= value < 2**63:
                    call[name] = value
            for name in ("provider", "model", "finish_reason"):
                value = payload.get(name, metadata.get(name))
                if call[name] is None and isinstance(value, str) and len(value) <= 256:
                    call[name] = value
        metrics: JsonObject = {"calls": list(calls.values())}
        for name in metric_names:
            values = [row[name] for row in calls.values()]
            metrics[name] = sum(values) if values and all(type(value) is int for value in values) else None
        return {
            "schema": "openrtl.web-history-detail.v1", "event": selected,
            "trace": trace, "state": summary, "evidence": evidence,
            "terminal_revision": terminal["revision"], "details": details,
            "capture": self._capture_status(), "files": files, "metrics": metrics,
            "visibility": {
                "raw_prompt": "captured" if categories & {"user_input", "conversation_input", "provider_request"} or "provider.request" in provider_kinds else "not_persisted",
                "raw_reply": "captured" if categories & {"assistant_output", "conversation_output", "provider_response"} or "provider.response" in provider_kinds else "not_persisted",
                "hidden_reasoning": "not_collected",
                "provider_reasoning": reasoning_visibility,
                "tool_calls": tool_calls if tool_calls is not None else "not_recorded",
                "shell_commands": shell_commands if shell_commands is not None else "not_recorded",
                "observed_process_requests": process_requests,
                "observed_process_responses": process_responses,
                "shell_output": process_output if process_output != "not_recorded" else
                                "simulation_diagnostics" if evidence is not None else "not_recorded",
            },
        }

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
        self._capture(client_operation_id, "conversation_input", {"text": selected})
        task = asyncio.create_task(self._run_discussion(client_operation_id, selected))
        self._tasks[client_operation_id] = task
        def reconcile_before_start(done: asyncio.Task[None]) -> None:
            if done.cancelled():
                self._finish(client_operation_id, "reconciliation_needed", "operation_cancelled_uncertain")
                self._tasks.pop(client_operation_id, None)
        task.add_done_callback(reconcile_before_start)
        return self.operation(client_operation_id)

    async def submit_question(self, message: str, *, client_operation_id: str,
                              expected_revision: int, attachment: object | None = None,
                              kind: str = "question", intent: str = "feature") -> JsonObject:
        require(uuid.UUID(hex=client_operation_id).hex == client_operation_id,
                "client_operation_id_invalid")
        require(type(expected_revision) is int and expected_revision >= 0,
                "expected_revision_invalid")
        selected = text(message, maximum=16000)
        require(kind in ("question", "change"), "workbench_request_kind_invalid")
        require(intent in ("feature", "dv", "optimization"), "workbench_change_intent_invalid")
        bound = self.workbench.attachment(attachment) if attachment is not None else None
        request_digest = content_digest({"action": kind, "intent": intent,
                                         "message": selected, "attachment": bound})
        state = self.agent.store.read()
        existing = state["workspace_operations"].get(client_operation_id)
        if existing is not None:
            require(existing["request_digest"] == request_digest, "client_operation_id_conflict")
            return self.operation(client_operation_id)
        require(state["revision"] == expected_revision, "workspace_revision_stale")
        if kind == "change":
            require(state["stage"] == len(STAGES) and state["manifest"] is not None,
                    "change_requires_complete_baseline")
        require(state["active"] is None, "interrupted_operation_requires_reconciliation")
        require(not any(row["phase"] in ("queued", "active", "cancellation_requested")
                        for row in state["workspace_operations"].values()),
                "workspace_writer_busy_or_unreconciled")
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
        self._capture(client_operation_id, "conversation_input", {"text": selected, "attachment": bound})
        task = asyncio.create_task(self._run_question(client_operation_id, selected, bound, kind, intent))
        self._tasks[client_operation_id] = task
        task.add_done_callback(lambda done: self._reconcile_cancelled_task(client_operation_id, done))
        return self.operation(client_operation_id)

    def simulation_plan(self) -> JsonObject:
        state = self.agent.store.read()
        require(state["status"] == "building" and state["stage"] == len(STAGES) and
                state["manifest"] is not None, "workspace_simulation_not_ready")
        require(self.agent.simulator is not None, "isolated_simulator_not_configured")
        profile = getattr(self.agent.simulator, "profile", None)
        transport = getattr(self.agent.simulator, "workload_transport", None)
        public_runtime = ({"profile_digest": content_digest(profile),
                           "verilator_version": profile["verilator_version"],
                           "timeout_seconds": profile["timeout_seconds"],
                           "resources": profile.get("resources"),
                           "transport_digest": content_digest(transport.identity) if transport else None,
                           "backend": transport.identity["schema"] if transport else "host-bind"}
                          if isinstance(profile, dict) else {"profile_digest": None,
                                                               "verilator_version": None,
                                                               "timeout_seconds": None,
                                                               "resources": None,
                                                               "transport_digest": None,
                                                               "backend": "test-double"})
        plan = {"schema": "openrtl.web-simulation-plan.v1", "revision": state["revision"],
                "input_digest": design_input_digest(state), "top": state["manifest"]["top"],
                "sources": state["manifest"]["sources"],
                "test_modules": state["manifest"]["test_modules"],
                "expected_tests": state["manifest"]["expected_tests"],
                "seed": state["manifest"]["seed"], "parameters": {}, "runtime": public_runtime}
        return {**plan, "plan_digest": content_digest(plan)}

    async def submit_simulation(self, *, client_operation_id: str,
                                expected_revision: int, plan_digest: str) -> JsonObject:
        require(uuid.UUID(hex=client_operation_id).hex == client_operation_id,
                "client_operation_id_invalid")
        require(type(expected_revision) is int and expected_revision >= 0,
                "expected_revision_invalid")
        require(isinstance(plan_digest, str), "simulation_plan_digest_invalid")
        request_digest = content_digest({"action": "simulate", "plan_digest": plan_digest})
        state = self.agent.store.read()
        existing = state["workspace_operations"].get(client_operation_id)
        if existing is not None:
            require(existing["request_digest"] == request_digest, "client_operation_id_conflict")
            return self.operation(client_operation_id)
        require(state["revision"] == expected_revision, "workspace_revision_stale")
        require(state["active"] is None, "interrupted_operation_requires_reconciliation")
        require(not any(row["phase"] in ("queued", "active", "cancellation_requested")
                        for row in state["workspace_operations"].values()),
                "workspace_writer_busy_or_unreconciled")
        require(len(state["workspace_operations"]) < 128, "workspace_operation_limit")
        require(self.simulation_plan()["plan_digest"] == plan_digest, "simulation_plan_stale")
        updated = copy.deepcopy(state)
        updated["workspace_operations"][client_operation_id] = {
            "request_digest": request_digest, "phase": "queued",
            "result_revision": None, "error_code": None,
        }
        self.agent.store.save(state, updated, "workspace.requested",
                              {"client_operation_id": client_operation_id,
                               "request_digest": request_digest})
        task = asyncio.create_task(self._run_simulation(client_operation_id))
        self._tasks[client_operation_id] = task
        task.add_done_callback(lambda done: self._reconcile_cancelled_task(client_operation_id, done))
        return self.operation(client_operation_id)

    async def _run_simulation(self, identifier: str) -> None:
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
            previous_progress = self.agent.progress
            self.agent.progress = self._on_progress
            try:
                await self.agent.advance()
            finally:
                self.agent.progress = previous_progress
            self._finish(identifier, "completed", None)
        except asyncio.CancelledError:
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
        except Exception:
            # The simulator may have lost contact after creating a container.
            # Keep the workspace blocked until its recorded runtime is reconciled.
            uncertain = self.agent.store.read()["active"] is not None
            self._finish(identifier, "reconciliation_needed" if uncertain else "failed",
                         "simulation_reconciliation_required" if uncertain else "operation_failed")
        finally:
            self._tasks.pop(identifier, None)
            self._progress = None

    def _reconcile_cancelled_task(self, identifier: str, done: asyncio.Task[None]) -> None:
        if done.cancelled():
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
            self._tasks.pop(identifier, None)

    async def _run_question(self, identifier: str, message: str, attachment: JsonObject | None,
                            kind: str, intent: str) -> None:
        previous_progress = self.agent.progress
        self.agent.progress = self._on_progress
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
            context = message
            if attachment is not None:
                if attachment.get("kind") == "waveform":
                    window = self.workbench.waveforms.query(
                        attachment["run_id"], attachment["trace_digest"], attachment["signals"],
                        attachment["start_fs"], attachment["end_fs"], limit=32)
                    context += "\nSelected recorded waveform (bounded): " + canonical(window).decode("utf-8")
                else:
                    source = self.workbench.source(attachment["revision"], attachment["path"], attachment["digest"])
                    lines = source["content"].splitlines()[attachment["start_line"] - 1:attachment["end_line"]]
                    context += ("\nSelected source: " + attachment["path"] + " at revision " +
                                str(attachment["revision"]) + " digest " + attachment["digest"] +
                                " lines " + str(attachment["start_line"]) + "-" + str(attachment["end_line"]) +
                                "\n" + "\n".join(lines))
                require(len(context.encode("utf-8")) <= 32000, "workbench_question_too_large")
            if kind == "change":
                result = await self.agent.propose_improvement(context, intent=intent)
                self._replies[identifier] = result["summary"] + " Review the exact change card before approval."
            else:
                result = await self.agent.explain(context)
                self._replies[identifier] = result["explanation"]
            self._capture(identifier, "conversation_output", {"text": self._replies[identifier]})
            self._finish(identifier, "completed", None)
        except asyncio.CancelledError:
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
        except ValueError as error:
            code = str(error) if str(error) in EXPERT_OPERATION_ERROR_CODES else "operation_failed"
            self._finish(identifier, "failed", code)
        except Exception:
            self._finish(identifier, "failed", "operation_failed")
        finally:
            self.agent.progress = previous_progress
            self._progress = None
            self._tasks.pop(identifier, None)

    async def _run_discussion(self, identifier: str, message: str) -> None:
        previous_progress = self.agent.progress
        self.agent.progress = self._on_progress
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
                self._capture(identifier, "conversation_output", {"text": value})
            await self.agent.discuss(message, emit_reply=emit_reply)
            self._finish(identifier, "completed", None)
        except asyncio.CancelledError:
            self._finish(identifier, "reconciliation_needed", "operation_cancelled_uncertain")
        except ValueError as error:
            code = str(error) if str(error) in EXPERT_OPERATION_ERROR_CODES else "operation_failed"
            self._finish(identifier, "failed", code)
        except Exception:
            # Arbitrary exception text is never persisted.
            self._finish(identifier, "failed", "operation_failed")
        finally:
            self.agent.progress = previous_progress
            self._progress = None
            self._tasks.pop(identifier, None)

    def _finish(self, identifier: str, phase: str, error_code: str | None) -> None:
        state = self.agent.store.read()
        if state["workspace_operations"][identifier]["phase"] in (
                "completed", "failed", "cancelled", "reconciliation_needed"):
            return
        updated = copy.deepcopy(state)
        row = updated["workspace_operations"][identifier]
        row.update(phase=phase, error_code=error_code,
                   result_revision=state["revision"])
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

    async def abandon_interrupted_simulation(self, operation_id: str) -> JsonObject:
        state = self.agent.store.read()
        require(state["active"] is not None and state["active"]["kind"] == "simulation" and
                state["active"]["id"] == operation_id, "recovery_operation_mismatch")
        require(not self._tasks, "workspace_writer_busy_or_unreconciled")
        return await self.agent.abandon(operation_id)

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
