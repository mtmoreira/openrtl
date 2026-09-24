"""Provider-free workspace contracts; no real provider or simulation is used."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
import uuid

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent, ExpertReply
from openrtl.application.design_workspace import DesignWorkspace
from tests.test_design_agent import FakeExpert
from tests.test_design_conversation import ready_spec


class DesignWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = DesignSessionStore(Path(self.temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.expert.responses["discovery"] = {
            "reply": "What width should the counter have?", "specification": None,
            "engineering_memory": [{"id": "counter.width", "kind": "question",
                "text": "Counter width is unresolved", "provenance": "agent_proposal"}],
            "questions_asked": ["counter.width"],
        }
        self.agent = DesignAgent(self.store, self.expert)
        self.workspace = DesignWorkspace(self.agent)

    def test_durable_idempotency_reconnect_and_stale_mutation(self) -> None:
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            queued = await self.workspace.submit_discussion("Design a counter", client_operation_id=identifier,
                                                            expected_revision=0)
            self.assertEqual(queued["phase"], "queued")
            with self.assertRaisesRegex(ValueError, "writer_busy"):
                await self.workspace.submit_discussion("A second request", client_operation_id=uuid.uuid4().hex,
                                                       expected_revision=self.store.read()["revision"])
            self.assertEqual(len(self.expert.seen), 0)
            await self.workspace._tasks[identifier]
            completed = self.workspace.operation(identifier)
            self.assertEqual(completed["phase"], "completed")
            self.assertEqual(completed["reply"],
                             "Please clarify these design decisions:\n\n1. Counter width is unresolved")
            self.assertEqual(len(self.expert.seen), 1)
            reopened = DesignWorkspace(self.agent)
            retried = await reopened.submit_discussion("Design a counter", client_operation_id=identifier,
                                                       expected_revision=0)
            self.assertEqual(retried["phase"], "completed")
            self.assertIsNone(retried["reply"])
            self.assertEqual(len(self.expert.seen), 1)
            with self.assertRaisesRegex(ValueError, "id_conflict"):
                await reopened.submit_discussion("Different private text", client_operation_id=identifier,
                                                   expected_revision=0)
            with self.assertRaisesRegex(ValueError, "revision_stale"):
                await reopened.submit_discussion("Another design", client_operation_id=uuid.uuid4().hex,
                                                   expected_revision=0)
            first = reopened.snapshot(0)
            self.assertGreater(first["next_cursor"], 0)
            self.assertEqual(reopened.snapshot(first["next_cursor"])["events"], ())
            self.assertNotIn("Design a counter", str(self.store.read()))
            self.assertNotIn("What width should", str(self.store.events()))
        asyncio.run(scenario())

    def test_discovery_validation_reports_safe_rule_without_persisting_response(self) -> None:
        self.expert.responses["discovery"] = {
            "reply": "A proposal is ready", "specification": {
                "title": "PRIVATE SYNTHETIC RESPONSE", "top": "counter", "behavior": "Count",
                "clock_reset": "Reset", "requirements": [{"id": "count", "text": "Count",
                    "acceptance": "Check the count"}], "ports": [], "questions": [],
                "assumptions": [], "readiness": {"schema": "openrtl.design-readiness.v1", "items": []}},
            "questions_asked": [], "engineering_memory": [],
        }
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_discussion("Synthetic request", client_operation_id=identifier,
                                                   expected_revision=0)
            await self.workspace._tasks[identifier]
            self.assertEqual(self.workspace.operation(identifier)["error_code"],
                             "expert_output_readiness_invalid")
            self.assertEqual(self.store.read()["last_error"], "expert_output_invalid")
            self.assertNotIn("PRIVATE SYNTHETIC RESPONSE", str(self.store.read()) + str(self.store.events()))
            detail = self.workspace.history(self.store.read()["revision"])
            failed = next(row for row in detail["trace"] if row["event"] == "operation.failed")
            self.assertEqual(failed["fields"]["validation_code"], "readiness_categories_missing")
        asyncio.run(scenario())

    def test_cancellation_retains_uncertain_provider_intent(self) -> None:
        class SlowExpert:
            async def generate(self, stage: str, context: dict[str, object], operation_id: str) -> ExpertReply:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
        self.agent.expert = SlowExpert()
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_discussion("Synthetic private request", client_operation_id=identifier,
                                                   expected_revision=0)
            await asyncio.sleep(0.01)
            self.assertEqual(self.workspace.request_cancellation(identifier)["phase"],
                             "cancellation_requested")
            await self.workspace._tasks[identifier]
            self.assertEqual(self.workspace.operation(identifier)["phase"], "reconciliation_needed")
            self.assertIsNotNone(self.store.read()["active"])
            self.assertEqual(self.store.read()["calls"], 1)
            self.assertEqual(DesignWorkspace(self.agent).operation(identifier)["phase"],
                             "reconciliation_needed")
            resumed = DesignWorkspace(self.agent)
            resumed.reconcile_interrupted()
            self.assertEqual(self.store.read()["workspace_operations"][identifier]["phase"],
                             "reconciliation_needed")
        asyncio.run(scenario())

    def test_queued_cancellation_finishes_without_provider_dispatch(self) -> None:
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_discussion("Synthetic queued request", client_operation_id=identifier,
                                                   expected_revision=0)
            self.assertEqual(self.workspace.request_cancellation(identifier)["phase"], "cancelled")
            await asyncio.sleep(0)
            self.assertEqual(self.store.read()["calls"], 0)
            self.assertIsNone(self.store.read()["active"])
            self.assertEqual(len(self.expert.seen), 0)
        asyncio.run(scenario())

    def test_reopened_queued_request_is_cancelled_without_dispatch(self) -> None:
        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_discussion("Synthetic queued request", client_operation_id=identifier,
                                                   expected_revision=0)
            resumed = DesignWorkspace(self.agent)
            resumed.reconcile_interrupted()
            await self.workspace._tasks[identifier]
            self.assertEqual(resumed.operation(identifier)["phase"], "cancelled")
            self.assertEqual(self.store.read()["calls"], 0)
            self.assertEqual(len(self.expert.seen), 0)
        asyncio.run(scenario())

    def test_review_card_requires_exact_current_revision_and_payload(self) -> None:
        self.agent.propose(ready_spec())
        old_card = self.workspace.review("specification")
        self.agent.detail("brief")
        with self.assertRaisesRegex(ValueError, "revision_stale"):
            self.workspace.approve_review("specification", expected_revision=old_card["revision"],
                                          state_digest=old_card["state_digest"],
                                          payload_digest=old_card["payload_digest"])
        card = self.workspace.review("specification")
        approved = self.workspace.approve_review("specification", expected_revision=card["revision"],
                                                 state_digest=card["state_digest"],
                                                 payload_digest=card["payload_digest"])
        self.assertEqual(approved["status"], "building")


if __name__ == "__main__":
    unittest.main()
