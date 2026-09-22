"""Local HTTP contract checks with a scripted expert and no provider calls."""

from __future__ import annotations

from dataclasses import replace
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from openrtl.adapters.design_web import WorkspaceRuntime, handler
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignPolicy, ExpertReply
from openrtl.domain.design_session import COACHING_SESSION_SCHEMA, SESSION_SCHEMA, JsonObject, canonical, coaching_initial_state
from tests.test_design_agent import FakeExpert


class DesignWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.expert = FakeExpert()
        self.expert.responses["discovery"] = {"reply": "What width should it use?",
            "specification": None, "engineering_memory": [{"id": "width", "kind": "question",
                "text": "Width is unresolved", "provenance": "agent_proposal"}],
            "questions_asked": ["width"]}
        self.project = Path(self.temporary.name).resolve() / "project"
        self.runtime = WorkspaceRuntime(self.project, create=True,
                                        expert_factory=lambda: self.expert, policy=DesignPolicy())
        self.addCleanup(self.runtime.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.runtime))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method: str, path: str, body: dict[str, object] | None = None,
                *, origin: str | None = None) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {}
        raw = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            raw = json.dumps(body).encode()
        if origin is not None:
            headers["Origin"] = origin
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_create_reconnect_and_origin_protection(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            for asset, expected in (("/", b"OpenRTL workspace"),
                                    ("/app.css", b".workspace"),
                                    ("/app.js", b"function renderSnapshot")):
                connection.request("GET", asset)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn(expected, response.read())
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
        finally:
            connection.close()
        code, first = self.request("GET", "/api/snapshot?cursor=0")
        self.assertEqual(code, 200)
        state = first["state"]
        self.assertIsInstance(state, dict)
        self.assertEqual(state["revision"], 0)
        identifier = uuid.uuid4().hex
        submission = {"message": "A counter", "client_operation_id": identifier, "expected_revision": 0}
        code, _ = self.request("POST", "/api/discussions", submission, origin="https://other.example")
        self.assertEqual(code, 409)
        self.assertEqual(len(self.expert.seen), 0)
        code, accepted = self.request("POST", "/api/discussions", submission,
                                      origin="http://127.0.0.1:" + str(self.server.server_port))
        self.assertEqual(code, 202)
        self.assertEqual(accepted["id"], identifier)
        self.assertEqual(self.project.is_dir(), True)
        # The event loop remains active after the HTTP response and owns this task.
        import time
        for _ in range(50):
            code, operation = self.request("GET", "/api/operations/" + identifier)
            if operation["phase"] == "completed":
                break
            time.sleep(0.01)
        self.assertEqual(code, 200)
        self.assertEqual(operation["phase"], "completed")
        self.assertEqual(len(self.expert.seen), 1)
        code, retried = self.request("POST", "/api/discussions", submission)
        self.assertEqual(code, 202)
        self.assertEqual(retried["phase"], "completed")
        self.assertEqual(len(self.expert.seen), 1)
        code, fresh = self.request("GET", "/api/snapshot?cursor=0")
        self.assertEqual(code, 200)
        self.assertTrue(fresh["state"]["engineering_memory"])
        self.assertNotIn("A counter", json.dumps(fresh))
        self.assertNotIn("What width should", json.dumps(fresh))
        received = next(row for row in fresh["events"] if row["event"] == "operation.received")
        code, detail = self.request("GET", "/api/history/" + str(received["sequence"]))
        self.assertEqual(code, 200)
        self.assertEqual(detail["schema"], "openrtl.web-history-detail.v1")
        self.assertEqual(detail["visibility"]["raw_prompt"], "not_persisted")
        self.assertEqual(detail["visibility"]["raw_reply"], "not_persisted")
        self.assertEqual(detail["visibility"]["hidden_reasoning"], "not_collected")
        self.assertEqual(detail["visibility"]["tool_calls"], 0)
        started = next(row for row in detail["trace"] if row["event"] == "operation.started")
        self.assertEqual(started["fields"]["prompt_version"], "openrtl.design.instructions.v3")
        self.assertEqual(started["fields"]["context_schema"], "openrtl.design-context.v6")
        self.assertNotIn("A counter", json.dumps(detail))
        self.assertNotIn("What width should", json.dumps(detail))
        code, idle = self.request("GET", "/api/snapshot?cursor=" + str(fresh["next_cursor"]))
        self.assertEqual(code, 200)
        self.assertEqual(idle["events"], [])
        self.runtime.close()
        reopened = WorkspaceRuntime(self.project, create=False, expert_factory=lambda: None,
                                    policy=DesignPolicy())
        try:
            restored = reopened.call(lambda workspace: workspace.snapshot(0))
            self.assertEqual(restored["state"]["engineering_memory"],
                             fresh["state"]["engineering_memory"])
            self.assertEqual(reopened.call(lambda workspace: workspace.operation(identifier))["phase"],
                             "completed")
        finally:
            reopened.close()

    def test_browser_create_is_fixed_to_selected_project_path(self) -> None:
        pending_path = Path(self.temporary.name).resolve() / "browser-created"
        pending = WorkspaceRuntime(pending_path, create=False,
                                   expert_factory=lambda: None, policy=DesignPolicy())
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(pending))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("GET", "/api/project")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertFalse(json.loads(response.read())["created"])
            connection.close()
            self.assertFalse(pending_path.exists())
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", "/api/project", body=b"{}",
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            self.assertTrue(json.loads(response.read())["ready"])
            connection.close()
            self.assertTrue(pending_path.is_dir())
            self.assertFalse((Path(self.temporary.name) / "other-project").exists())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            pending.close()

    def test_older_project_requires_explicit_upgrade(self) -> None:
        legacy_path = Path(self.temporary.name).resolve() / "older-session"
        store = DesignSessionStore(legacy_path, create=True)
        store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0",
                                 (canonical(coaching_initial_state()).decode(),))
        store.close()
        runtime = WorkspaceRuntime(legacy_path, create=False, expert_factory=lambda: None,
                                   policy=DesignPolicy())
        try:
            self.assertEqual(runtime.project_state()["schema"], COACHING_SESSION_SCHEMA)
            self.assertFalse(runtime.project_state()["ready"])
            self.assertEqual(runtime.upgrade_project()["schema"], SESSION_SCHEMA)
            self.assertTrue(runtime.project_state()["ready"])
            self.assertEqual(runtime.call(lambda workspace: workspace.snapshot(0))["state"]["schema"],
                             SESSION_SCHEMA)
        finally:
            runtime.close()

    def test_provider_settings_are_redacted_and_unknown_models_fail_closed(self) -> None:
        provider_project = Path(self.temporary.name).resolve() / "provider-settings"
        runtime = WorkspaceRuntime(provider_project, create=True, expert_factory=lambda: None,
                                   provider_builder=lambda provider, model, key, timeout: self.expert,
                                   policy=DesignPolicy())
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler(runtime))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method: str, body: dict[str, object] | None = None,
                    path: str = "/api/provider") -> tuple[int, dict[str, object]]:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                raw = json.dumps(body).encode() if body is not None else None
                connection.request(method, path, body=raw,
                                   headers={"Content-Type": "application/json"} if body is not None else {})
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        try:
            code, catalog = request("GET")
            self.assertEqual(code, 200)
            self.assertEqual(len(catalog["models"]), 9)
            self.assertEqual(catalog["ollama"]["host"], "http://127.0.0.1:11434")
            key = "synthetic-key-value-do-not-persist"
            body = {"provider": "openai", "model": "gpt-5.6-terra", "max_spend_usd": "20.00",
                    "api_key": key, "enabled": True}
            code, selected = request("POST", body)
            self.assertEqual(code, 200)
            self.assertTrue(selected["key_present"])
            code, state = request("GET")
            self.assertEqual(code, 200)
            self.assertEqual(state["selected_model"], "gpt-5.6-terra")
            self.assertEqual(state["selected_provider"], "openai")
            self.assertEqual(state["max_spend_usd"], "20.000000")
            self.assertNotIn(key, json.dumps(state) + json.dumps(selected))
            self.assertNotIn(key.encode(), (provider_project / "session.sqlite3").read_bytes())
            body["model"] = "unknown-model"
            code, rejected = request("POST", body)
            self.assertEqual(code, 409)
            self.assertEqual(rejected["error"], "provider_model_incompatible")
            self.assertEqual(runtime.provider_settings()["selected_model"], "gpt-5.6-terra")
            self.expert.fail = True
            revision = runtime.call(lambda workspace: workspace.snapshot()["state"]["revision"])
            operation_id = uuid.uuid4().hex
            runtime.submit("A wire", operation_id, revision)
            import time
            for _ in range(50):
                operation = runtime.call(lambda workspace: workspace.operation(operation_id))
                if operation["phase"] == "failed":
                    break
                time.sleep(0.01)
            self.assertEqual(operation["error_code"], "expert_invocation_failed")
            code, uncertain = request("GET")
            self.assertEqual(code, 200)
            self.assertTrue(uncertain["uncertain"])
            self.assertGreater(float(uncertain["estimated_spend_usd"]), 0)
            action = {"expected_revision": uncertain["revision"],
                      "decision": "retain_full_reservation"}
            code, done = request("POST", action, "/api/provider/reconcile")
            self.assertEqual(code, 200)
            self.assertFalse(done["uncertain"])
            self.assertEqual(done["estimated_spend_usd"], uncertain["estimated_spend_usd"])
            code, stale = request("POST", action, "/api/provider/reconcile")
            self.assertEqual(code, 409)
            self.assertEqual(stale["error"], "workspace_revision_stale")
            local: dict[str, object] = {
                "provider": "ollama", "model": "qwen3:8b", "max_spend_usd": None,
                "api_key": None, "enabled": False,
            }
            code, configured = request("POST", local)
            self.assertEqual(code, 200)
            self.assertEqual(configured["selected_provider"], "ollama")
            self.assertIsNone(configured["max_spend_usd"])
            code, local_state = request("GET")
            self.assertEqual(code, 200)
            self.assertEqual(local_state["selected_model"], "qwen3:8b")
            self.assertFalse(local_state["key_present"])
            local["model"] = "http://remote/model"
            code, rejected_local = request("POST", local)
            self.assertEqual(code, 409)
            self.assertEqual(rejected_local["error"], "provider_model_incompatible")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            runtime.close()

    def test_timeout_and_capture_settings_validate_before_changing_runtime(self) -> None:
        built: list[tuple[str, str, int]] = []

        def build(provider: str, model: str, key: str | None, timeout: int) -> FakeExpert:
            built.append((provider, model, timeout))
            return self.expert

        # Inject the same provider-construction capability used by the launcher.
        self.runtime.provider_builder = build
        code, initial = self.request("GET", "/api/provider")
        self.assertEqual(code, 200)
        self.assertEqual(initial["timeout_seconds"], 120)
        self.assertFalse(initial["detailed_capture"])
        settings: dict[str, object] = {
            "provider": "ollama", "model": "fixture:local", "max_spend_usd": None,
            "api_key": None, "enabled": True, "timeout_seconds": 240,
            "detailed_capture": True,
        }
        code, configured = self.request("POST", "/api/provider", settings)
        self.assertEqual(code, 200)
        self.assertEqual(built, [("ollama", "fixture:local", 240)])
        self.assertEqual(configured["timeout_seconds"], 240)
        self.assertTrue(configured["detailed_capture"])
        _, baseline = self.request("GET", "/api/provider")
        _, before = self.request("GET", "/api/snapshot")
        for field, values in (("timeout_seconds", [0, 301, True, 1.5, "240"]),
                              ("detailed_capture", [1, "true", []])):
            for value in values:
                with self.subTest(field=field, value=value):
                    code, rejected = self.request("POST", "/api/provider", {**settings, field: value})
                    self.assertEqual(code, 409)
                    self.assertIn("error", rejected)
                    _, current = self.request("GET", "/api/provider")
                    _, snapshot = self.request("GET", "/api/snapshot")
                    self.assertEqual(current, baseline)
                    self.assertEqual(snapshot["state"], before["state"])
                    self.assertEqual(len(built), 1)
        settings.update(timeout_seconds=300, detailed_capture=False)
        code, disabled = self.request("POST", "/api/provider", settings)
        self.assertEqual(code, 200)
        self.assertFalse(disabled["detailed_capture"])
        self.assertEqual(built[-1], ("ollama", "fixture:local", 300))
        code, conversation = self.request("GET", "/api/conversation")
        self.assertEqual(code, 200)
        self.assertFalse(conversation["capture"]["enabled"])
        self.assertEqual(conversation["messages"], [])
        # Legacy clients omit the new fields; omission preserves their current values.
        settings.pop("timeout_seconds")
        settings.pop("detailed_capture")
        code, preserved = self.request("POST", "/api/provider", settings)
        self.assertEqual(code, 200)
        self.assertEqual(preserved["timeout_seconds"], 300)
        self.assertFalse(preserved["detailed_capture"])

    def test_captured_conversation_reopens_without_restoring_capture_consent(self) -> None:
        expert = self.expert

        class SelectedExpert:
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await expert.generate(stage, context, operation_id)
                return replace(reply, provider="ollama", model="fixture:local")

        self.runtime.provider_builder = lambda provider, model, key, timeout: SelectedExpert()
        code, _ = self.request("POST", "/api/provider", {
            "provider": "ollama", "model": "fixture:local", "max_spend_usd": None,
            "api_key": None, "enabled": True, "timeout_seconds": 180, "detailed_capture": True,
        })
        self.assertEqual(code, 200)
        _, snapshot = self.request("GET", "/api/snapshot")
        identifier = uuid.uuid4().hex
        code, _ = self.request("POST", "/api/discussions", {
            "message": "Synthetic captured circuit request", "client_operation_id": identifier,
            "expected_revision": snapshot["state"]["revision"],
        })
        self.assertEqual(code, 202)
        import time
        for _ in range(50):
            code, operation = self.request("GET", "/api/operations/" + identifier)
            if operation["phase"] in ("completed", "failed", "cancelled", "reconciliation_needed"):
                break
            time.sleep(0.01)
        self.assertEqual(operation["phase"], "completed", operation)
        code, captured = self.request("GET", "/api/conversation")
        self.assertEqual(code, 200)
        self.assertEqual([row["kind"] for row in captured["messages"]], ["user", "agent"])
        self.assertEqual(captured["messages"][0]["text"], "Synthetic captured circuit request")
        self.assertEqual(captured["messages"][1]["text"], "What width should it use?")
        self.assertEqual(len(self.expert.seen), 1)
        _, snapshot = self.request("GET", "/api/snapshot")
        self.assertNotIn("Synthetic captured circuit request", json.dumps(snapshot))
        self.runtime.close()
        reopened = WorkspaceRuntime(self.project, create=False, expert_factory=lambda: None,
                                    policy=DesignPolicy())
        try:
            restored = reopened.call(lambda workspace: workspace.conversation())
            self.assertEqual(restored["messages"], captured["messages"])
            self.assertFalse(restored["capture"]["enabled"])
            self.assertTrue(restored["capture"]["available"])
            self.assertEqual(reopened.provider_settings()["timeout_seconds"], 120)
            self.assertEqual(reopened.call(lambda workspace: workspace.operation(identifier))["reply"],
                             "What width should it use?")
            self.assertEqual(len(self.expert.seen), 1)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
