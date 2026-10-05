"""Synthetic duplicate-document recovery; no providers or private captures read."""

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
from tests.test_design_agent import FakeExpert, FakeSimulator, contribution, manifest, specification


def duplicate_documents(stage: str = "verification_plan") -> JsonObject:
    """Match the captured shape, never its private generated document contents."""
    output = contribution(stage)
    output["summary"] = "SYNTHETIC REJECTED DOCUMENT SUMMARY"
    path = output["files"][0]["path"]
    output["files"] = [
        {"path": path, "content": "# SYNTHETIC CONFLICTING DOCUMENT ONE\n"},
        {"path": path, "content": "# SYNTHETIC CONFLICTING DOCUMENT TWO\n"},
    ]
    return output


class ScriptedDocumentExpert:
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


class DocumentRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = DesignSessionStore(Path(temporary.name).resolve() / "project", create=True)
        self.addCleanup(self.store.close)
        self.agent = DesignAgent(self.store, FakeExpert())
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))

    def reach_stage(self, stage: str) -> JsonObject:
        while self.store.read()["stage"] < STAGES.index(stage):
            asyncio.run(self.agent.advance())
        self.assertEqual(self.store.read()["stage"], STAGES.index(stage))
        return self.store.read()

    def agent_for(self, expert: ScriptedDocumentExpert, *, stage: str | None = "verification_plan",
                  policy: DesignPolicy | None = None,
                  capture: bool = False) -> tuple[DesignAgent, DesignTraceStore, JsonObject]:
        if stage is not None:
            self.reach_stage(stage)
        traces = DesignTraceStore(self.store, enabled=capture)
        agent = DesignAgent(self.store, expert, policy=policy or DesignPolicy(), trace_store=traces)
        return agent, traces, self.store.read()

    def failures(self) -> list[JsonObject]:
        return [row for row in self.store.events() if row["event"] == "operation.failed"]

    def assert_retained(self, before: JsonObject) -> None:
        state = self.store.read()
        self.assertIsNone(state["active"])
        for field in ("stage", "status", "files", "summaries", "manifest", "simulation",
                      "approved_spec", "spec", "change_plan", "engineering_memory", "repairs"):
            self.assertEqual(state[field], before[field], field)
        self.assertEqual(self.store.contents(state), self.store.contents(before))

    def test_captured_two_distinct_contents_recover_without_choosing_either_candidate(self) -> None:
        rejected = duplicate_documents()
        valid = contribution("verification_plan")
        valid["files"][0]["content"] = "# Fresh explicit proposal after conflict feedback\n"
        expert = ScriptedDocumentExpert([rejected, valid])
        agent, traces, before = self.agent_for(expert, capture=True)

        state = asyncio.run(agent.advance())

        self.assertEqual(state["stage"], STAGES.index("verification_plan") + 1)
        self.assertEqual(state["calls"] - before["calls"], 2)
        self.assertIsNone(state["active"])
        self.assertIsNone(state["last_error"])
        self.assertEqual(self.store.contents(state), {
            **self.store.contents(before), "docs/verification-plan.md": valid["files"][0]["content"]})
        self.assertEqual([row["fields"]["validation_code"] for row in self.failures()],
                         ["contribution_ownership_invalid"])
        self.assertNotIn("document_correction", expert.seen[0][1])
        correction = expert.seen[1][1]["document_correction"]
        self.assertEqual(correction["attempt"], 1)
        self.assertEqual(correction["validation_code"], "contribution_ownership_invalid")
        self.assertEqual(correction["candidate"], rejected)
        self.assertEqual(rejected, duplicate_documents())
        feedback = correction["validation_feedback"]
        self.assertEqual(set(feedback), {"field", "duplicate_of", "expected"})
        self.assertEqual(feedback["field"], "files[1].path")
        self.assertEqual(feedback["duplicate_of"], "files[0].path")
        self.assertIs(type(feedback["expected"]), str)
        self.assertTrue(feedback["expected"])
        self.assertNotIn("docs/verification-plan.md", feedback["expected"])
        self.assertEqual(expert.seen[0][1]["expert_deadline"], expert.seen[1][1]["expert_deadline"])
        self.assertEqual(len({row[2] for row in expert.seen}), 2)
        records = traces.records({row[2] for row in expert.seen})
        self.assertEqual([row["payload"]["output"] for row in records
                          if row["category"] == "assistant_output"], [rejected, valid])
        received = [row for row in self.store.events() if row["event"] == "operation.received"
                    and row["fields"]["operation_id"] in {item[2] for item in expert.seen}]
        self.assertEqual(len(received), 2)
        self.assertEqual(sum(row["fields"]["input_tokens"] for row in received), 200)
        self.assertEqual(sum(row["fields"]["output_tokens"] for row in received), 100)
        public = canonical({"state": state, "events": self.store.events()}).decode()
        for private in ("SYNTHETIC CONFLICTING", "SYNTHETIC REJECTED DOCUMENT", "document_correction",
                        "validation_feedback", "duplicate_of", "expert_deadline"):
            self.assertNotIn(private, public)
        self.assertFalse(any(b"SYNTHETIC CONFLICTING" in bytes(row[0])
                             for row in self.store.connection.execute("SELECT content FROM blobs")))

    def test_architecture_duplicates_also_request_an_explicit_new_proposal(self) -> None:
        expert = ScriptedDocumentExpert([duplicate_documents("architecture"), contribution("architecture")])
        agent, _, before = self.agent_for(expert, stage="architecture")
        state = asyncio.run(agent.advance())
        self.assertEqual(state["stage"], 1)
        self.assertEqual(state["calls"] - before["calls"], 2)
        self.assertEqual(self.store.contents(state)["docs/architecture.md"],
                         contribution("architecture")["files"][0]["content"])

    def test_multiple_duplicates_receive_all_fixed_location_feedback(self) -> None:
        rejected = duplicate_documents()
        rejected["files"].append({"path": "docs/appendix.md", "content": "# Appendix one\n"})
        rejected["files"].append({"path": "docs/appendix.md", "content": "# Appendix two\n"})
        rejected["files"].append(copy.deepcopy(rejected["files"][0]))
        valid = contribution("verification_plan")
        valid["files"].append({"path": "docs/appendix.md", "content": "# Fresh appendix\n"})
        expert = ScriptedDocumentExpert([rejected, valid])
        agent, _, _ = self.agent_for(expert)
        state = asyncio.run(agent.advance())
        correction = expert.seen[1][1]["document_correction"]
        feedback = [correction["validation_feedback"], *correction["additional_feedback"]]
        self.assertEqual([(row["field"], row["duplicate_of"]) for row in feedback],
                         [("files[1].path", "files[0].path"),
                          ("files[3].path", "files[2].path"),
                          ("files[4].path", "files[0].path")])
        self.assertEqual(len({row["expected"] for row in feedback}), 1)
        self.assertEqual(correction["candidate"], rejected)
        self.assertEqual(self.store.contents(state)["docs/appendix.md"], "# Fresh appendix\n")

    def test_valid_multiple_documents_are_not_narrowed_to_a_single_file(self) -> None:
        valid = contribution("verification_plan")
        valid["files"].append({"path": "docs/coverage.md", "content": "# Additional coverage\n"})
        expert = ScriptedDocumentExpert([valid])
        agent, _, before = self.agent_for(expert)
        state = asyncio.run(agent.advance())
        self.assertEqual(set(state["files"]) - set(before["files"]),
                         {"docs/verification-plan.md", "docs/coverage.md"})
        self.assertEqual(len(expert.seen), 1)

    def test_exhaustion_preserves_saved_state_and_does_not_capture_without_optin(self) -> None:
        rejected = duplicate_documents()
        expert = ScriptedDocumentExpert([rejected] * 3)
        agent, traces, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assert_retained(before)
        self.assertEqual(self.store.read()["calls"] - before["calls"], 3)
        self.assertEqual(len(expert.seen), 3)
        self.assertEqual([row["fields"]["validation_code"] for row in self.failures()],
                         ["contribution_ownership_invalid"] * 3)
        for attempt in (1, 2):
            self.assertEqual(expert.seen[attempt][1]["document_correction"]["attempt"], attempt)
            self.assertEqual(expert.seen[attempt][1]["document_correction"]["candidate"], rejected)
        self.assertEqual(len({row[1]["expert_deadline"] for row in expert.seen}), 1)
        self.assertEqual(traces.records({row[2] for row in expert.seen}), [])
        self.assertFalse(traces.status()["available"])

    def test_zero_correction_policy_preserves_first_failure(self) -> None:
        expert = ScriptedDocumentExpert([duplicate_documents(), contribution("verification_plan")])
        agent, _, before = self.agent_for(expert, policy=DesignPolicy(max_document_corrections=0))
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)

    def test_correction_policy_accepts_only_bounded_json_integers(self) -> None:
        for invalid in (-1, 3, True, 1.0, "1", None):
            with self.subTest(value=invalid), self.assertRaisesRegex(
                    ValueError, "^document_correction_budget_invalid$"):
                DesignPolicy(max_document_corrections=invalid)  # type: ignore[arg-type]

    def test_each_attempt_consumes_the_normal_call_budget(self) -> None:
        before = self.reach_stage("verification_plan")
        expert = ScriptedDocumentExpert([duplicate_documents()] * 3)
        agent, _, before = self.agent_for(expert, policy=DesignPolicy(max_calls=before["calls"] + 2))
        with self.assertRaisesRegex(ValueError, "^expert_call_budget_exhausted$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 2)
        self.assertEqual(self.store.read()["calls"] - before["calls"], 2)
        self.assert_retained(before)

    def test_expired_shared_deadline_prevents_another_attempt(self) -> None:
        class ExpiredExpert(ScriptedDocumentExpert):
            timeout_seconds = 0.01

            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                await asyncio.sleep(0.02)
                return reply

        expert = ExpiredExpert([duplicate_documents(), contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)

    def test_provider_exception_after_duplicate_is_terminal(self) -> None:
        expert = ScriptedDocumentExpert([duplicate_documents(),
                                        RuntimeError("SYNTHETIC PRIVATE PROVIDER FAILURE"),
                                        contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_invocation_failed$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 2)
        self.assert_retained(before)
        self.assertNotIn("validation_code", self.failures()[-1]["fields"])
        self.assertNotIn("SYNTHETIC PRIVATE PROVIDER FAILURE", canonical(self.store.events()).decode())

    def test_plain_or_unknown_exception_text_is_not_correction_authority(self) -> None:
        class PrivateError(ValueError):
            def __str__(self) -> str:
                raise AssertionError("Private exception text must not be inspected")

        for error in (ValueError("contribution_ownership_invalid"),
                      ValueError("SYNTHETIC PRIVATE VALIDATION"),
                      KeyError("contribution_ownership_invalid"), PrivateError("contribution_ownership_invalid")):
            with self.subTest(error_type=type(error).__name__):
                expert = ScriptedDocumentExpert([duplicate_documents(), contribution("verification_plan")])
                agent, _, before = self.agent_for(expert)
                with patch("openrtl.application.design_agent.validate_files", side_effect=error):
                    with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                        asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
        self.assertNotIn("SYNTHETIC PRIVATE VALIDATION", canonical(self.store.events()).decode())

    def test_later_unsafe_or_unowned_paths_override_duplicate_correction(self) -> None:
        for path, code in (("../escape.md", "source_path_invalid"),
                           ("docs/.hidden.md", "source_path_invalid"),
                           ("rtl/unowned.sv", "contribution_ownership_invalid")):
            with self.subTest(path=path):
                rejected = duplicate_documents()
                rejected["files"].append({"path": path, "content": "# Cannot be accepted\n"})
                expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
                agent, _, before = self.agent_for(expert)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"], code)

    def test_invalid_duplicate_or_later_contents_are_terminal(self) -> None:
        for index in (1, 2):
            for invalid, code in ((None, "text_type_invalid"), ("", "text_size_invalid"),
                                  ("x\x00", "text_control_character_invalid"),
                                  ("x" * (256 * 1024 + 1), "text_size_invalid")):
                with self.subTest(index=index, code=code):
                    rejected = duplicate_documents()
                    rejected["files"].append({"path": "docs/appendix.md", "content": "# Appendix\n"})
                    rejected["files"][index]["content"] = invalid
                    expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
                    agent, _, before = self.agent_for(expert)
                    with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                        asyncio.run(agent.advance())
                    self.assertEqual(len(expert.seen), 1)
                    self.assert_retained(before)
                    self.assertEqual(self.failures()[-1]["fields"]["validation_code"], code)

    def test_output_size_bound_is_terminal_before_duplicate_recovery(self) -> None:
        rejected = duplicate_documents()
        rejected["files"].extend({"path": f"docs/large-{i}.md", "content": "x" * (256 * 1024)}
                                 for i in range(2))
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_invocation_failed$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertNotIn("validation_code", self.failures()[-1]["fields"])

    def test_duplicate_does_not_hide_total_project_artifact_bound(self) -> None:
        architecture_expert = FakeExpert()
        architecture = contribution("architecture")
        architecture["files"][0]["content"] = "x" * 230000
        architecture_expert.responses["architecture"] = architecture
        self.agent.expert = architecture_expert
        rejected = duplicate_documents()
        rejected["files"].extend({"path": f"docs/large-{i}.md", "content": "y" * 160000}
                                 for i in range(2))
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"], "project_artifacts_exceed_bound")

    def test_largest_competing_content_counts_regardless_of_duplicate_order(self) -> None:
        architecture_expert = FakeExpert()
        architecture = contribution("architecture")
        architecture["files"][0]["content"] = "x" * 230000
        architecture_expert.responses["architecture"] = architecture
        self.agent.expert = architecture_expert
        for large_index in (0, 1):
            with self.subTest(large_index=large_index):
                rejected = duplicate_documents()
                rejected["files"][large_index]["content"] = "y" * 256000
                rejected["files"].append({"path": "docs/appendix.md", "content": "z" * 50000})
                expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
                agent, _, before = self.agent_for(expert)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"],
                                 "project_artifacts_exceed_bound")

    def test_correction_context_bound_is_terminal_without_truncating_candidates(self) -> None:
        architecture_expert = FakeExpert()
        architecture = contribution("architecture")
        architecture["files"][0]["content"] = "x" * 230000
        architecture_expert.responses["architecture"] = architecture
        self.agent.expert = architecture_expert
        rejected = duplicate_documents()
        rejected["files"][0]["content"] = "y" * 150000
        rejected["files"][1]["content"] = "z" * 150000
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_context_exceeds_bound$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assertEqual(self.store.read()["calls"] - before["calls"], 1)
        self.assert_retained(before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"], "contribution_ownership_invalid")

    def test_existing_architecture_collision_is_terminal_even_after_duplicate(self) -> None:
        rejected = duplicate_documents()
        rejected["files"].append({"path": "docs/architecture.md", "content": "# Must not overwrite\n"})
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"], "artifact_owner_conflict")

    def test_nonnull_manifest_is_terminal_even_with_duplicate_documents(self) -> None:
        rejected = duplicate_documents()
        rejected["manifest"] = manifest()
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, before = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"],
                         "stage_cannot_replace_simulation_manifest")

    def reviewed_document_change(self) -> JsonObject:
        while self.store.read()["stage"] < len(STAGES):
            asyncio.run(self.agent.advance())
        paths: dict[str, list[str]] = {stage: [] for stage in STAGES}
        paths["verification_plan"] = ["docs/verification-plan.md", "docs/coverage.md"]
        plan = self.agent.plan_change({"specification": specification(), "stage_paths": paths,
                                       "manifest": manifest()})
        self.agent.approve_change(plan, content_digest(plan))
        return self.reach_stage("verification_plan")

    def test_reviewed_multifile_write_set_cannot_be_narrowed_during_correction(self) -> None:
        before = self.reviewed_document_change()
        rejected = duplicate_documents()
        # Missing reviewed coverage.md is independently invalid, so no correction.
        expert = ScriptedDocumentExpert([rejected, contribution("verification_plan")])
        agent, _, _ = self.agent_for(expert)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"],
                         "change_stage_paths_differ_from_review")

    def test_reviewed_multiple_documents_can_recover_only_as_exact_reviewed_set(self) -> None:
        before = self.reviewed_document_change()
        rejected = duplicate_documents()
        rejected["files"].append({"path": "docs/coverage.md", "content": "# Candidate coverage\n"})
        valid = contribution("verification_plan")
        valid["files"][0]["content"] += "# Reviewed update\n"
        valid["files"].append({"path": "docs/coverage.md", "content": "# Fresh reviewed coverage\n"})
        expert = ScriptedDocumentExpert([rejected, valid])
        agent, _, _ = self.agent_for(expert)
        state = asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 2)
        self.assertEqual(state["change_plan"], before["change_plan"])
        self.assertEqual(state["manifest"], before["manifest"])
        for path, digest in before["files"].items():
            if path != "docs/verification-plan.md":
                self.assertEqual(state["files"][path], digest)
        for row in valid["files"]:
            self.assertEqual(self.store.contents(state)[row["path"]], row["content"])

    def test_reviewed_replacement_does_not_count_the_old_artifact_twice(self) -> None:
        initial_expert = FakeExpert()
        for stage, size in (("architecture", 230000), ("verification_plan", 260000)):
            output = contribution(stage)
            output["files"][0]["content"] = "x" * size
            initial_expert.responses[stage] = output
        self.agent.expert = initial_expert
        before = self.reviewed_document_change()
        valid = contribution("verification_plan")
        valid["files"][0]["content"] = "y" * 50000
        valid["files"].append({"path": "docs/coverage.md", "content": "z" * 50000})
        expert = ScriptedDocumentExpert([valid])
        agent, _, _ = self.agent_for(expert)
        state = asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assertEqual(state["files"]["docs/architecture.md"], before["files"]["docs/architecture.md"])
        self.assertEqual(self.store.contents(state)["docs/verification-plan.md"], "y" * 50000)
        self.assertEqual(state["change_plan"], before["change_plan"])
        self.assertEqual(state["manifest"], before["manifest"])

    def test_non_document_duplicates_remain_terminal_including_diagnosis(self) -> None:
        for stage in ("reference_model", "rtl", "assertions", "dv", "diagnosis"):
            with self.subTest(stage=stage):
                if stage == "diagnosis":
                    asyncio.run(self.agent.advance())  # Accept the untouched normal DV response.
                    simulator = FakeSimulator()
                    simulator.fail_first = True
                    self.agent.simulator = simulator
                    asyncio.run(self.agent.advance())
                    self.assertEqual(self.store.read()["status"], "needs_repair")
                else:
                    self.reach_stage(stage)
                rejected = contribution(stage)
                rejected["files"].append(copy.deepcopy(rejected["files"][0]))
                expert = ScriptedDocumentExpert([rejected, contribution(stage)])
                agent, _, before = self.agent_for(expert, stage=None)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    asyncio.run(agent.advance())
                self.assertEqual(len(expert.seen), 1)
                self.assert_retained(before)
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"],
                                 "contribution_ownership_invalid")

    def test_all_corrections_use_normal_spend_and_known_usage_accounting(self) -> None:
        expert = ScriptedDocumentExpert([duplicate_documents(), contribution("verification_plan")],
                                       provider="openai", model="gpt-5.6-terra")
        agent, _, _ = self.agent_for(expert, policy=DesignPolicy(provider_model="gpt-5.6-terra"))
        agent.configure_provider("gpt-5.6-terra", 5_000_000_000)
        state = asyncio.run(agent.advance())
        expected = 2 * estimated_cost_nano("gpt-5.6-terra", 100, 50)
        self.assertEqual(state["provider"]["spent_nano_usd"], expected)
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])
        events = [row for row in self.store.events() if row["event"] == "operation.received"
                  and row["fields"]["operation_id"] in {item[2] for item in expert.seen}]
        self.assertEqual(sum(row["fields"]["estimated_cost_nano_usd"] for row in events), expected)

    def test_remaining_spend_is_checked_before_correction_dispatch(self) -> None:
        expert = ScriptedDocumentExpert([duplicate_documents(), contribution("verification_plan")],
                                       provider="openai", model="gpt-5.6-terra")
        policy = DesignPolicy(provider_model="gpt-5.6-terra")
        agent, _, _ = self.agent_for(expert, policy=policy)
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

    def test_unknown_usage_retains_reservation_and_is_terminal(self) -> None:
        class UnknownUsageExpert(ScriptedDocumentExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                return ExpertReply(reply.output, reply.provider, reply.model)

        expert = UnknownUsageExpert([duplicate_documents(), contribution("verification_plan")],
                                    provider="openai", model="gpt-5.6-terra")
        policy = DesignPolicy(provider_model="gpt-5.6-terra")
        agent, _, _ = self.agent_for(expert, policy=policy)
        agent.configure_provider("gpt-5.6-terra", 5_000_000_000)
        before = self.store.read()
        with self.assertRaises(ValueError):
            asyncio.run(agent.advance())
        self.assertEqual(len(expert.seen), 1)
        self.assert_retained(before)
        self.assertTrue(self.store.read()["provider"]["uncertain"])
        self.assertEqual(self.store.read()["provider"]["spent_nano_usd"],
                         request_reserve_nano("gpt-5.6-terra", policy.max_output_tokens))


if __name__ == "__main__":
    unittest.main()
