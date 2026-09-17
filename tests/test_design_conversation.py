"""Conversation/policy contracts using labeled doubles, never live RTL evidence."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_generation import response_schema
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.application.design_batch_policy import run_policy_batch
from openrtl.application.design_conversation import approve_shown, revoke, route, show_review
from openrtl.design_cli import conversation, run_design_command
from openrtl.domain.design_readiness import CATEGORIES, require_ready
from openrtl.domain.design_session import JsonObject, content_digest, validate_spec
from tests.test_design_agent import FakeExpert, FakeSimulator, specification


def ready_spec() -> JsonObject:
    spec = specification()
    decisions = {"interfaces": "a input and y output, both four bits.",
        "widths_signedness": "Both a and y are unsigned four-bit bitvectors.",
        "clock_reset": "Pure combinational transfer; no clock or reset.",
        "timing_latency": "Combinational with no registered cycle latency.",
        "handshake": "No handshake; y follows a continuously.",
        "exceptional_behavior": "Inputs cover all sixteen bit patterns; no exceptional opcode exists.",
        "acceptance": "Transfer matches all sixteen possible inputs."}
    spec["readiness"] = {"schema": "openrtl.design-readiness.v1", "items": [
        {"category": category, "status": "specified", "decision": decisions[category],
         "requirement_ids": ["wire.transfer"], "ports": ["a", "y"]} for category in CATEGORIES]}
    return spec


class ReadinessTest(unittest.TestCase):
    def test_legacy_spec_is_readable_but_not_automatically_ready(self) -> None:
        legacy = specification()
        self.assertEqual(validate_spec(legacy), legacy)
        with self.assertRaisesRegex(ValueError, "legacy_specification"):
            require_ready(legacy)
        self.assertNotIn("readiness", legacy)

    def test_complete_versioned_decisions_validate_and_schema_requires_them(self) -> None:
        require_ready(validate_spec(ready_spec()))
        schema = response_schema("discovery")
        proposed = schema["properties"]["specification"]["anyOf"][0]
        self.assertIn("readiness", proposed["required"])
        legacy = response_schema("discovery", include_readiness=False)["properties"]["specification"]["anyOf"][0]
        self.assertNotIn("readiness", legacy["required"])

    def test_missing_duplicate_categories_unknown_anchors_and_uncovered_ports_fail(self) -> None:
        for mutation in ("missing", "duplicate", "anchor", "port", "acceptance", "empty"):
            spec = ready_spec()
            rows = spec["readiness"]["items"]
            if mutation == "missing": rows.pop()
            if mutation == "duplicate": rows[1] = copy.deepcopy(rows[0])
            if mutation == "anchor": rows[2]["requirement_ids"] = ["invented.requirement"]
            if mutation == "port": rows[1]["ports"] = ["a"]
            if mutation == "acceptance": rows[-1]["status"] = "not_applicable"
            if mutation == "empty": rows[0]["decision"] = ""
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): validate_spec(spec)

    def test_unresolved_decision_and_question_cannot_be_approved(self) -> None:
        for unresolved in (True, False):
            spec = ready_spec()
            if unresolved: spec["readiness"]["items"][2]["status"] = "unresolved"
            else: spec["questions"] = [{"id": "wire.question", "text": "What reset behavior?"}]
            validate_spec(spec)
            with self.assertRaisesRegex(ValueError, "unresolved"): require_ready(spec)

    def test_routing_distinguishes_ambiguity_negation_changes_and_preferences(self) -> None:
        state = {"status": "accepted"}
        for message in ("yes", "approve", "do not continue", "approve this specification and run", "okay"):
            self.assertEqual(route(message, state), "clarify")
        self.assertEqual(route("add tests for reset", state), "propose-dv")
        self.assertEqual(route("add a reset input", state), "propose-feature")
        self.assertEqual(route("Can you add a reset input?", state), "propose-feature")
        self.assertEqual(route("explain why reset is synchronous", state), "explain")
        self.assertEqual(route("Do not wrap on overflow", {"status": "discovery"}), "discuss")


class ConversationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.store = DesignSessionStore(self.root / "project", create=True)
        self.addCleanup(self.store.close)
        self.expert = FakeExpert()
        self.expert.responses["discovery"] = ready_spec()
        self.agent = DesignAgent(self.store, self.expert, FakeSimulator())

    def chat(self, messages: list[str]) -> list[str]:
        inputs = iter([*messages, "/quit"])
        output: list[str] = []
        self.assertEqual(asyncio.run(conversation(self.agent, read=lambda _: next(inputs), emit=output.append)), 0)
        return output

    def test_normal_review_approves_exact_shown_spec_without_hash_or_execution(self) -> None:
        output = self.chat(["Design a four-bit wire", "approve this specification"])
        state = self.store.read()
        self.assertEqual(state["status"], "building")
        self.assertEqual(state["calls"], 1)
        self.assertEqual(state["stage"], 0)
        self.assertEqual(state["approval_mode"], "user")
        self.assertTrue(any("unsigned four-bit" in line for line in output))
        self.assertFalse(any("/approve sha256:" in line for line in output))
        self.assertFalse(any('"readiness":' in line for line in output))

    def test_greeting_and_role_question_answer_without_fabricating_a_design(self) -> None:
        replies = iter((
            {"reply": "Hi. Tell me what circuit you want to design.", "specification": None},
            {"reply": "I help turn circuit requirements into reviewable RTL and tests.", "specification": None},
        ))
        class DiscussionExpert(FakeExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str):
                self.responses["discovery"] = next(replies)
                return await super().generate(stage, context, operation_id)
        self.agent.expert = DiscussionExpert()
        output = self.chat(["hi", "who are you?"])
        self.assertTrue(any("Tell me what circuit" in line for line in output))
        self.assertTrue(any("reviewable RTL" in line for line in output))
        self.assertIsNone(self.store.read()["spec"])
        self.assertEqual(self.store.read()["calls"], 2)
        self.assertFalse(any("Review for specification" in line for line in output))
        self.assertNotIn("Tell me what circuit", json.dumps(self.store.read()))
        self.assertNotIn("reviewable RTL", json.dumps(self.store.events()))

    def test_natural_language_design_request_still_requires_exact_review(self) -> None:
        self.expert.responses["discovery"] = {"reply": "Here is a draft for your review.",
                                               "specification": ready_spec()}
        output = self.chat(["Design a four-bit wire"])
        self.assertEqual(self.store.read()["status"], "discovery")
        self.assertEqual(self.store.read()["spec"], ready_spec())
        self.assertTrue(any("draft for your review" in line for line in output))
        self.assertTrue(any("Review for specification" in line for line in output))

    def test_stale_shown_review_is_refused_and_new_review_can_be_approved(self) -> None:
        self.agent.propose(ready_spec())
        review = show_review(self.agent, lambda _: None)
        self.agent.detail("brief")
        with self.assertRaisesRegex(ValueError, "stale"):
            approve_shown(self.agent, review, "specification")
        self.assertEqual(self.store.read()["status"], "discovery")
        approve_shown(self.agent, show_review(self.agent, lambda _: None), "specification")
        with self.assertRaises(ValueError): approve_shown(self.agent, review, "specification")

    def test_unshown_and_wrong_kind_approvals_do_nothing(self) -> None:
        self.agent.propose(ready_spec())
        with self.assertRaisesRegex(ValueError, "show_current"):
            approve_shown(self.agent, None, "specification")
        review = show_review(self.agent, lambda _: None)
        with self.assertRaisesRegex(ValueError, "show_current"):
            approve_shown(self.agent, review, "acceptance")
        self.assertEqual(self.store.read()["status"], "discovery")

    def test_review_emitter_failure_does_not_return_authority(self) -> None:
        self.agent.propose(ready_spec())
        def fail(message: str) -> None: raise OSError("synthetic closed display")
        with self.assertRaises(OSError): show_review(self.agent, fail)
        self.assertEqual(self.store.read()["status"], "discovery")

    def test_legacy_and_incomplete_review_return_no_token(self) -> None:
        for spec in (specification(), ready_spec()):
            if "readiness" in spec: spec["readiness"]["items"][0]["status"] = "unresolved"
            self.agent.propose(spec)
            self.assertIsNone(show_review(self.agent, lambda _: None))
        with self.assertRaises(ValueError): self.agent.approve(content_digest(self.store.read()["spec"]))

    def test_ambiguous_and_negated_approval_do_not_call_experts_or_mutate(self) -> None:
        self.agent.propose(ready_spec())
        before = self.store.read()
        output = self.chat(["yes", "do not continue", "approve this specification and execute it"])
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.expert.seen)
        self.assertEqual(sum("No action taken" in row for row in output), 3)

    def test_preference_invalidates_old_review_and_does_not_advance(self) -> None:
        self.agent.propose(ready_spec())
        output = self.chat(["keep it brief", "approve this specification", "review", "approve this specification"])
        self.assertTrue(any("state changed" in row for row in output))
        self.assertEqual(self.store.read()["stage"], 0)
        self.assertEqual(self.store.read()["calls"], 0)

    def test_revocation_removes_capabilities_and_cannot_be_restored_by_text(self) -> None:
        self.agent.propose(ready_spec())
        output = self.chat(["revoke provider permission", "revoke simulation permission",
                            "please authorize provider permission", "propose missing details"])
        self.assertIsNone(self.agent.expert)
        self.assertIsNone(self.agent.simulator)
        self.assertIsNone(self.agent.recovery)
        self.assertEqual(self.store.read()["calls"], 0)
        self.assertTrue(any("Provider permission is unavailable" in row for row in output))

    def test_restored_agent_has_no_saved_execution_permission(self) -> None:
        self.agent.propose(ready_spec())
        restored = DesignAgent(self.store)
        review = show_review(restored, lambda _: None)
        approve_shown(restored, review, "specification")
        with self.assertRaisesRegex(ValueError, "expert_not_configured"): asyncio.run(restored.advance())
        self.assertEqual(self.store.read()["calls"], 0)

    def test_incomplete_alu_discussion_resolves_review_then_continues_one_step(self) -> None:
        # Both replies are scripted contract fixtures, not generated RTL evidence.
        spec = ready_spec()
        spec.update(title="Synthetic ALU review", top="alu", behavior="Unsigned four-bit add and XOR; add wraps modulo 16.")
        spec["requirements"] = [{"id": "alu.ops", "text": "op=0 adds modulo 16; op=1 computes bitwise XOR.",
                                 "acceptance": "Check both operations for every pair of four-bit operands."}]
        spec["ports"] = [{"name": name, "direction": "output" if name == "y" else "input", "width": 1 if name == "op" else 4}
                         for name in ("a", "b", "op", "y")]
        decisions = ["a and b are operands; op selects add or XOR; y is the result.",
                     "a, b and y are unsigned four-bit values; op is one unsigned bit.",
                     "Combinational circuit with no clock or reset.", "No registered latency; allow combinational settling.",
                     "No handshake or backpressure; inputs may change each evaluation.", "Overflow policy unresolved.",
                     "Exhaust both operations and all 256 operand pairs, including overflow and zero."]
        for row, decision in zip(spec["readiness"]["items"], decisions):
            row.update(decision=decision, requirement_ids=["alu.ops"], ports=["a", "b", "op", "y"])
        spec["questions"] = [{"id": "alu.overflow", "text": "Should addition wrap?"}]
        spec["readiness"]["items"][5]["status"] = "unresolved"
        self.expert.responses["discovery"] = spec
        inputs = iter(["Design an ALU", "approve this specification", "Propose the missing overflow decision",
                       "approve this specification", "continue", "/quit"])
        count = 0
        def read(prompt: str) -> str:
            nonlocal count
            count += 1
            if count == 3:
                complete = copy.deepcopy(spec)
                complete["questions"] = []
                complete["assumptions"] = [{"id": "alu.overflow", "text": "Addition wraps modulo 16.", "rationale": "Explicit synthetic proposal for review."}]
                complete["readiness"]["items"][5].update(status="specified", decision="Addition wraps modulo 16; XOR is bitwise.")
                self.expert.responses["discovery"] = complete
            return next(inputs)
        output: list[str] = []
        asyncio.run(conversation(self.agent, read=read, emit=output.append))
        self.assertEqual(self.store.read()["stage"], 1)
        self.assertEqual(self.store.read()["calls"], 3)
        self.assertTrue(any("incomplete" in row for row in output))
        self.assertTrue(any("Assumption alu.overflow" in row for row in output))
        self.assertFalse(any(p.startswith("rtl/") for p in self.store.read()["files"]))

    def test_final_acceptance_rechecks_retained_measurement(self) -> None:
        self.agent.propose(ready_spec())
        self.agent.approve(content_digest(ready_spec()))
        while self.store.read()["status"] != "awaiting_acceptance": asyncio.run(self.agent.advance())
        review = show_review(self.agent, lambda _: None)
        with patch.object(self.store, "measurement", side_effect=ValueError("changed_evidence")) as measured:
            with self.assertRaisesRegex(ValueError, "changed_evidence"): approve_shown(self.agent, review, "acceptance")
            measured.assert_called_once()
        self.assertEqual(self.store.read()["status"], "awaiting_acceptance")

    def test_batch_default_stops_at_final_review_and_preserves_resume_deadline(self) -> None:
        outputs: list[str] = []
        result = asyncio.run(run_policy_batch(self.agent, "review", specification=ready_spec(), emit=outputs.append))
        self.assertEqual(result["outcome"], "awaiting_review")
        deadline = self.store.read()["delegation"]["deadline_ns"]
        result = asyncio.run(run_policy_batch(self.agent, "review", emit=outputs.append))
        self.assertEqual(result["outcome"], "awaiting_review")
        self.assertEqual(self.store.read()["delegation"]["deadline_ns"], deadline)
        with self.assertRaisesRegex(ValueError, "cannot_be_replaced"):
            asyncio.run(run_policy_batch(self.agent, "explore", emit=lambda _: None))

    def test_batch_plain_intent_without_provider_stops_without_persisting_text(self) -> None:
        revoke(self.agent, "provider")
        result = asyncio.run(run_policy_batch(self.agent, "review", intent="private intent sentinel", emit=lambda _: None))
        self.assertEqual(result["outcome"], "provider_permission_required")
        self.assertIsNone(self.store.read()["spec"])
        self.assertNotIn("private intent sentinel", json.dumps(result))

    def test_batch_initial_discovery_obeys_deadline_and_retains_uncertainty(self) -> None:
        with patch.object(self.agent, "discuss", new=AsyncMock(side_effect=TimeoutError)):
            result = asyncio.run(run_policy_batch(self.agent, "review", intent="synthetic intent", max_seconds=1))
        self.assertEqual(result["outcome"], "deadline_exhausted")

    def test_batch_missing_readiness_and_assumptions_stop_with_action(self) -> None:
        result = asyncio.run(run_policy_batch(self.agent, "review", specification=specification(), emit=lambda _: None))
        self.assertEqual(result["outcome"], "readiness_review_required")
        self.assertIn("next_action", result)

    def test_batch_each_assumption_is_printed_and_recorded(self) -> None:
        spec = ready_spec()
        spec["assumptions"] = [{"id": "wire.default", "text": "Synthetic default", "rationale": "For unit contract coverage"}]
        output: list[str] = []
        result = asyncio.run(run_policy_batch(self.agent, "assumptions", specification=spec, emit=output.append))
        self.assertEqual(result["outcome"], "awaiting_review")
        self.assertEqual(len(result["warnings"]), 1)
        self.assertTrue(any("WARNING" in row and "Synthetic default" in row for row in output))

    def test_batch_warning_is_shown_before_delegated_approval(self) -> None:
        spec = ready_spec()
        spec["assumptions"] = [{"id": "wire.default", "text": "Synthetic default", "rationale": "For contract testing"}]
        modes: list[object] = []
        def emit(message: str) -> None:
            if message.startswith("WARNING"): modes.append(self.store.read()["approval_mode"])
        asyncio.run(run_policy_batch(self.agent, "assumptions", specification=spec, emit=emit))
        self.assertEqual(modes, [None])

    def test_normal_batch_cannot_accept_a_synthetic_receipt_without_measurement(self) -> None:
        with patch.object(self.store, "measurement", side_effect=ValueError("not_real_evidence")) as check:
            result = asyncio.run(run_policy_batch(self.agent, "review", specification=ready_spec(),
                                                  final_acceptance=True, emit=lambda _: None))
            check.assert_called_once()
        self.assertEqual(result["outcome"], "stopped")
        self.assertEqual(self.store.read()["status"], "awaiting_acceptance")

    def test_normal_batch_refuses_provider_downgrade_to_legacy_spec(self) -> None:
        seed = ready_spec()
        seed["questions"] = [{"id": "wire.default", "text": "Choose a default."}]
        legacy = specification()
        legacy["assumptions"] = [{"id": "wire.default", "text": "Synthetic default", "rationale": "Unit-only response"}]
        self.expert.responses["discovery"] = legacy
        result = asyncio.run(run_policy_batch(self.agent, "explore", specification=seed, emit=lambda _: None))
        self.assertEqual(result["outcome"], "stopped")
        self.assertIsNone(self.store.read()["approved_spec"])

    def test_initial_discovery_is_inside_saved_time_and_call_budgets(self) -> None:
        import time
        start = time.time_ns()
        result = asyncio.run(run_policy_batch(self.agent, "review", intent="Synthetic wire request",
                                              max_steps=1, max_seconds=60, emit=lambda _: None))
        state = self.store.read()
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(state["calls"], 1)
        self.assertGreaterEqual(state["delegation"]["started_ns"], start)
        self.assertEqual(state["delegation"]["deadline_ns"]-state["delegation"]["started_ns"], 60*10**9)
        with self.assertRaisesRegex(ValueError, "intent_is_only_for_new_session"):
            asyncio.run(run_policy_batch(self.agent, "review", intent="Must not replay", max_steps=1, max_seconds=60))

    def test_conversational_change_request_proposes_and_never_applies(self) -> None:
        self.agent.propose(ready_spec())
        self.agent.approve(content_digest(ready_spec()))
        while self.store.read()["status"] != "awaiting_acceptance": asyncio.run(self.agent.advance())
        before = self.store.read()
        with patch.object(self.agent, "propose_improvement", new=AsyncMock()) as propose:
            self.chat(["Can you add a reset input?"])
            propose.assert_awaited_once_with("Can you add a reset input?", intent="feature")
        self.assertEqual(self.store.read(), before)

    def test_batch_budget_is_not_refilled_on_resume(self) -> None:
        agent = DesignAgent(self.store, self.expert, policy=DesignPolicy(max_calls=1))
        result = asyncio.run(run_policy_batch(agent, "review", specification=ready_spec(), max_steps=1, emit=lambda _: None))
        self.assertEqual(result["outcome"], "budget_exhausted")
        result = asyncio.run(run_policy_batch(agent, "review", max_steps=1, emit=lambda _: None))
        self.assertEqual(result["outcome"], "budget_exhausted")
        self.assertEqual(self.store.read()["delegation"]["steps"], 1)

    def test_invalid_policy_is_rejected_before_discovery(self) -> None:
        before = self.store.read()
        with self.assertRaises(ValueError):
            asyncio.run(run_policy_batch(self.agent, "invalid", intent="unused"))
        self.assertEqual(self.store.read(), before)
        self.assertFalse(self.expert.seen)

    def test_actual_cli_policy_intent_stops_without_provider_or_internal_json(self) -> None:
        from openrtl.cli import parser
        intent = self.root / "intent.txt"
        intent.write_text("Create an unsigned four-bit incrementer.\n")
        project = self.root / "cli project"
        args = parser().parse_args(["batch", "--project", str(project), "--create", "--intent", str(intent), "--policy", "review"])
        with patch("builtins.print"):
            self.assertEqual(run_design_command(args), 2)
        reports = list((project / "reports").glob("batch-*.json"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(json.loads(reports[0].read_text())["outcome"], "provider_permission_required")


if __name__ == "__main__":
    unittest.main()
