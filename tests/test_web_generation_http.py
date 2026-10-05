"""Loopback generation contracts with scripted experts, never live providers."""

from __future__ import annotations

import asyncio
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
from typing import cast
import unittest
import uuid

from openrtl.adapters.design_web import WorkspaceRuntime, handler
from openrtl.application.design_agent import DesignPolicy, ExpertReply
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import JsonObject, STAGES
from tests.test_design_agent import FakeExpert, FakeSimulator
from tests.test_design_conversation import ready_spec


class WebGenerationHTTPTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "project"
        self.expert = FakeExpert()
        self.simulator = FakeSimulator()
        self.start(create=True)
        self.addCleanup(self.stop)
        self.runtime.call(lambda workspace: workspace.agent.propose(ready_spec()))

    def start(self, *, create: bool) -> None:
        self.runtime = WorkspaceRuntime(
            self.project, create=create, expert_factory=lambda: self.expert,
            simulator_factory=lambda: self.simulator, policy=DesignPolicy())
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler(self.runtime))
        except BaseException:
            self.runtime.close()
            raise
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.close()

    def request(self, method: str, path: str, body: object = None,
                *, headers: dict[str, str] | None = None,
                raw: bytes | None = None) -> tuple[int, JsonObject]:
        selected_headers = {"Content-Type": "application/json"} if body is not None else {}
        selected_headers.update(headers or {})
        if body is not None:
            raw = json.dumps(body).encode()
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=selected_headers)
            response = connection.getresponse()
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            return response.status, cast(JsonObject, json.loads(response.read()))
        finally:
            connection.close()

    def snapshot(self) -> JsonObject:
        status, snapshot = self.request("GET", "/api/snapshot")
        self.assertEqual(status, 200)
        return snapshot

    def approve(self) -> None:
        status, card = self.request("GET", "/api/review?kind=specification")
        self.assertEqual(status, 200)
        status, approved = self.request("POST", "/api/approve", {
            "kind": "specification", "expected_revision": card["revision"],
            "state_digest": card["state_digest"], "payload_digest": card["payload_digest"]})
        self.assertEqual(status, 200, approved)
        self.assertEqual(approved["status"], "building")

    def plan(self) -> JsonObject:
        status, plan = self.request("GET", "/api/generation/plan")
        self.assertEqual(status, 200, plan)
        return plan

    def submission(self, plan: JsonObject) -> JsonObject:
        return {"client_operation_id": uuid.uuid4().hex,
                "expected_revision": plan["revision"], "plan_digest": plan["plan_digest"]}

    def finished(self, identifier: str) -> JsonObject:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status, operation = self.request("GET", "/api/operations/" + identifier)
            self.assertEqual(status, 200)
            if operation["phase"] not in ("queued", "active", "cancellation_requested"):
                return operation
            time.sleep(0.01)
        self.fail("Scripted generation did not finish within five seconds")

    def test_zero_questions_requires_exact_review_and_approval_before_generation(self) -> None:
        before = self.snapshot()
        self.assertEqual(before["state"]["spec"]["questions"], [])
        self.assertEqual(before["workflow"]["action"], "review_specification")
        self.assertIsNone(before["workflow"]["plan"])
        self.assertIsNone(before["workflow"]["stage"])
        self.assertIn("review", before["workflow"]["message"])
        for method, path, body in (
            ("GET", "/api/generation/plan", None),
            ("POST", "/api/generations", {"client_operation_id": uuid.uuid4().hex,
                "expected_revision": before["state"]["revision"], "plan_digest": "sha256:unapproved"}),
        ):
            status, rejected = self.request(method, path, body)
            self.assertEqual(status, 409)
            self.assertEqual(rejected, {"error": "workspace_generation_not_ready"})
        status, card = self.request("GET", "/api/review?kind=specification")
        self.assertEqual(status, 200)
        status, rejected = self.request("POST", "/api/approve", {
            "kind": "specification", "expected_revision": card["revision"],
            "state_digest": card["state_digest"], "payload_digest": "sha256:not-displayed"})
        self.assertEqual((status, rejected), (409, {"error": "invalid_or_stale_request"}))
        self.assertEqual(self.snapshot()["state"], before["state"])
        self.assertEqual(self.expert.seen, [])
        self.approve()
        approved = self.snapshot()
        self.assertEqual(approved["state"]["stage"], 0)
        self.assertEqual(approved["state"]["calls"], 0)
        self.assertEqual(approved["workflow"]["action"], "generate")
        self.assertEqual(approved["workflow"]["stage"], "architecture")
        self.assertEqual(approved["workflow"]["plan"], self.plan())

    def test_generation_plan_is_read_only_and_bound_to_displayed_state(self) -> None:
        self.approve()
        before = self.snapshot()
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        self.assertEqual(plan["schema"], "openrtl.web-generation-plan.v1")
        self.assertEqual(plan["revision"], before["state"]["revision"])
        self.assertEqual(plan["input_digest"], before["design_input_digest"])
        self.assertEqual(plan["approved_spec"], before["state"]["approved_spec"])
        self.assertEqual(plan["status"], "building")
        self.assertEqual(self.snapshot()["state"], before["state"])
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(self.simulator.calls, [])

    def test_generation_routes_enforce_same_origin_and_exact_host(self) -> None:
        self.approve()
        body = self.submission(self.plan())
        before = self.snapshot()["state"]
        for headers in ({"Origin": "https://other.example"},
                        {"Host": "other.example"},
                        {"Host": "localhost:" + str(self.server.server_port)}):
            for method, path, selected in (("GET", "/api/generation/plan", None),
                                           ("POST", "/api/generations", body)):
                with self.subTest(headers=headers, method=method):
                    self.assertEqual(self.request(method, path, selected, headers=headers),
                                     (409, {"error": "invalid_or_stale_request"}))
        self.assertEqual(self.snapshot()["state"], before)
        self.assertEqual(self.expert.seen, [])

    def test_generation_request_rejects_extra_missing_and_malformed_fields(self) -> None:
        self.approve()
        body = self.submission(self.plan())
        before = self.snapshot()["state"]
        invalid_bodies = [
            {**body, "approved": True}, {**body, "stage": "simulation"},
            {key: value for key, value in body.items() if key != "plan_digest"},
            {**body, "expected_revision": True}, {**body, "expected_revision": -1},
            {**body, "expected_revision": str(body["expected_revision"])},
            {**body, "plan_digest": None}, {**body, "client_operation_id": "not-a-uuid"},
            {**body, "client_operation_id": None}, {**body, "client_operation_id": 42},
            [body],
        ]
        for invalid in invalid_bodies:
            with self.subTest(body=invalid):
                self.assertEqual(self.request("POST", "/api/generations", invalid),
                                 (409, {"error": "invalid_or_stale_request"}))
        self.assertEqual(self.request("POST", "/api/generations", body,
                                      headers={"Content-Type": "text/plain"}),
                         (409, {"error": "invalid_or_stale_request"}))
        self.assertEqual(self.request("POST", "/api/generations", raw=b"{not-json",
                                      headers={"Content-Type": "application/json"}),
                         (409, {"error": "invalid_or_stale_request"}))
        for method, path, selected in (("GET", "/api/generation/plan?stage=rtl", None),
                                      ("POST", "/api/generations?stage=rtl", body)):
            self.assertEqual(self.request(method, path, selected),
                             (409, {"error": "invalid_or_stale_request"}))
        self.assertEqual(self.snapshot()["state"], before)
        self.assertEqual(self.expert.seen, [])

    def test_stale_plan_and_revision_cannot_start_a_different_stage(self) -> None:
        self.approve()
        body = self.submission(self.plan())
        before = self.snapshot()["state"]
        self.assertEqual(self.request("POST", "/api/generations", {**body, "plan_digest": "sha256:changed"}),
                         (409, {"error": "generation_plan_stale"}))
        self.runtime.call(lambda workspace: workspace.agent.detail("brief"))
        changed = self.snapshot()["state"]
        self.assertGreater(changed["revision"], before["revision"])
        self.assertEqual(self.request("POST", "/api/generations", body),
                         (409, {"error": "workspace_revision_stale"}))
        self.assertEqual(self.request("POST", "/api/generations", {**body, "expected_revision": changed["revision"]}),
                         (409, {"error": "generation_plan_stale"}))
        self.assertEqual(self.snapshot()["state"], changed)
        self.assertEqual(self.expert.seen, [])

    def test_active_completed_and_restarted_operation_replay_never_redispatches(self) -> None:
        class HeldExpert(FakeExpert):
            def __init__(self) -> None:
                super().__init__()
                self.started = threading.Event()
                self.release: asyncio.Event | None = None

            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                self.release = asyncio.Event()
                self.started.set()
                await self.release.wait()
                return await super().generate(stage, context, operation_id)

        held = HeldExpert()
        def select(workspace: DesignWorkspace) -> JsonObject:
            workspace.agent.expert = held
            return {}
        self.runtime.call(select)
        self.approve()
        body = self.submission(self.plan())
        status, accepted = self.request("POST", "/api/generations", body,
            headers={"Origin": "http://127.0.0.1:" + str(self.server.server_port)})
        self.assertEqual(status, 202)
        self.assertEqual(accepted["id"], body["client_operation_id"])
        self.assertTrue(held.started.wait(timeout=5))
        try:
            status, duplicate = self.request("POST", "/api/generations", body)
            self.assertEqual((status, duplicate["phase"]), (202, "active"))
            self.assertEqual(self.snapshot()["state"]["calls"], 1)
        finally:
            assert held.release is not None
            self.runtime.loop.call_soon_threadsafe(held.release.set)
        self.assertEqual(self.finished(body["client_operation_id"])["phase"], "completed")
        before = self.snapshot()["state"]
        status, duplicate = self.request("POST", "/api/generations", body)
        self.assertEqual((status, duplicate["phase"]), (202, "completed"))
        self.assertEqual(len(held.seen), 1)
        self.stop()
        self.start(create=False)
        status, duplicate = self.request("POST", "/api/generations", body)
        self.assertEqual((status, duplicate["phase"]), (202, "completed"))
        self.assertEqual(self.snapshot()["state"], before)
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(self.request("POST", "/api/generations", {**body, "plan_digest": "sha256:different"}),
                         (409, {"error": "invalid_or_stale_request"}))

    def test_each_generation_advances_once_and_final_boundary_never_simulates(self) -> None:
        self.approve()
        for index, stage in enumerate(STAGES):
            plan = self.plan()
            self.assertEqual(plan["stage"], stage)
            body = self.submission(plan)
            status, _ = self.request("POST", "/api/generations", body)
            self.assertEqual(status, 202)
            operation = self.finished(body["client_operation_id"])
            self.assertEqual(operation["phase"], "completed", operation)
            self.assertEqual(self.snapshot()["state"]["stage"], index + 1)
            self.assertEqual(self.simulator.calls, [])
        final = self.snapshot()
        self.assertEqual(final["workflow"]["action"], "simulate")
        self.assertIsNone(final["workflow"]["plan"])
        self.assertEqual(self.request("GET", "/api/generation/plan"),
                         (409, {"error": "workspace_generation_not_ready"}))
        self.assertEqual(self.request("POST", "/api/generations", {
            "client_operation_id": uuid.uuid4().hex, "expected_revision": final["state"]["revision"],
            "plan_digest": body["plan_digest"]}), (409, {"error": "workspace_generation_not_ready"}))
        self.assertEqual([stage for stage, _ in self.expert.seen], list(STAGES))
        self.assertEqual(self.simulator.calls, [])
        self.assertEqual(self.snapshot()["state"], final["state"])

    def test_failed_generation_reports_safe_error_without_exception_text_or_retry(self) -> None:
        self.approve()
        self.expert.fail = True
        body = self.submission(self.plan())
        self.assertEqual(self.request("POST", "/api/generations", body)[0], 202)
        operation = self.finished(body["client_operation_id"])
        self.assertEqual(operation["phase"], "failed")
        self.assertEqual(operation["error_code"], "expert_invocation_failed")
        snapshot = self.snapshot()
        self.assertEqual(snapshot["state"]["stage"], 0)
        status, history = self.request("GET", "/api/history/" + str(snapshot["state"]["revision"]))
        self.assertEqual(status, 200)
        self.assertNotIn("private prompt must not be logged", json.dumps([operation, snapshot, history]))
        self.assertEqual(self.request("POST", "/api/generations", body)[1]["phase"], "failed")
        self.assertEqual(len(self.expert.seen), 1)


if __name__ == "__main__":
    unittest.main()
