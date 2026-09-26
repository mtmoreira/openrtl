"""Web engineering handoff with synthetic experts, never live provider evidence."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import replace
from pathlib import Path
import tempfile
from typing import cast
import unittest
from unittest.mock import patch
import uuid

from agentrig.core.errors import AgentRigError, Failure, FailureKind
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.adapters.design_web import WorkspaceRuntime
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply, design_input_digest
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_session import JsonObject, STAGES, canonical, content_digest
from openrtl.domain.provider_controls import request_reserve_nano
from tests.test_design_agent import FakeExpert, FakeSimulator, manifest, specification
from tests.test_design_conversation import ready_spec


class WebGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "project"
        self.store = DesignSessionStore(self.root, create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.simulator = FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)
        self.workspace = DesignWorkspace(self.agent)

    def approve_specification(self) -> None:
        self.agent.propose(ready_spec())
        card = self.workspace.review("specification")
        self.workspace.approve_review("specification", expected_revision=card["revision"],
            state_digest=card["state_digest"], payload_digest=card["payload_digest"])

    async def generate_one(self) -> JsonObject:
        plan = self.workspace.generation_plan()
        identifier = uuid.uuid4().hex
        queued = await self.workspace.submit_generation(client_operation_id=identifier,
            expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
        self.assertEqual(queued["phase"], "queued")
        await self.workspace._tasks[identifier]
        operation = self.workspace.operation(identifier)
        self.assertEqual(operation["phase"], "completed")
        return operation

    async def generate_baseline(self) -> None:
        self.approve_specification()
        for _ in STAGES:
            await self.generate_one()

    def test_complete_draft_requires_displayed_approval_before_generation(self) -> None:
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "discuss")
        self.agent.propose(ready_spec())
        before = self.store.read()
        workflow = self.workspace.snapshot()["workflow"]
        self.assertEqual(workflow["action"], "review_specification")
        self.assertIsNone(workflow["plan"])
        with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
            self.workspace.generation_plan()

        async def scenario() -> None:
            with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
                await self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                    expected_revision=before["revision"], plan_digest=content_digest({}))
        asyncio.run(scenario())
        self.assertEqual(self.store.read(), before)
        self.assertIsNone(before["approved_spec"])
        self.assertEqual(self.expert.seen, [])
        card = self.workspace.review("specification")
        self.workspace.approve_review("specification", expected_revision=card["revision"],
            state_digest=card["state_digest"], payload_digest=card["payload_digest"])
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "generate")
        self.assertEqual(self.store.read()["stage"], 0)
        self.assertEqual(self.expert.seen, [])

    def test_no_questions_is_not_sufficient_when_readiness_is_unresolved(self) -> None:
        spec = ready_spec()
        spec["readiness"]["items"][2]["status"] = "unresolved"
        self.agent.propose(spec)
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "discuss")
        card = self.workspace.review("specification")
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.workspace.approve_review("specification", expected_revision=card["revision"],
                state_digest=card["state_digest"], payload_digest=card["payload_digest"])
        with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
            self.workspace.generation_plan()
        self.assertEqual(self.store.read()["calls"], 0)

    def test_plan_binds_exact_reviewed_state_and_read_has_no_effects(self) -> None:
        self.approve_specification()
        before = self.store.read()
        plan = self.workspace.generation_plan()
        self.assertEqual(set(plan), {"schema", "revision", "input_digest", "approved_spec",
                                     "status", "stage", "plan_digest"})
        self.assertEqual(plan["schema"], "openrtl.web-generation-plan.v1")
        self.assertEqual(plan["revision"], before["revision"])
        self.assertEqual(plan["input_digest"], design_input_digest(before))
        self.assertEqual(plan["approved_spec"], before["approved_spec"])
        self.assertEqual(plan["status"], "building")
        self.assertEqual(plan["stage"], "architecture")
        self.assertEqual(plan["plan_digest"], content_digest({
            key: value for key, value in plan.items() if key != "plan_digest"}))
        self.assertEqual(self.workspace.snapshot()["workflow"]["plan"], plan)
        self.assertEqual(self.workspace.generation_plan(), plan)
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.expert.seen, [])

    def test_exactly_one_stage_and_durable_completed_replay_after_reopen(self) -> None:
        self.approve_specification()
        plan = self.workspace.generation_plan()
        identifier = uuid.uuid4().hex

        async def scenario() -> None:
            queued = await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            self.assertEqual(queued["phase"], "queued")
            self.assertEqual(self.expert.seen, [])
            replay = await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            self.assertEqual(replay["phase"], "queued")
            await self.workspace._tasks[identifier]
        asyncio.run(scenario())
        self.assertEqual([stage for stage, _ in self.expert.seen], ["architecture"])
        self.assertEqual(self.simulator.calls, [])
        self.assertEqual(self.store.read()["stage"], 1)
        self.assertEqual(set(self.store.read()["files"]), {"docs/architecture.md"})
        self.store.close()
        reopened = DesignSessionStore(self.root)
        self.addCleanup(reopened.close)
        workspace = DesignWorkspace(DesignAgent(reopened))
        before = reopened.read()
        replay = asyncio.run(workspace.submit_generation(client_operation_id=identifier,
            expected_revision=plan["revision"], plan_digest=plan["plan_digest"]))
        self.assertEqual(replay["phase"], "completed")
        self.assertEqual(reopened.read(), before)
        self.assertEqual(len(self.expert.seen), 1)

    def test_revision_plan_and_id_conflicts_never_dispatch(self) -> None:
        self.approve_specification()
        plan = self.workspace.generation_plan()

        async def scenario() -> None:
            for revision, digest, error in (
                (plan["revision"] - 1, plan["plan_digest"], "workspace_revision_stale"),
                (plan["revision"], content_digest({"different": True}), "generation_plan_stale"),
                (True, plan["plan_digest"], "expected_revision_invalid"),
                (plan["revision"], None, "generation_plan_digest_invalid"),
            ):
                before = self.store.read()
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    await self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                        expected_revision=revision, plan_digest=cast(str, digest))
                self.assertEqual(self.store.read(), before)
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            with self.assertRaisesRegex(ValueError, "client_operation_id_conflict"):
                await self.workspace.submit_generation(client_operation_id=identifier,
                    expected_revision=plan["revision"], plan_digest=content_digest({}))
            self.workspace.request_cancellation(identifier)
            await asyncio.sleep(0)
        asyncio.run(scenario())
        self.assertEqual(self.expert.seen, [])

    def test_old_plan_cannot_be_reused_at_new_revision(self) -> None:
        self.approve_specification()
        old = self.workspace.generation_plan()
        self.agent.detail("brief")
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "generation_plan_stale"):
            asyncio.run(self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                expected_revision=before["revision"], plan_digest=old["plan_digest"]))
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.expert.seen, [])

    def test_busy_writer_blocks_second_request_and_plan(self) -> None:
        self.approve_specification()
        plan = self.workspace.generation_plan()

        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            with self.assertRaisesRegex(ValueError, "workspace_writer_busy_or_unreconciled"):
                self.workspace.generation_plan()
            self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "blocked")
            with self.assertRaisesRegex(ValueError, "workspace_writer_busy_or_unreconciled"):
                await self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                    expected_revision=self.store.read()["revision"], plan_digest=plan["plan_digest"])
            await self.workspace._tasks[identifier]
        asyncio.run(scenario())
        self.assertEqual(len(self.expert.seen), 1)

    def test_missing_provider_and_uncertain_spend_fail_before_queue(self) -> None:
        self.approve_specification()
        self.agent.expert = None
        with self.assertRaisesRegex(ValueError, "expert_not_configured"):
            self.workspace.generation_plan()
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "blocked")
        self.agent.expert = self.expert
        state = self.store.read()
        updated = copy.deepcopy(state)
        updated["provider"]["uncertain"] = True
        self.store.save(state, updated, "operation.failed", {"error_code": "expert_invocation_failed"})
        with self.assertRaisesRegex(ValueError, "provider_spend_uncertain"):
            self.workspace.generation_plan()
        self.assertEqual(self.store.read()["workspace_operations"], {})
        self.assertEqual(self.expert.seen, [])

    def test_saved_active_operation_requires_reconciliation(self) -> None:
        self.approve_specification()
        state = self.store.read()
        self.store.save(state, {**state, "active": {"id": uuid.uuid4().hex,
            "kind": "expert", "stage": "architecture"}}, "operation.started")
        with self.assertRaisesRegex(ValueError, "interrupted_operation_requires_reconciliation"):
            self.workspace.generation_plan()
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "blocked")
        self.assertEqual(self.expert.seen, [])

    def test_openai_request_reservation_blocks_without_effects_until_budget_is_sufficient(self) -> None:
        self.approve_specification()
        model = "gpt-5.6-terra"
        reserve = request_reserve_nano(model, self.agent.policy.max_output_tokens)
        self.agent.configure_provider(model, reserve - 1)
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "provider_spend_budget_exhausted"):
            self.workspace.generation_plan()
        with self.assertRaisesRegex(ValueError, "provider_spend_budget_exhausted"):
            asyncio.run(self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                expected_revision=before["revision"], plan_digest=content_digest({})))
        workflow = self.workspace.snapshot()["workflow"]
        self.assertEqual(workflow["action"], "blocked")
        self.assertIn("Provider settings", workflow["message"])
        self.assertIsNone(workflow["plan"])
        self.assertEqual(self.store.read(), before)
        self.assertEqual(self.expert.seen, [])
        self.agent.configure_provider(model, reserve)
        enough = self.store.read()
        plan = self.workspace.generation_plan()
        self.assertEqual(plan["stage"], "architecture")
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "generate")
        self.assertEqual(self.store.read(), enough)
        self.assertEqual(enough["calls"], 0)
        self.assertEqual(enough["provider"]["spent_nano_usd"], 0)
        self.assertEqual(enough["workspace_operations"], {})
        self.assertEqual(self.expert.seen, [])

    def test_configured_ollama_stage_needs_no_usd_budget(self) -> None:
        class LocalExpert(FakeExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                return replace(reply, provider="ollama", model="synthetic:1b")
        self.expert = LocalExpert()
        self.agent.expert = self.expert
        self.approve_specification()
        self.agent.configure_provider("ollama/synthetic:1b", None)
        before = self.store.read()
        self.assertEqual(self.workspace.generation_plan()["stage"], "architecture")
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "generate")
        self.assertEqual(self.store.read(), before)
        asyncio.run(self.generate_one())
        after = self.store.read()
        self.assertEqual([stage for stage, _ in self.expert.seen], ["architecture"])
        self.assertIsNone(after["provider"]["limit_nano_usd"])
        self.assertEqual(after["provider"]["spent_nano_usd"], 0)
        self.assertIsNone(after["provider"]["pending"])
        self.assertFalse(after["provider"]["uncertain"])
        self.assertEqual(self.simulator.calls, [])

    def test_ollama_reference_model_timeout_retries_with_longer_deadline_without_reset(self) -> None:
        attempts: list[tuple[str, int, JsonObject]] = []
        builds: list[int] = []

        class LocalExpert(FakeExpert):
            def __init__(self, timeout: int) -> None:
                super().__init__()
                self.timeout_seconds = timeout

            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                attempts.append((stage, self.timeout_seconds, copy.deepcopy(context)))
                if stage == "reference_model" and self.timeout_seconds == 300:
                    raise AgentRigError(Failure(kind=FailureKind.DEADLINE_EXCEEDED,
                        code="ollama.request_timeout", message="Synthetic provider timeout"))
                reply = await super().generate(stage, context, operation_id)
                return replace(reply, provider="ollama", model="synthetic:1b")

        def build(provider: str, model: str, key: str | None, timeout: int) -> LocalExpert:
            self.assertEqual((provider, model, key), ("ollama", "synthetic:1b", None))
            builds.append(timeout)
            return LocalExpert(timeout)

        runtime = WorkspaceRuntime(self.root.parent / "retry-project", create=True,
            expert_factory=lambda: None, provider_builder=build, policy=DesignPolicy(),
            initial_provider="ollama", initial_model="synthetic:1b", timeout_seconds=300,
            detailed_capture=True, simulator_factory=lambda: self.simulator)
        self.addCleanup(runtime.close)

        def approve(workspace: DesignWorkspace) -> JsonObject:
            workspace.agent.propose(ready_spec())
            card = workspace.review("specification")
            return workspace.approve_review("specification", expected_revision=card["revision"],
                state_digest=card["state_digest"], payload_digest=card["payload_digest"])

        def advance() -> JsonObject:
            plan = runtime.call(lambda workspace: workspace.generation_plan())
            identifier = uuid.uuid4().hex
            self.assertEqual(runtime.generate(identifier, plan["revision"], plan["plan_digest"])["phase"],
                             "queued")

            async def settle() -> JsonObject:
                assert runtime.workspace is not None
                await runtime.workspace._tasks[identifier]
                return runtime.workspace.operation(identifier)
            return runtime._await(settle())

        runtime.call(approve)
        for _ in range(2):
            self.assertEqual(advance()["phase"], "completed")
        saved = runtime.call(lambda workspace: workspace.agent.store.read())
        self.assertEqual(saved["stage"], 2)
        self.assertEqual(set(saved["files"]), {"docs/architecture.md", "docs/verification-plan.md"})

        failed_operation = advance()
        self.assertEqual(failed_operation["phase"], "failed")
        self.assertEqual(failed_operation["error_code"], "provider_timeout")
        failed = runtime.call(lambda workspace: workspace.agent.store.read())
        self.assertEqual((failed["stage"], failed["status"], failed["calls"]), (2, "building", 3))
        self.assertEqual(failed["files"], saved["files"])
        self.assertEqual(failed["approved_spec"], saved["approved_spec"])
        self.assertIsNone(failed["active"])
        self.assertIsNone(failed["provider"]["pending"])
        self.assertFalse(failed["provider"]["uncertain"])
        self.assertIsNone(failed["simulation"])
        detail = runtime.call(lambda workspace: workspace.history(failed["revision"]))
        failure = next(row["payload"] for row in detail["details"]
                       if row["category"] == "provider_failure")
        self.assertEqual(failure["error_code"], "provider_timeout")
        self.assertEqual(failure["stage"], "reference_model")
        self.assertIsInstance(failure["elapsed_ms"], int)
        self.assertNotIn("Synthetic provider timeout", canonical(detail).decode())

        settings = runtime.configure_provider("ollama", "synthetic:1b", None, None, True,
                                              timeout_seconds=900)
        self.assertEqual(settings["timeout_seconds"], 900)
        self.assertTrue(settings["detailed_capture"])
        self.assertEqual(builds, [300, 900])
        self.assertEqual(runtime.call(lambda workspace: workspace.agent.store.read()), failed)
        self.assertEqual(len(attempts), 3)  # A settings change never silently retries a provider call.
        self.assertEqual(runtime.call(lambda workspace: workspace.generation_plan())["stage"], "reference_model")
        self.assertEqual(advance()["phase"], "completed")
        resumed = runtime.call(lambda workspace: workspace.agent.store.read())
        self.assertEqual((resumed["stage"], resumed["calls"]), (3, 4))
        self.assertEqual({path: resumed["files"][path] for path in saved["files"]}, saved["files"])
        self.assertEqual(set(resumed["files"]) - set(saved["files"]),
                         {"model/wire.py", "model/test_model.py"})
        self.assertEqual(resumed["approved_spec"], saved["approved_spec"])
        self.assertEqual([(stage, timeout) for stage, timeout, _ in attempts],
            [("architecture", 300), ("verification_plan", 300), ("reference_model", 300),
             ("reference_model", 900)])
        self.assertEqual(attempts[-1][2], attempts[-2][2])
        self.assertEqual(runtime.call(lambda workspace: workspace.history(failed["revision"])), detail)
        self.assertIsNone(resumed["simulation"])
        self.assertEqual(self.simulator.calls, [])

    def test_call_budget_exhaustion_blocks_plan_without_new_effect(self) -> None:
        self.approve_specification()
        self.agent.policy = replace(self.agent.policy, max_calls=1)
        asyncio.run(self.generate_one())
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "expert_call_budget_exhausted"):
            self.workspace.generation_plan()
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "blocked")
        self.assertEqual(self.store.read(), before)

    def test_queued_revision_change_cannot_execute_a_different_stage(self) -> None:
        self.approve_specification()
        plan = self.workspace.generation_plan()

        async def scenario() -> None:
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            # Another authorized local writer changed state before the task ran.
            self.agent.detail("brief")
            await self.workspace._tasks[identifier]
            self.assertEqual(self.workspace.operation(identifier)["phase"], "failed")
            self.assertEqual(self.store.read()["stage"], 0)
        asyncio.run(scenario())
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(self.simulator.calls, [])

    def test_each_generation_stops_before_separately_reviewed_simulation(self) -> None:
        asyncio.run(self.generate_baseline())
        self.assertEqual([stage for stage, _ in self.expert.seen], list(STAGES))
        self.assertEqual(self.simulator.calls, [])
        self.assertEqual(self.store.read()["status"], "building")
        self.assertEqual(self.store.read()["stage"], len(STAGES))
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "simulate")
        with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
            self.workspace.generation_plan()
        simulation = self.workspace.simulation_plan()
        with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
            asyncio.run(self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                expected_revision=simulation["revision"], plan_digest=simulation["plan_digest"]))
        self.assertEqual(self.simulator.calls, [])

    def test_diagnosis_stops_before_rerun_and_signoff_stops_before_acceptance(self) -> None:
        async def scenario() -> None:
            await self.generate_baseline()
            self.simulator.fail_first = True
            # Explicit test-double simulation is not a generation side effect.
            await self.agent.advance()
            self.assertEqual(self.workspace.generation_plan()["stage"], "diagnosis")
            await self.generate_one()
            self.assertEqual(len(self.simulator.calls), 1)
            self.assertEqual(self.store.read()["status"], "building")
            self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "simulate")
            await self.agent.advance()
            self.assertEqual(self.workspace.generation_plan()["stage"], "signoff")
            await self.generate_one()
            self.assertEqual(len(self.simulator.calls), 2)
            self.assertEqual(self.store.read()["status"], "awaiting_acceptance")
            self.assertIsNone(self.store.read()["acceptance_mode"])
            self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "review_acceptance")
            with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
                self.workspace.generation_plan()
            card = self.workspace.review("acceptance")
            with self.assertRaisesRegex(ValueError, "comparison_requires_runtime_bound_run"):
                self.workspace.approve_review("acceptance", expected_revision=card["revision"],
                    state_digest=card["state_digest"], payload_digest=card["payload_digest"])
            self.assertEqual(self.store.read()["status"], "awaiting_acceptance")
            # Only exercise projection of an accepted state, not real evidence qualification.
            with patch.object(self.store, "measurement", return_value={}) as measured:
                self.workspace.approve_review("acceptance", expected_revision=card["revision"],
                    state_digest=card["state_digest"], payload_digest=card["payload_digest"])
                measured.assert_called_once()
            self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "complete")
        asyncio.run(scenario())
        self.assertEqual([stage for stage, _ in self.expert.seen], [*STAGES, "diagnosis", "signoff"])

    def test_review_rejection_does_not_loop_or_accept(self) -> None:
        async def scenario() -> None:
            await self.generate_baseline()
            await self.agent.advance()
            self.expert.responses["signoff"] = {"verdict": "revise", "summary": "Synthetic review",
                "findings": ["Manual design decision is required."]}
            await self.generate_one()
        asyncio.run(scenario())
        self.assertEqual(self.store.read()["status"], "review_blocked")
        workflow = self.workspace.snapshot()["workflow"]
        self.assertEqual(workflow["action"], "blocked")
        self.assertIn("Signoff requested changes", workflow["message"])
        self.assertIn("Propose a reviewed change", workflow["message"])
        self.assertNotIn("Manual design decision is required.", workflow["message"])
        self.assertIsNone(workflow["plan"])
        with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
            self.workspace.generation_plan()
        self.assertEqual(len(self.simulator.calls), 1)

    def test_pending_change_proposal_blocks_generation_until_review(self) -> None:
        async def scenario() -> None:
            await self.generate_baseline()
            await self.agent.advance()
            old_plan = self.workspace.generation_plan()
            self.assertEqual(old_plan["stage"], "signoff")
            paths: dict[str, list[str]] = {stage: [] for stage in STAGES}
            paths["dv"] = ["dv/test_wire.py"]
            self.expert.responses["change_planning"] = {
                "summary": "Synthetic DV extension awaiting explicit review",
                "specification": ready_spec(), "stage_paths": paths, "manifest": manifest()}
            proposal = await self.agent.propose_improvement("Extend the DV checks", intent="dv")
            before = self.store.read()
            calls = len(self.expert.seen)
            self.assertEqual(before["proposal"], proposal)
            self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "review_change")
            with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
                self.workspace.generation_plan()
            with self.assertRaisesRegex(ValueError, "workspace_generation_not_ready"):
                await self.workspace.submit_generation(client_operation_id=uuid.uuid4().hex,
                    expected_revision=before["revision"], plan_digest=old_plan["plan_digest"])
            self.assertEqual(self.store.read(), before)
            self.assertEqual(len(self.expert.seen), calls)
            self.assertEqual(len(self.simulator.calls), 1)
        asyncio.run(scenario())

    def test_legacy_manual_approval_retains_existing_generation_compatibility(self) -> None:
        legacy = specification()
        self.agent.propose(legacy)
        card = self.workspace.review("specification")
        with self.assertRaisesRegex(ValueError, "legacy_specification"):
            self.workspace.approve_review("specification", expected_revision=card["revision"],
                state_digest=card["state_digest"], payload_digest=card["payload_digest"])
        self.agent.approve(content_digest(legacy))
        asyncio.run(self.generate_one())
        self.assertEqual(self.store.read()["stage"], 1)
        self.assertEqual(self.simulator.calls, [])

    def test_exhausted_repair_budget_does_not_dispatch_diagnosis(self) -> None:
        async def scenario() -> None:
            await self.generate_baseline()
            self.simulator.fail_first = True
            await self.agent.advance()
        asyncio.run(scenario())
        self.agent.policy = replace(self.agent.policy, max_repairs=0)
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "repair_budget_exhausted"):
            self.workspace.generation_plan()
        self.assertEqual(self.workspace.snapshot()["workflow"]["action"], "blocked")
        self.assertEqual(self.store.read(), before)
        self.assertEqual([stage for stage, _ in self.expert.seen], list(STAGES))

    def test_revoked_provider_while_queued_does_not_dispatch(self) -> None:
        self.approve_specification()

        async def scenario() -> None:
            plan = self.workspace.generation_plan()
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            self.agent.expert = None
            await self.workspace._tasks[identifier]
            self.assertEqual(self.workspace.operation(identifier)["phase"], "failed")
            self.assertEqual(self.store.read()["stage"], 0)
        asyncio.run(scenario())
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(self.store.read()["calls"], 0)

    def test_queued_cancellation_and_restart_never_dispatch(self) -> None:
        self.approve_specification()

        async def scenario() -> None:
            for restart in (False, True):
                with self.subTest(restart=restart):
                    plan = self.workspace.generation_plan()
                    identifier = uuid.uuid4().hex
                    await self.workspace.submit_generation(client_operation_id=identifier,
                        expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
                    task = self.workspace._tasks[identifier]
                    if restart:
                        reopened = DesignWorkspace(self.agent)
                        reopened.reconcile_interrupted()
                        await task
                    else:
                        self.assertEqual(self.workspace.request_cancellation(identifier)["phase"], "cancelled")
                        await asyncio.sleep(0)
                    self.assertEqual(self.workspace.operation(identifier)["phase"], "cancelled")
                    self.assertEqual(self.store.read()["stage"], 0)
        asyncio.run(scenario())
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertEqual(self.expert.seen, [])

    def test_active_cancellation_preserves_uncertain_intent_after_restart(self) -> None:
        self.approve_specification()

        async def scenario() -> None:
            entered = asyncio.Event()
            class WaitingExpert:
                async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                    entered.set()
                    await asyncio.Event().wait()
                    raise AssertionError("unreachable")
            self.agent.expert = WaitingExpert()
            plan = self.workspace.generation_plan()
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            task = self.workspace._tasks[identifier]
            await asyncio.wait_for(entered.wait(), timeout=2)
            self.assertEqual(self.workspace.request_cancellation(identifier)["phase"], "cancellation_requested")
            await task
            self.assertEqual(self.workspace.operation(identifier)["phase"], "reconciliation_needed")
            self.assertEqual(self.store.read()["active"]["stage"], "architecture")
            self.assertEqual(self.store.read()["calls"], 1)
            reopened = DesignWorkspace(self.agent)
            reopened.reconcile_interrupted()
            self.assertEqual(reopened.operation(identifier)["phase"], "reconciliation_needed")
            with self.assertRaisesRegex(ValueError, "interrupted_operation_requires_reconciliation"):
                reopened.generation_plan()
        asyncio.run(scenario())

    def test_restart_records_active_generation_as_uncertain_without_replay(self) -> None:
        self.approve_specification()
        state = self.store.read()
        identifier = uuid.uuid4().hex
        updated = copy.deepcopy(state)
        updated["calls"] = 1
        updated["active"] = {"id": uuid.uuid4().hex, "kind": "expert", "stage": "architecture"}
        updated["workspace_operations"][identifier] = {
            "request_digest": content_digest({"action": "generate"}),
            "phase": "active", "result_revision": None, "error_code": None}
        self.store.save(state, updated, "workspace.active", {"client_operation_id": identifier})
        restarted = DesignWorkspace(self.agent)
        restarted.reconcile_interrupted()
        self.assertEqual(restarted.operation(identifier)["phase"], "reconciliation_needed")
        self.assertEqual(restarted.operation(identifier)["error_code"], "operation_cancelled_uncertain")
        self.assertEqual(self.store.read()["active"], updated["active"])
        self.assertEqual(self.store.read()["calls"], 1)
        self.assertEqual(self.expert.seen, [])
        self.assertEqual(restarted.snapshot()["workflow"]["action"], "blocked")
        before = self.store.read()
        restarted.reconcile_interrupted()
        self.assertEqual(self.store.read(), before)

    def test_failure_retains_standardized_telemetry_without_raw_exception(self) -> None:
        self.approve_specification()
        self.expert.fail = True
        self.agent.trace_store = DesignTraceStore(self.store, enabled=True)

        async def scenario() -> None:
            plan = self.workspace.generation_plan()
            identifier = uuid.uuid4().hex
            await self.workspace.submit_generation(client_operation_id=identifier,
                expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
            await self.workspace._tasks[identifier]
            row = self.workspace.operation(identifier)
            self.assertEqual(row["phase"], "failed")
            self.assertEqual(row["error_code"], "expert_invocation_failed")
        asyncio.run(scenario())
        detail = self.workspace.history(self.store.read()["revision"])
        self.assertIn("operation.failed", {row["event"] for row in detail["trace"]})
        self.assertIn("provider_failure", {row["category"] for row in detail["details"]})
        self.assertIsInstance(detail["metrics"]["elapsed_ms"], int)
        self.assertIsNone(self.store.read()["active"])
        self.assertEqual(self.store.read()["stage"], 0)
        self.assertNotIn("private prompt must not be logged", canonical(detail).decode())
        self.assertNotIn("private prompt must not be logged", canonical(self.workspace.snapshot()).decode())

    def test_success_retains_opt_in_capture_and_linked_usage(self) -> None:
        self.approve_specification()
        self.agent.trace_store = DesignTraceStore(self.store, enabled=True)
        asyncio.run(self.generate_one())
        detail = self.workspace.history(self.store.read()["revision"])
        self.assertIn("assistant_output", {row["category"] for row in detail["details"]})
        self.assertEqual(detail["metrics"]["input_tokens"], 10)
        self.assertEqual(detail["metrics"]["output_tokens"], 20)
        self.assertIn("workspace.requested", {row["event"] for row in detail["trace"]})
        self.assertIn("operation.completed", {row["event"] for row in detail["trace"]})
        self.assertEqual(detail["files"][0]["path"], "docs/architecture.md")
        self.assertNotIn("assistant_output", canonical(self.workspace.snapshot()).decode())

    def test_unexpected_error_with_recorded_intent_requires_reconciliation(self) -> None:
        self.approve_specification()

        async def broken_advance() -> JsonObject:
            state = self.store.read()
            self.store.save(state, {**state, "active": {"id": uuid.uuid4().hex,
                "kind": "expert", "stage": "architecture"}}, "operation.started")
            raise RuntimeError("SYNTHETIC PRIVATE EXCEPTION")

        async def scenario() -> None:
            plan = self.workspace.generation_plan()
            identifier = uuid.uuid4().hex
            with patch.object(self.agent, "advance", broken_advance):
                await self.workspace.submit_generation(client_operation_id=identifier,
                    expected_revision=plan["revision"], plan_digest=plan["plan_digest"])
                await self.workspace._tasks[identifier]
            self.assertEqual(self.workspace.operation(identifier)["phase"], "reconciliation_needed")
            self.assertIsNotNone(self.store.read()["active"])
            self.assertNotIn("SYNTHETIC PRIVATE EXCEPTION", canonical(self.workspace.snapshot()).decode())
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
