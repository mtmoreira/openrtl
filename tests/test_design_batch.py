"""Provider-free batch/recovery contracts; doubles are not live design evidence."""

from __future__ import annotations

import argparse
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_simulation import IsolatedDesignSimulator
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply
from openrtl.application.design_batch import bind_delegation, run_batch
from openrtl.design_cli import add_design_commands, run_design_command
from openrtl.domain.design_delegation import validate_authorization, validate_delegated_spec
from openrtl.domain.design_session import JsonObject, SESSION_SCHEMA, canonical, content_digest, legacy_initial_state
from tests.test_design_agent import FakeExpert, FakeSimulator, specification


def authorization(seed: JsonObject) -> JsonObject:
    return {"schema": "openrtl.design-delegation.v1", "seed_spec_digest": content_digest(seed),
            "allow_assumptions": True, "allow_requirement_proposals": False, "allow_final_acceptance": True,
            "max_calls": 20, "max_repairs": 2, "max_steps": 24, "max_seconds": 60}


class DesignBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "project"
        self.store = DesignSessionStore(self.root, create=True)
        self.expert, self.simulator = FakeExpert(), FakeSimulator()
        self.agent = DesignAgent(self.store, self.expert, self.simulator)
        self.seed = specification()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def grant(self, plan: JsonObject | None = None) -> JsonObject:
        self.agent.propose(self.seed)
        selected = plan or authorization(self.seed)
        return bind_delegation(self.agent, selected, content_digest(selected))

    def batch(self) -> JsonObject:
        return asyncio.run(run_batch(self.agent, emit=lambda _: None))

    def reopen(self) -> None:
        self.store.close()
        self.store = DesignSessionStore(self.root)
        self.agent = DesignAgent(self.store, self.expert, self.simulator)

    def test_complete_delegated_chain_and_resume_are_idempotent(self) -> None:
        self.grant()
        result = self.batch()
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(result["approval_mode"], "delegated")
        self.assertEqual(result["acceptance_mode"], "delegated")
        before = self.store.read()
        self.reopen()
        plan = authorization(self.seed)
        bind_delegation(self.agent, plan, content_digest(plan))
        self.assertEqual(self.batch(), result)
        self.assertEqual(self.store.read(), before)

    def test_final_acceptance_can_remain_with_user(self) -> None:
        plan = authorization(self.seed)
        plan["allow_final_acceptance"] = False
        self.grant(plan)
        result = self.batch()
        self.assertEqual(result["outcome"], "awaiting_review")
        self.assertEqual(result["status"], "awaiting_acceptance")
        self.assertIsNone(result["acceptance_mode"])

    def test_question_assumptions_remain_pending_after_delegated_acceptance(self) -> None:
        self.seed["questions"] = [{"id": "width", "text": "Use four bits?"}]
        proposed = specification()
        proposed["assumptions"] = [{"id": "width", "text": "Four bits", "rationale": "Bounded exploration"}]
        self.expert.responses["discovery"] = proposed
        self.grant()
        result = self.batch()
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(result["warnings"][0]["review"], "pending")
        warning_id = result["warnings"][0]["id"]
        self.reopen()
        self.agent.acknowledge_warning(warning_id)
        self.assertEqual(self.store.read()["warnings"][0]["review"], "acknowledged")
        revision = self.store.read()["revision"]
        self.agent.acknowledge_warning(warning_id)
        self.assertEqual(self.store.read()["revision"], revision)

    def test_question_cannot_disappear_without_recorded_assumption(self) -> None:
        self.seed["questions"] = [{"id": "width", "text": "Use four bits?"}]
        self.grant()
        self.assertEqual(self.batch()["outcome"], "stopped")
        self.assertIsNone(self.store.read()["approved_spec"])
        self.assertEqual(len(self.expert.seen), 1)

    def test_no_assumption_authority_stops_before_expert(self) -> None:
        self.seed["questions"] = [{"id": "width", "text": "Use four bits?"}]
        plan = authorization(self.seed)
        plan["allow_assumptions"] = False
        self.grant(plan)
        self.assertEqual(self.batch()["outcome"], "awaiting_review")
        self.assertEqual(self.expert.seen, [])

    def test_requirement_changes_are_scope_checked_and_warned(self) -> None:
        self.seed["questions"] = [{"id": "width", "text": "Use four bits?"}]
        proposed = specification()
        proposed["title"] = "Proposed wire variant"
        proposed["assumptions"] = [{"id": "width", "text": "Four bits", "rationale": "Exploration"}]
        self.expert.responses["discovery"] = proposed
        self.grant()
        self.assertEqual(self.batch()["outcome"], "stopped")
        self.assertIsNone(self.store.read()["approved_spec"])
        self.assertTrue(any(w["subject"] == "title" for w in self.store.read()["warnings"]))
        with self.assertRaisesRegex(ValueError, "cannot_be_replaced"):
            plan = authorization(self.seed)
            plan["allow_requirement_proposals"] = True
            bind_delegation(self.agent, plan, content_digest(plan))
        self.agent.revise()
        self.agent.propose(self.seed)
        bind_delegation(self.agent, plan, content_digest(plan))
        self.assertEqual(self.batch()["outcome"], "accepted")

    def test_exact_digest_and_seed_required_before_mutation(self) -> None:
        self.agent.propose(self.seed)
        before = self.store.read()
        plan = authorization(self.seed)
        with self.assertRaisesRegex(ValueError, "digest_mismatch"):
            bind_delegation(self.agent, plan, "sha256:wrong")
        self.assertEqual(self.store.read(), before)
        plan["seed_spec_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ValueError, "seed_changed"):
            bind_delegation(self.agent, plan, content_digest(plan))
        self.assertEqual(self.store.read(), before)

    def test_step_and_wall_deadline_budgets_survive_resume(self) -> None:
        plan = authorization(self.seed)
        plan["max_steps"] = 1
        state = self.grant(plan)
        self.assertEqual(self.batch()["outcome"], "budget_exhausted")
        self.reopen()
        bind_delegation(self.agent, plan, content_digest(plan))
        self.assertEqual(self.batch()["steps"], 1)
        self.assertEqual(self.expert.seen, [])
        with patch("openrtl.application.design_batch.time.time_ns", return_value=state["delegation"]["deadline_ns"]):
            self.assertEqual(self.batch()["outcome"], "budget_exhausted")

    def test_call_ceiling_cannot_be_increased_on_resume_or_revision(self) -> None:
        plan = authorization(self.seed)
        plan["max_calls"] = 1
        self.grant(plan)
        self.assertEqual(self.batch()["outcome"], "stopped")
        self.reopen()
        self.assertEqual(self.store.read()["limits"]["max_calls"], 1)
        with self.assertRaisesRegex(ValueError, "budget_exhausted"):
            asyncio.run(self.agent.advance())
        self.agent.revise()
        self.agent.propose(self.seed)
        self.agent.approve(content_digest(self.seed))
        with self.assertRaisesRegex(ValueError, "budget_exhausted"):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["calls"], 1)

    def test_expired_deadline_stops_without_consuming_a_step(self) -> None:
        state = self.grant()
        with patch("openrtl.application.design_batch.time.time_ns", return_value=state["delegation"]["deadline_ns"]):
            result = self.batch()
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(result["steps"], 0)
        self.assertEqual(self.store.read(), state)

    def test_repair_count_is_not_refunded_by_spec_revision(self) -> None:
        self.grant()
        self.simulator.fail_first = True
        self.assertEqual(self.batch()["outcome"], "accepted")
        self.assertEqual(self.store.read()["repairs"], 1)
        self.agent.revise()
        self.assertEqual(self.store.read()["repairs"], 1)
        self.assertEqual(self.store.read()["limits"]["max_repairs"], 2)

    def test_deadline_cancellation_keeps_provider_intent(self) -> None:
        class HangingExpert:
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
        self.seed["questions"] = [{"id": "width", "text": "Use four bits?"}]
        state = self.grant()
        self.agent = DesignAgent(self.store, HangingExpert())
        # Fake wall clock leaves 10ms for this unit-only coroutine, no live request.
        with patch("openrtl.application.design_batch.time.time_ns",
                   return_value=state["delegation"]["deadline_ns"] - 10_000_000):
            result = self.batch()
        self.assertEqual(result["outcome"], "deadline_exhausted")
        self.assertIsNotNone(result["active"])
        self.assertEqual(result["calls"], 1)

    def test_delegation_does_not_restore_provider_or_runtime(self) -> None:
        self.grant()
        self.agent = DesignAgent(self.store)
        self.assertEqual(self.batch()["outcome"], "stopped")
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertEqual(self.expert.seen, [])

    def test_lock_rejects_second_writer_but_allows_readonly_inspection(self) -> None:
        with self.assertRaisesRegex(ValueError, "writer_already_active"):
            DesignSessionStore(self.root)
        reader = DesignSessionStore(self.root, read_only=True)
        try:
            self.assertEqual(reader.read(), self.store.read())
            with self.assertRaisesRegex(ValueError, "read_only"):
                reader.save(reader.read(), reader.read(), "detail.changed")
        finally:
            reader.close()
        self.reopen()
        self.assertTrue(self.store.exclusive)

    def test_interrupted_expert_requires_reopen_exact_abandon_and_no_refund(self) -> None:
        class HangingExpert:
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        async def interrupt() -> None:
            task = asyncio.create_task(DesignAgent(self.store, HangingExpert()).discuss("ephemeral"))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(interrupt())
        active = self.store.read()["active"]["id"]
        with self.assertRaisesRegex(ValueError, "current_writer"):
            asyncio.run(self.agent.abandon(active))
        self.reopen()
        with self.assertRaisesRegex(ValueError, "mismatch"):
            asyncio.run(self.agent.abandon("0" * 32))
        asyncio.run(self.agent.abandon(active))
        state = self.store.read()
        self.assertIsNone(state["active"])
        self.assertEqual(state["calls"], 1)
        self.assertEqual(state["warnings"][0]["kind"], "interrupted_operation")

    def test_simulation_error_retains_intent_and_requires_cleanup_port(self) -> None:
        self.grant()
        self.simulator.synthetic = True
        self.assertEqual(self.batch()["outcome"], "stopped")
        operation = self.store.read()["active"]["id"]
        self.reopen()
        self.assertEqual(self.batch()["outcome"], "reconciliation_required")
        with self.assertRaisesRegex(ValueError, "runtime_reconciliation"):
            asyncio.run(self.agent.abandon(operation))
        class Recovery:
            async def abandon(self, operation_id: str, input_digest: str) -> None:
                if operation_id != operation:
                    raise ValueError("wrong operation")
        self.agent = DesignAgent(self.store, recovery=Recovery())
        asyncio.run(self.agent.abandon(operation))
        self.assertIsNone(self.store.read()["active"])
        self.assertIsNone(self.store.read()["simulation"])

    def test_legacy_schema_needs_explicit_append_only_upgrade(self) -> None:
        legacy = canonical(legacy_initial_state()).decode()
        self.store.connection.execute("UPDATE snapshots SET payload=? WHERE revision=0", (legacy,))
        with self.assertRaisesRegex(ValueError, "upgrade_required"):
            self.agent.propose(self.seed)
        self.store.upgrade()
        self.assertEqual(self.store.read()["schema"], SESSION_SCHEMA)
        self.assertEqual(self.store.connection.execute("SELECT payload FROM snapshots WHERE revision=0").fetchone()[0], legacy)
        revision = self.store.read()["revision"]
        self.store.upgrade()
        self.assertEqual(self.store.read()["revision"], revision)

    def test_batch_cli_persists_report_without_model_permission(self) -> None:
        seed_path = self.root / "seed.json"
        plan_path = self.root / "delegation.json"
        seed_path.write_bytes(canonical(self.seed))
        plan = authorization(self.seed)
        plan_path.write_bytes(canonical(plan))
        self.store.close()
        parser = argparse.ArgumentParser()
        add_design_commands(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(["batch", "--project", str(self.root), "--spec", str(seed_path),
                                  "--delegation", str(plan_path), "--approve-delegation", content_digest(plan)])
        with patch("builtins.print"):
            self.assertEqual(run_design_command(args), 2)
        self.store = DesignSessionStore(self.root)
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertEqual(len(list((self.root / "reports").glob("batch-*.json"))), 1)


class DelegationContractTest(unittest.TestCase):
    def test_unknown_fields_boolean_limits_and_unapproved_changes_fail_closed(self) -> None:
        for key, value in (("surprise", True), ("max_calls", True), ("allow_assumptions", 1)):
            plan = authorization(specification())
            plan[key] = value
            with self.assertRaises(ValueError):
                validate_authorization(plan)
        changed = specification()
        changed["ports"][0]["width"] = 8
        with self.assertRaisesRegex(ValueError, "not_delegated"):
            validate_delegated_spec(specification(), changed, authorization(specification()))


class RuntimeReconciliationTest(unittest.TestCase):
    def test_only_exact_recorded_container_can_be_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            operation = "a" * 32
            run = project / "runs" / operation
            run.mkdir(parents=True)
            (run / "docker-config").mkdir()
            profile = {"docker_executable": "/unit-only/docker", "socket": "/unit-only/socket", "image_id": "sha256:" + "b" * 64}
            intent = {"schema": "openrtl.design-runtime-intent.v1", "operation_id": operation,
                      "container_name": "openrtl-design-" + operation, "profile_digest": content_digest(profile),
                      "input_digest": "sha256:" + "c" * 64}
            (run / "intent.json").write_bytes(canonical(intent))

            class RuntimeDouble(IsolatedDesignSimulator):
                def __init__(self) -> None:
                    self.project, self.profile = project, profile
                    self.commands: list[list[str]] = []
                    self.wrong_identity = False
                    self.absent = False
                async def _process(self, argv: list[str], config: Path, timeout: int,
                                   bound: int = 48 * 1024 * 1024) -> tuple[int, bytes]:
                    self.commands.append(argv)
                    if "ps" in argv:
                        return 0, b"" if self.absent else b"d" * 64
                    if "inspect" in argv:
                        name = "another-project" if self.wrong_identity else "openrtl-design-" + operation
                        return 0, ("/" + name + "|" + profile["image_id"] + "|" + operation).encode()
                    return 0, b"removed"

            runtime = RuntimeDouble()
            runtime.wrong_identity = True
            with self.assertRaisesRegex(ValueError, "identity_mismatch"):
                asyncio.run(runtime.abandon(operation, intent["input_digest"]))
            self.assertFalse(any("rm" in argv for argv in runtime.commands))
            runtime.wrong_identity = False
            asyncio.run(runtime.abandon(operation, intent["input_digest"]))
            self.assertEqual(runtime.commands[-1][-3:], ["rm", "--force", "d" * 64])
            runtime.absent = True
            asyncio.run(runtime.abandon(operation, intent["input_digest"]))
            self.assertIn("ps", runtime.commands[-1])
            self.assertTrue((run / "intent.json").is_file())


if __name__ == "__main__":
    unittest.main()
