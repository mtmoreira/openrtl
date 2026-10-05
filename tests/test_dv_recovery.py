"""Synthetic DV manifest recovery; no provider or generated-code execution."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply
from openrtl.domain.design_session import JsonObject, STAGES, canonical, content_digest
from openrtl.domain.provider_controls import estimated_cost_nano, request_reserve_nano
from tests.test_design_agent import FakeExpert, contribution, manifest, specification


def invalid_links() -> JsonObject:
    output = contribution("dv")
    output["summary"] = "SYNTHETIC REJECTED DV SUMMARY"
    output["files"][0]["content"] = "# SYNTHETIC REJECTED DV SOURCE\n"
    # Preserve the captured error's shape: requirement mappings also contain
    # engineering decisions/assumptions, rather than only approved requirements.
    output["manifest"]["requirement_tests"].extend([
        {"requirement_id": "memory.synthetic_decision", "tests": ["transfer"]},
        {"requirement_id": "assumption.synthetic_default", "tests": ["transfer"]},
    ])
    return output


class ScriptedDVExpert:
    def __init__(self, outputs: list[JsonObject | Exception], *,
                 provider: str = "unit-test", model: str = "fake-not-a-live-model") -> None:
        self.outputs = outputs
        self.provider, self.model = provider, model
        self.seen: list[tuple[str, JsonObject, str]] = []

    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        self.seen.append((stage, copy.deepcopy(context), operation_id))
        selected = self.outputs[len(self.seen) - 1]
        if isinstance(selected, Exception):
            raise selected
        return ExpertReply(copy.deepcopy(selected), self.provider, self.model, 100, 50)


class DVRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = DesignSessionStore(Path(temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        self.agent = DesignAgent(self.store, FakeExpert())
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        for _ in STAGES[:-1]:
            asyncio.run(self.agent.advance())
        self.before = self.store.read()
        self.before_files = self.store.contents(self.before)

    def agent_for(self, expert: ScriptedDVExpert, *, policy: DesignPolicy | None = None,
                  capture: bool = False) -> tuple[DesignAgent, DesignTraceStore]:
        traces = DesignTraceStore(self.store, enabled=capture)
        agent = DesignAgent(self.store, expert, policy=policy or DesignPolicy(), trace_store=traces)
        return agent, traces

    def failures(self) -> list[JsonObject]:
        return [row for row in self.store.events() if row["event"] == "operation.failed"]

    def assert_retained(self, before: JsonObject) -> None:
        state = self.store.read()
        self.assertIsNone(state["active"])
        for field in ("stage", "status", "files", "summaries", "manifest", "simulation",
                      "approved_spec", "spec", "change_plan", "engineering_memory"):
            self.assertEqual(state[field], before[field], field)
        self.assertEqual(self.store.contents(state), self.store.contents(before))

    def test_path_then_unknown_requirement_ids_recover_with_complete_private_candidates(self) -> None:
        path_error = invalid_links()
        path_error["manifest"]["sources"][1] = "rtl/property.sv"
        links_error = invalid_links()
        valid = contribution("dv")
        expert = ScriptedDVExpert([path_error, links_error, valid])
        agent, traces = self.agent_for(expert, capture=True)

        state = asyncio.run(agent.advance())

        self.assertEqual(state["stage"], len(STAGES))
        self.assertEqual(state["calls"] - self.before["calls"], 3)
        self.assertEqual(state["manifest"], manifest())
        self.assertIsNone(state["last_error"])
        self.assertIsNone(state["active"])
        self.assertEqual(self.store.contents(state), {
            **self.before_files, "dv/test_wire.py": valid["files"][0]["content"]})
        self.assertEqual([row["fields"]["validation_code"] for row in self.failures()],
                         ["source_unavailable", "requirement_test_links_incomplete"])
        deadlines = [context["expert_deadline"] for _, context, _ in expert.seen]
        self.assertTrue(all(deadline == deadlines[0] for deadline in deadlines))
        self.assertEqual(len({operation for _, _, operation in expert.seen}), 3)
        for _, context, _ in expert.seen:
            self.assertEqual(context["manifest_contract"], {
                "required_requirement_ids": ["wire.transfer"], "rtl_top_module_name": "wire_top",
                "rtl_sources": ["rtl/properties.sv", "rtl/wire_top.sv"]})
            self.assertFalse(any(path.startswith("rtl/") for path in context["artifacts"]))
        for attempt, rejected, field, code in (
                (1, path_error, "manifest.sources", "source_unavailable"),
                (2, links_error, "manifest.requirement_tests", "requirement_test_links_incomplete")):
            correction = expert.seen[attempt][1]["dv_correction"]
            self.assertEqual(correction["attempt"], attempt)
            self.assertEqual(correction["validation_code"], code)
            self.assertEqual(correction["candidate"], rejected)
            feedback = correction["validation_feedback"]
            self.assertEqual(set(feedback), {"field", "expected"})
            self.assertEqual(feedback["field"], field)
            self.assertIs(type(feedback["expected"]), str)
            self.assertTrue(feedback["expected"])
        records = traces.records({row[2] for row in expert.seen})
        self.assertEqual([row["payload"]["output"] for row in records
                          if row["category"] == "assistant_output"], [path_error, links_error, valid])
        received = [row for row in self.store.events() if row["event"] == "operation.received"
                    and row["fields"]["operation_id"] in {item[2] for item in expert.seen}]
        self.assertEqual(len(received), 3)
        self.assertEqual(sum(row["fields"]["input_tokens"] for row in received), 300)
        self.assertEqual(sum(row["fields"]["output_tokens"] for row in received), 150)
        public = canonical({"state": state, "events": self.store.events()}).decode()
        for private in ("SYNTHETIC REJECTED DV", "memory.synthetic_decision", "dv_correction",
                        "validation_feedback", "expert_deadline", "rtl/property.sv"):
            self.assertNotIn(private, public)
        self.assertFalse(any(b"SYNTHETIC REJECTED DV SOURCE" in bytes(row[0])
                             for row in self.store.connection.execute("SELECT content FROM blobs")))

    def test_exhaustion_preserves_saved_artifacts_and_never_prunes_unknown_ids(self) -> None:
        rejected = invalid_links()
        expert = ScriptedDVExpert([rejected] * 3)
        agent, traces = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assert_retained(self.before)
        self.assertEqual(self.store.read()["calls"] - self.before["calls"], 3)
        self.assertEqual(len(expert.seen), 3)
        self.assertEqual([row["fields"]["validation_code"] for row in self.failures()],
                         ["requirement_test_links_incomplete"] * 3)
        for attempt in (1, 2):
            self.assertEqual(expert.seen[attempt][1]["dv_correction"]["candidate"], rejected)
        self.assertEqual(rejected, invalid_links())
        self.assertEqual(traces.records({row[2] for row in expert.seen}), [])
        self.assertFalse(traces.status()["available"])

    def test_missing_mappings_are_not_inferred_from_available_tests(self) -> None:
        rejected = contribution("dv")
        rejected["manifest"]["requirement_tests"] = []
        expert = ScriptedDVExpert([rejected] * 3)
        agent, _ = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assert_retained(self.before)
        self.assertEqual(len(expert.seen), 3)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"],
                         "requirement_test_links_incomplete")

    def test_call_budget_counts_each_attempt(self) -> None:
        expert = ScriptedDVExpert([invalid_links()] * 3)
        agent, _ = self.agent_for(expert, policy=DesignPolicy(max_calls=self.before["calls"] + 2))
        with self.assertRaisesRegex(ValueError, "^expert_call_budget_exhausted$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 2)
        self.assertEqual(self.store.read()["calls"] - self.before["calls"], 2)
        self.assert_retained(self.before)

    def test_zero_recovery_policy_returns_first_manifest_failure(self) -> None:
        expert = ScriptedDVExpert([invalid_links(), contribution("dv")])
        agent, _ = self.agent_for(expert, policy=DesignPolicy(max_dv_corrections=0))
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(self.before)

    def test_correction_policy_rejects_noninteger_and_unbounded_values(self) -> None:
        for invalid in (-1, 3, True, 1.0, "1", None):
            with self.subTest(value=invalid), self.assertRaisesRegex(
                    ValueError, "^dv_correction_budget_invalid$"):
                DesignPolicy(max_dv_corrections=invalid)  # type: ignore[arg-type]

    def test_expired_deadline_stops_before_another_provider_attempt(self) -> None:
        class ExpiredExpert(ScriptedDVExpert):
            timeout_seconds = 0.01

            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                result = await super().generate(stage, context, operation_id)
                await asyncio.sleep(0.02)
                return result

        expert = ExpiredExpert([invalid_links(), contribution("dv")])
        agent, _ = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(self.before)

    def test_provider_exception_is_terminal_even_after_a_recoverable_manifest_error(self) -> None:
        expert = ScriptedDVExpert([invalid_links(), RuntimeError("SYNTHETIC PRIVATE PROVIDER FAILURE"),
                                  contribution("dv")])
        agent, _ = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_invocation_failed$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 2)
        self.assert_retained(self.before)
        self.assertNotIn("validation_code", self.failures()[-1]["fields"])
        self.assertNotIn("SYNTHETIC PRIVATE PROVIDER FAILURE", canonical(self.store.events()).decode())

    def test_file_security_errors_are_terminal_before_manifest_correction(self) -> None:
        for path, code in (("../escaped.py", "source_path_invalid"),
                           ("rtl/unowned.sv", "contribution_ownership_invalid"),
                           ("dv/.hidden.py", "source_path_invalid")):
            with self.subTest(path=path):
                rejected = invalid_links()
                rejected["files"][0]["path"] = path
                expert = ScriptedDVExpert([rejected, contribution("dv")])
                agent, _ = self.agent_for(expert)
                before = self.store.read()
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"], code)

    def test_unknown_validation_errors_do_not_become_retry_authority(self) -> None:
        class PrivateError(ValueError):
            def __str__(self) -> str:
                raise AssertionError("Private exception text must not be inspected")

        for error in (ValueError("requirement_test_links_incomplete"),
                      ValueError("SYNTHETIC PRIVATE FAILURE"), KeyError("source_unavailable"),
                      PrivateError("source_unavailable")):
            with self.subTest(error_type=type(error).__name__):
                expert = ScriptedDVExpert([invalid_links(), contribution("dv")])
                agent, _ = self.agent_for(expert)
                before = self.store.read()
                with patch("openrtl.application.design_agent.validate_manifest", side_effect=error):
                    with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                        asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
        self.assertNotIn("SYNTHETIC PRIVATE FAILURE", canonical(self.store.events()).decode())

    def test_unsafe_manifest_source_path_does_not_request_correction(self) -> None:
        rejected = invalid_links()
        rejected["manifest"]["sources"][0] = "../outside.sv"
        expert = ScriptedDVExpert([rejected, contribution("dv")])
        agent, _ = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(self.before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"], "source_path_invalid")

    def test_missing_independent_model_tests_is_terminal_before_manifest_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                initial_expert = FakeExpert()
                model = contribution("reference_model")
                model["files"] = model["files"][:1]
                initial_expert.responses["reference_model"] = model
                agent = DesignAgent(store, initial_expert)
                agent.propose(specification())
                agent.approve(content_digest(specification()))
                for _ in STAGES[:-1]:
                    asyncio.run(agent.advance())
                before = store.read()
                expert = ScriptedDVExpert([invalid_links(), contribution("dv")])
                agent = DesignAgent(store, expert)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assertEqual(store.read()["files"], before["files"])
                self.assertEqual(store.read()["stage"], before["stage"])
                self.assertIsNone(store.read()["manifest"])
                self.assertEqual(store.events()[-1]["fields"]["validation_code"],
                                 "independent_model_tests_missing")
            finally:
                store.close()

    def test_all_attempts_use_normal_known_usage_and_spend_accounting(self) -> None:
        expert = ScriptedDVExpert([invalid_links(), contribution("dv")],
                                 provider="openai", model="gpt-5.6-terra")
        agent, _ = self.agent_for(expert, policy=DesignPolicy(provider_model="gpt-5.6-terra"))
        agent.configure_provider("gpt-5.6-terra", 5_000_000_000)
        state = asyncio.run(agent.advance())
        expected = 2 * estimated_cost_nano("gpt-5.6-terra", 100, 50)
        self.assertEqual(state["provider"]["spent_nano_usd"], expected)
        self.assertIsNone(state["provider"]["pending"])
        self.assertFalse(state["provider"]["uncertain"])
        events = [row for row in self.store.events() if row["event"] == "operation.received"
                  and row["fields"]["operation_id"] in {item[2] for item in expert.seen}]
        self.assertEqual(sum(row["fields"]["estimated_cost_nano_usd"] for row in events), expected)

    def test_correction_respects_remaining_spend_before_dispatch(self) -> None:
        expert = ScriptedDVExpert([invalid_links(), contribution("dv")],
                                 provider="openai", model="gpt-5.6-terra")
        policy = DesignPolicy(provider_model="gpt-5.6-terra")
        agent, _ = self.agent_for(expert, policy=policy)
        agent.configure_provider("gpt-5.6-terra", request_reserve_nano(
            "gpt-5.6-terra", policy.max_output_tokens))
        before = self.store.read()
        with self.assertRaisesRegex(ValueError, "^provider_spend_budget_exhausted$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertEqual(self.store.read()["provider"]["spent_nano_usd"],
                         estimated_cost_nano("gpt-5.6-terra", 100, 50))
        self.assertFalse(self.store.read()["provider"]["uncertain"])

    def test_unknown_provider_usage_is_terminal_and_retains_reservation(self) -> None:
        class UnknownUsageExpert(ScriptedDVExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                return ExpertReply(reply.output, reply.provider, reply.model)

        expert = UnknownUsageExpert([invalid_links(), contribution("dv")],
                                    provider="openai", model="gpt-5.6-terra")
        policy = DesignPolicy(provider_model="gpt-5.6-terra")
        agent, _ = self.agent_for(expert, policy=policy)
        agent.configure_provider("gpt-5.6-terra", 5_000_000_000)
        before = self.store.read()
        with self.assertRaises(ValueError):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertTrue(self.store.read()["provider"]["uncertain"])
        self.assertEqual(self.store.read()["provider"]["spent_nano_usd"],
                         request_reserve_nano("gpt-5.6-terra", policy.max_output_tokens))

    def reviewed_dv_change(self) -> JsonObject:
        asyncio.run(self.agent.advance())
        paths: dict[str, list[str]] = {stage: [] for stage in STAGES}
        paths["dv"] = ["dv/test_wire.py"]
        plan = self.agent.plan_change({"specification": specification(), "stage_paths": paths,
                                       "manifest": manifest()})
        self.agent.approve_change(plan, content_digest(plan))
        for _ in STAGES[:-1]:
            asyncio.run(self.agent.advance())
        return self.store.read()

    def test_reviewed_change_paths_and_manifest_remain_authoritative(self) -> None:
        before = self.reviewed_dv_change()
        for kind, code in (("path", "change_stage_paths_differ_from_review"),
                           ("manifest", "change_manifest_differs_from_review")):
            with self.subTest(kind=kind):
                rejected = invalid_links() if kind == "path" else contribution("dv")
                if kind == "path":
                    rejected["files"][0]["path"] = "dv/unreviewed.py"
                else:
                    rejected["manifest"]["seed"] += 1
                expert = ScriptedDVExpert([rejected, contribution("dv")])
                agent, _ = self.agent_for(expert)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"], code)

    def test_repair_can_only_complete_with_exact_reviewed_manifest_and_write_set(self) -> None:
        before = self.reviewed_dv_change()
        valid = contribution("dv")
        valid["files"][0]["content"] += "# Explicitly reviewed DV revision\n"
        expert = ScriptedDVExpert([invalid_links(), valid])
        agent, _ = self.agent_for(expert)
        state = asyncio.run(agent.advance())
        self.assertEqual(state["manifest"], before["change_plan"]["manifest"])
        self.assertEqual(state["stage"], len(STAGES))
        self.assertEqual(len(expert.seen), 2)
        for path, digest in before["files"].items():
            if path != "dv/test_wire.py":
                self.assertEqual(state["files"][path], digest)
        self.assertEqual(self.store.contents(state)["dv/test_wire.py"], valid["files"][0]["content"])


if __name__ == "__main__":
    unittest.main()
