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
from openrtl.application.design_agent import DesignAgent, DesignExpert, DesignPolicy
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import JsonObject, SESSION_SCHEMA, canonical, require


class WorkspaceRuntime:
    """Own SQLite and agent tasks on one loop, independent of request threads."""

    def __init__(self, project: Path, *, create: bool, expert_factory: Callable[[], DesignExpert | None],
                 policy: DesignPolicy) -> None:
        self.project = safe_root(project)
        self.expert_factory = expert_factory
        self.policy = policy
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
        expert = self.expert_factory()
        self.workspace = DesignWorkspace(DesignAgent(self.store, expert, policy=self.policy))
        self.workspace.reconcile_interrupted()

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

        def _body(self, keys: set[str]) -> JsonObject:
            require(self.headers.get("Content-Type") == "application/json", "web_content_type_invalid")
            size = self.headers.get("Content-Length")
            require(size is not None and size.isascii() and size.isdecimal() and 1 <= int(size) <= 20_000,
                    "web_request_size_invalid")
            raw = self.rfile.read(int(size))
            require(len(raw) == int(size), "web_request_incomplete")
            value = json.loads(raw)
            require(isinstance(value, dict) and set(value) == keys, "web_request_fields_invalid")
            return cast(JsonObject, value)

        def _perform(self, action: Callable[[], None]) -> None:
            try:
                self._headers()
                action()
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                self._json(409, {"error": "invalid_or_stale_request"})
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
                if parsed.path == "/api/project":
                    self._json(200, runtime.project_state())
                    return
                if parsed.path == "/api/review":
                    query = parse_qs(parsed.query, strict_parsing=True)
                    require(set(query) == {"kind"} and len(query["kind"]) == 1, "web_query_invalid")
                    self._json(200, runtime.call(lambda workspace: workspace.review(query["kind"][0])))
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
                if parsed.path == "/api/project":
                    self._body(set())
                    self._json(201, runtime.create_project())
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
          policy: DesignPolicy) -> None:
    require(type(port) is int and 0 <= port <= 65535, "web_port_invalid")
    runtime = WorkspaceRuntime(project, create=create, expert_factory=expert_factory, policy=policy)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), handler(runtime))
        try:
            print("OpenRTL UI: http://127.0.0.1:" + str(server.server_port), flush=True)
            server.serve_forever(poll_interval=0.2)
        finally:
            server.server_close()
    finally:
        runtime.close()
