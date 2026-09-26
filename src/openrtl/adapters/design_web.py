"""Loopback HTTP adapter for the shared design workspace."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
from pathlib import Path
import re
import threading
from typing import Any, Callable, cast
from urllib.parse import parse_qs, urlsplit

from openrtl.adapters.design_session_store import DesignSessionStore, safe_root
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignExpert, DesignPolicy, DesignRecovery, DesignSimulator
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import JsonObject, SESSION_SCHEMA, canonical, require
from openrtl.domain.provider_controls import (
    dollars, model_catalog, ollama_catalog, provider_selector, selector_model, selector_provider,
    spend_limit_nano,
)


class WorkspaceRuntime:
    """Own SQLite and agent tasks on one loop, independent of request threads."""

    def __init__(self, project: Path, *, create: bool, expert_factory: Callable[[], DesignExpert | None],
                 policy: DesignPolicy,
                 provider_builder: Callable[[str, str, str | None, int], DesignExpert] | None = None,
                 initial_provider: str = "openai",
                 initial_model: str | None = None, initial_limit_nano: int | None = None,
                 initial_key: str | None = None,
                 timeout_seconds: int = 120, detailed_capture: bool = False,
                 simulator_factory: Callable[[], DesignSimulator | None] | None = None) -> None:
        self.project = safe_root(project)
        self.expert_factory = expert_factory
        self.simulator_factory = simulator_factory or (lambda: None)
        self.policy = policy
        self.provider_builder = provider_builder
        self.initial_provider = initial_provider
        self.initial_model = initial_model
        self.initial_limit_nano = initial_limit_nano
        require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 300, "expert_timeout_invalid")
        require(type(detailed_capture) is bool, "trace_capture_invalid")
        self.timeout_seconds, self.detailed_capture = timeout_seconds, detailed_capture
        self.trace_store: DesignTraceStore | None = None
        self._key = initial_key
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.failure: BaseException | None = None
        self.closed = False
        self.workspace: DesignWorkspace | None = None
        self.store: DesignSessionStore | None = None
        self.thread = threading.Thread(target=self._run, args=(create,),
                                       name="openrtl-workspace", daemon=True)
        self.thread.start()
        require(self.ready.wait(timeout=10), "workspace_start_timeout")
        if self.failure is not None:
            raise ValueError("workspace_start_failed") from None

    def _open_project(self, *, create: bool) -> None:
        store = DesignSessionStore(self.project, create=create)
        self.store = store
        if store.read()["schema"] == SESSION_SCHEMA:
            self._activate_project()

    def _activate_project(self) -> None:
        require(self.store is not None and self.workspace is None, "project_activation_invalid")
        self.trace_store = DesignTraceStore(self.store, enabled=self.detailed_capture)
        expert = (self.provider_builder(self.initial_provider, self.initial_model, self._key, self.timeout_seconds)
                  if self.provider_builder is not None and self.initial_model is not None
                  else self.expert_factory())
        simulator = self.simulator_factory()
        from openrtl.adapters.design_simulation import IsolatedDesignSimulator
        if isinstance(simulator, IsolatedDesignSimulator):
            simulator.trace_store = self.trace_store
        recovery = cast(DesignRecovery | None, simulator if hasattr(simulator, "abandon") else None)
        self._bind_trace(expert)
        agent = DesignAgent(self.store, expert, simulator, policy=self.policy, recovery=recovery,
                            trace_store=self.trace_store)
        if self.initial_model is not None:
            selector = provider_selector(self.initial_provider, self.initial_model)
            selected = self.store.read()["provider"]
            if selected["model"] != selector or selected["limit_nano_usd"] != self.initial_limit_nano:
                agent.configure_provider(selector, self.initial_limit_nano)
        self.workspace = DesignWorkspace(agent)
        self.workspace.reconcile_interrupted()

    def _bind_trace(self, expert: DesignExpert | None) -> None:
        from openrtl.adapters.design_generation import AgentRigDesignExpert, OllamaDesignExpert
        if isinstance(expert, (AgentRigDesignExpert, OllamaDesignExpert)):
            expert.trace_store = self.trace_store

    def provider_settings(self) -> JsonObject:
        async def invoke() -> JsonObject:
            state = self.store.read() if self.store is not None and self.workspace is not None else None
            provider = (state["provider"] if state is not None
                        else {"model": None, "limit_nano_usd": None,
                              "spent_nano_usd": 0, "uncertain": False,
                              "prior_unpriced_calls": 0})
            selection = provider["model"]
            selected_provider = selector_provider(selection) if selection is not None else "openai"
            selected_model = selector_model(selection) if selection is not None else None
            return {"models": model_catalog(), "ollama": ollama_catalog(),
                    "selected_provider": selected_provider, "selected_model": selected_model,
                    "revision": state["revision"] if state is not None else None,
                    "max_spend_usd": dollars(provider["limit_nano_usd"]) if provider["limit_nano_usd"] is not None else None,
                    "estimated_spend_usd": dollars(provider["spent_nano_usd"]),
                    "uncertain": provider["uncertain"],
                    "prior_unpriced_calls": provider["prior_unpriced_calls"],
                    "enabled": self.workspace is not None and self.workspace.agent.expert is not None,
                    "timeout_seconds": self.timeout_seconds, "detailed_capture": self.detailed_capture,
                    "key_present": selected_provider == "openai" and self._key is not None,
                    "editable": self.provider_builder is not None and self.workspace is not None}
        return self._await(invoke())

    def reconcile_provider_spend(self, expected_revision: object, decision: object) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "provider_settings_unavailable")
            require(type(expected_revision) is int and type(decision) is str,
                    "provider_reconciliation_request_invalid")
            state = self.workspace.agent.reconcile_provider_spend(
                cast(int, expected_revision), cast(str, decision))
            return {"revision": state["revision"], "estimated_spend_usd":
                    dollars(state["provider"]["spent_nano_usd"]),
                    "uncertain": state["provider"]["uncertain"]}
        return self._await(invoke())

    def configure_provider(self, provider: object, model: object, max_spend_usd: object,
                           api_key: object, enabled: object, timeout_seconds: object = None,
                           detailed_capture: object = None) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None and self.provider_builder is not None,
                    "provider_settings_unavailable")
            selection = provider_selector(provider, model)
            selected_provider = selector_provider(selection)
            selected_model = selector_model(selection)
            timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
            capture = self.detailed_capture if detailed_capture is None else detailed_capture
            require(type(timeout) is int and 1 <= timeout <= 300, "expert_timeout_invalid")
            require(type(capture) is bool, "trace_capture_invalid")
            require(type(enabled) is bool and (api_key is None or type(api_key) is str),
                    "provider_settings_invalid")
            candidate_key = self._key
            if selected_provider == "openai" and api_key is not None:
                from openrtl.adapters.provider_invocation import MemoryOpenAIAuthenticationSource
                candidate_key = MemoryOpenAIAuthenticationSource(cast(str, api_key)).resolve_api_key()
            if selected_provider == "openai":
                limit = spend_limit_nano(max_spend_usd)
            else:
                require(max_spend_usd is None and api_key is None, "provider_spend_not_applicable")
                limit = None
            candidate = (self.provider_builder(selected_provider, selected_model, candidate_key, cast(int, timeout))
                         if enabled else None)
            self.workspace.agent.configure_provider(selection, limit)
            assert self.trace_store is not None
            self.trace_store.set_enabled(cast(bool, capture))
            self.timeout_seconds, self.detailed_capture = cast(int, timeout), cast(bool, capture)
            self._bind_trace(candidate)
            self._key = candidate_key
            self.workspace.agent.expert = candidate
            return {"selected_provider": selected_provider, "selected_model": selected_model,
                    "max_spend_usd": dollars(limit) if limit is not None else None,
                    "estimated_spend_usd": dollars(self.workspace.agent.store.read()["provider"]["spent_nano_usd"]),
                    "enabled": enabled,
                    "timeout_seconds": self.timeout_seconds, "detailed_capture": self.detailed_capture,
                    "key_present": selected_provider == "openai" and candidate_key is not None}
        return self._await(invoke())

    def _run(self, create: bool) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            if create or self.project.exists():
                self._open_project(create=create)
        except BaseException as error:
            self.failure = error
            self.ready.set()
            if self.store is not None:
                self.store.close()
            self.loop.close()
            return
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            if self.store is not None:
                self.store.close()
            self.loop.close()

    def project_state(self) -> JsonObject:
        async def invoke() -> JsonObject:
            return {"created": self.store is not None, "ready": self.workspace is not None,
                    "schema": self.store.read()["schema"] if self.store is not None else None,
                    "name": self.project.name}
        return self._await(invoke())

    def create_project(self) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.store is None, "project_already_created")
            self._open_project(create=True)
            return {"created": True, "ready": True, "schema": SESSION_SCHEMA, "name": self.project.name}
        return self._await(invoke())

    def upgrade_project(self) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.store is not None and self.workspace is None, "project_upgrade_not_required")
            self.store.upgrade()
            self._activate_project()
            return {"created": True, "ready": True, "schema": SESSION_SCHEMA, "name": self.project.name}
        return self._await(invoke())

    def call(self, operation: Callable[[DesignWorkspace], JsonObject]) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            return operation(self.workspace)
        return self._await(invoke())

    def submit(self, message: str, identifier: str, revision: int) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            return await self.workspace.submit_discussion(message, client_operation_id=identifier,
                                                          expected_revision=revision)
        return self._await(invoke())

    def question(self, message: str, identifier: str, revision: int, attachment: object | None,
                 kind: str, intent: str) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            return await self.workspace.submit_question(message, client_operation_id=identifier,
                                                        expected_revision=revision, attachment=attachment,
                                                        kind=kind, intent=intent)
        return self._await(invoke())

    def generate(self, identifier: str, revision: int, plan_digest: str) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            assert self.workspace is not None
            return await self.workspace.submit_generation(client_operation_id=identifier,
                                                          expected_revision=revision,
                                                          plan_digest=plan_digest)
        return self._await(invoke())

    def simulate(self, identifier: str, revision: int, plan_digest: str) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            return await self.workspace.submit_simulation(client_operation_id=identifier,
                                                          expected_revision=revision,
                                                          plan_digest=plan_digest)
        return self._await(invoke())

    def recover_simulation(self, operation_id: str) -> JsonObject:
        async def invoke() -> JsonObject:
            require(self.workspace is not None, "project_not_created")
            return await self.workspace.abandon_interrupted_simulation(operation_id)
        return self._await(invoke())

    def _await(self, operation: Coroutine[Any, Any, JsonObject]) -> JsonObject:
        future: Future[JsonObject] = asyncio.run_coroutine_threadsafe(operation, self.loop)
        return future.result(timeout=15)

    def close(self) -> None:
        if self.closed:
            return
        async def settle() -> JsonObject:
            if self.workspace is not None:
                await self.workspace.settle_for_shutdown()
            return {}
        self._await(settle())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        require(not self.thread.is_alive(), "workspace_stop_timeout")
        self.closed = True


def handler(runtime: WorkspaceRuntime) -> type[BaseHTTPRequestHandler]:
    class WorkspaceHandler(BaseHTTPRequestHandler):
        server_version = "OpenRTL/0.4"

        def log_message(self, format: str, *args: object) -> None:
            # Paths and request bodies may contain private design text.
            return

        def _headers(self) -> None:
            expected = "127.0.0.1:" + str(self.server.server_port)
            require(self.headers.get("Host") == expected, "web_host_invalid")
            origin = self.headers.get("Origin")
            require(origin is None or origin == "http://" + expected, "web_origin_invalid")

        def _send(self, code: int, body: bytes, media_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; "
                             "script-src 'self'; style-src 'self'; img-src 'self' data:; "
                             "object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, value: object) -> None:
            self._send(code, canonical(value), "application/json; charset=utf-8")

        def _body(self, keys: set[str], optional: set[str] | frozenset[str] = frozenset()) -> JsonObject:
            require(self.headers.get("Content-Type") == "application/json", "web_content_type_invalid")
            size = self.headers.get("Content-Length")
            require(size is not None and size.isascii() and size.isdecimal() and 1 <= int(size) <= 20_000,
                    "web_request_size_invalid")
            raw = self.rfile.read(int(size))
            require(len(raw) == int(size), "web_request_incomplete")
            value = json.loads(raw)
            require(isinstance(value, dict) and keys <= set(value) <= keys | optional, "web_request_fields_invalid")
            return cast(JsonObject, value)

        def _perform(self, action: Callable[[], None]) -> None:
            try:
                self._headers()
                action()
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                allowed = {"waveform_trace_missing", "waveform_trace_changed", "waveform_trace_unsupported",
                           "waveform_browser_limit", "waveform_trace_metadata_invalid", "waveform_run_unknown",
                           "waveform_trace_identity_stale", "waveform_response_limit",
                           "provider_model_incompatible", "provider_spend_limit_invalid",
                           "provider_kind_invalid", "provider_spend_not_applicable",
                           "pinned_optional_ollama_sdk_required",
                           "provider_spend_limit_below_used", "provider_spend_uncertain",
                           "provider_reconciliation_request_invalid",
                           "provider_reconciliation_decision_invalid",
                           "provider_reconciliation_not_required", "workspace_revision_stale",
                           "generation_plan_stale", "workspace_generation_not_ready",
                           "provider_spend_not_configured", "provider_spend_budget_exhausted",
                           "expert_not_configured", "expert_call_budget_exhausted", "repair_budget_exhausted",
                           "workspace_writer_busy_or_unreconciled",
                           "interrupted_operation_requires_reconciliation"}
                code = str(error) if type(error) is ValueError and str(error) in allowed else "invalid_or_stale_request"
                self._json(409, {"error": code})
            except Exception:
                self._json(503, {"error": "workspace_unavailable"})

        def do_GET(self) -> None:
            def action() -> None:
                parsed = urlsplit(self.path)
                require(not parsed.fragment, "web_path_invalid")
                if parsed.path == "/":
                    body = files("openrtl").joinpath("web/index.html").read_bytes()
                    self._send(200, body, "text/html; charset=utf-8")
                    return
                assets = {"/app.css": ("web/app.css", "text/css; charset=utf-8"),
                          "/app.js": ("web/app.js", "text/javascript; charset=utf-8")}
                if parsed.path in assets:
                    name, media = assets[parsed.path]
                    self._send(200, files("openrtl").joinpath(name).read_bytes(), media)
                    return
                if parsed.path == "/api/snapshot":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) in (set(), {"cursor"}), "web_query_invalid")
                    cursor = int(query.get("cursor", ["0"])[0])
                    self._json(200, runtime.call(lambda workspace: workspace.snapshot(cursor)))
                    return
                if parsed.path == "/api/conversation":
                    require(not parsed.query, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.conversation()))
                    return
                if parsed.path == "/api/project":
                    self._json(200, runtime.project_state())
                    return
                if parsed.path == "/api/provider":
                    require(not parsed.query, "web_query_invalid")
                    self._json(200, runtime.provider_settings())
                    return
                if parsed.path == "/api/review":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) == {"kind"} and len(query["kind"]) == 1, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.review(query["kind"][0])))
                    return
                if parsed.path == "/api/generation/plan":
                    require(not parsed.query, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.generation_plan()))
                    return
                if parsed.path == "/api/simulation/plan":
                    require(not parsed.query, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.simulation_plan()))
                    return
                if parsed.path == "/api/runs":
                    require(not parsed.query, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.waveforms.runs()))
                    return
                if parsed.path == "/api/waveform/catalog":
                    query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
                    require(set(query) in ({"run_id"}, {"run_id", "search"}) and
                            all(len(value) == 1 for value in query.values()), "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.waveforms.catalog(
                        query["run_id"][0], query.get("search", [""])[0])))
                    return
                if parsed.path == "/api/workbench":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) == {"revision"} and len(query["revision"]) == 1, "web_query_invalid")
                    revision = int(query["revision"][0])
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.inventory(revision)))
                    return
                if parsed.path == "/api/source":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) == {"revision", "path", "digest"} and
                            all(len(value) == 1 for value in query.values()), "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.source(
                        int(query["revision"][0]), query["path"][0], query["digest"][0])))
                    return
                if parsed.path == "/api/diff":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) == {"before", "after", "path"} and
                            all(len(value) == 1 for value in query.values()), "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.diff(
                        int(query["before"][0]), int(query["after"][0]), query["path"][0])))
                    return
                match = re.fullmatch(r"/api/history/([1-9][0-9]*)", parsed.path)
                if match is not None:
                    sequence = int(match.group(1))
                    self._json(200, runtime.call(lambda workspace: workspace.history(sequence)))
                    return
                match = re.fullmatch(r"/api/operations/([a-f0-9]{32})", parsed.path)
                if match is not None:
                    identifier = match.group(1)
                    self._json(200, runtime.call(lambda workspace: workspace.operation(identifier)))
                    return
                self._json(404, {"error": "route_not_found"})
            self._perform(action)

        def do_POST(self) -> None:
            def action() -> None:
                parsed = urlsplit(self.path)
                require(not parsed.query and not parsed.fragment, "web_path_invalid")
                if parsed.path == "/api/discussions":
                    body = self._body({"message", "client_operation_id", "expected_revision"})
                    self._json(202, runtime.submit(body["message"], body["client_operation_id"],
                                                   body["expected_revision"]))
                    return
                if parsed.path == "/api/questions":
                    body = self._body({"message", "client_operation_id", "expected_revision",
                                       "attachment", "kind", "intent"})
                    self._json(202, runtime.question(body["message"], body["client_operation_id"],
                                                     body["expected_revision"], body["attachment"],
                                                     body["kind"], body["intent"]))
                    return
                if parsed.path == "/api/generations":
                    body = self._body({"client_operation_id", "expected_revision", "plan_digest"})
                    self._json(202, runtime.generate(body["client_operation_id"],
                                                     body["expected_revision"], body["plan_digest"]))
                    return
                if parsed.path == "/api/simulations":
                    body = self._body({"client_operation_id", "expected_revision", "plan_digest"})
                    self._json(202, runtime.simulate(body["client_operation_id"],
                                                     body["expected_revision"], body["plan_digest"]))
                    return
                if parsed.path == "/api/simulation/recover":
                    body = self._body({"operation_id"})
                    self._json(200, runtime.recover_simulation(body["operation_id"]))
                    return
                if parsed.path == "/api/waveform/query":
                    body = self._body({"run_id", "trace_digest", "signals", "start_fs", "end_fs", "limit"})
                    self._json(200, runtime.call(lambda workspace: workspace.workbench.waveforms.query(
                        body["run_id"], body["trace_digest"], body["signals"],
                        body["start_fs"], body["end_fs"], body["limit"])))
                    return
                if parsed.path == "/api/project":
                    self._body(set())
                    self._json(201, runtime.create_project())
                    return
                if parsed.path == "/api/provider":
                    body = self._body({"provider", "model", "max_spend_usd", "api_key", "enabled"},
                                      {"timeout_seconds", "detailed_capture"})
                    self._json(200, runtime.configure_provider(
                        body["provider"], body["model"], body["max_spend_usd"],
                        body["api_key"], body["enabled"], body.get("timeout_seconds"),
                        body.get("detailed_capture")))
                    return
                if parsed.path == "/api/provider/reconcile":
                    body = self._body({"expected_revision", "decision"})
                    self._json(200, runtime.reconcile_provider_spend(
                        body["expected_revision"], body["decision"]))
                    return
                if parsed.path == "/api/project/upgrade":
                    self._body(set())
                    self._json(200, runtime.upgrade_project())
                    return
                if parsed.path == "/api/approve":
                    body = self._body({"kind", "expected_revision", "state_digest", "payload_digest"})
                    self._json(200, runtime.call(lambda workspace: workspace.approve_review(
                        body["kind"], expected_revision=body["expected_revision"],
                        state_digest=body["state_digest"], payload_digest=body["payload_digest"])))
                    return
                match = re.fullmatch(r"/api/operations/([a-f0-9]{32})/cancel", parsed.path)
                if match is not None:
                    self._body(set())
                    self._json(200, runtime.call(lambda workspace: workspace.request_cancellation(match.group(1))))
                    return
                self._json(404, {"error": "route_not_found"})
            self._perform(action)
    return WorkspaceHandler


def serve(project: Path, *, create: bool, port: int, expert_factory: Callable[[], DesignExpert | None],
          policy: DesignPolicy,
          provider_builder: Callable[[str, str, str | None, int], DesignExpert] | None = None,
          initial_provider: str = "openai",
          initial_model: str | None = None, initial_limit_nano: int | None = None,
          initial_key: str | None = None,
          timeout_seconds: int = 120, detailed_capture: bool = False,
          simulator_factory: Callable[[], DesignSimulator | None] | None = None) -> None:
    require(type(port) is int and 0 <= port <= 65535, "web_port_invalid")
    runtime = WorkspaceRuntime(project, create=create, expert_factory=expert_factory, policy=policy,
                               provider_builder=provider_builder, initial_provider=initial_provider,
                               initial_model=initial_model,
                               initial_limit_nano=initial_limit_nano, initial_key=initial_key,
                               timeout_seconds=timeout_seconds, detailed_capture=detailed_capture,
                               simulator_factory=simulator_factory)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler(runtime))
        try:
            print("OpenRTL UI: http://127.0.0.1:" + str(server.server_port), flush=True)
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
    finally:
        runtime.close()
