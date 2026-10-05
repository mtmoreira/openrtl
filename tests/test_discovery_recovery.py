"""Synthetic discovery correction sequences; never live provider evidence."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy, ExpertReply
from openrtl.application.design_workspace import DesignWorkspace
from openrtl.domain.design_readiness import CATEGORIES
from openrtl.domain.design_session import JsonObject, canonical, content_digest
from openrtl.domain.discovery_validation import IDENTIFIER_EXPECTATION
from openrtl.domain.provider_controls import estimated_cost_nano
from tests.test_design_agent import specification
from tests.test_hardware_specification import parameterized_fifo_spec


def discussion(spec: JsonObject | None, *, reply: str = "Synthetic reviewable proposal",
               memory: list[JsonObject] | None = None,
               questions: list[str] | None = None) -> JsonObject:
    return {"reply": reply, "specification": spec,
            "engineering_memory": memory or [], "questions_asked": questions or []}


def compact_candidate(value: JsonObject) -> JsonObject:
    result = copy.deepcopy(value)
    result["reply"] = "structured"
    return result


def zero_width_proposal() -> JsonObject:
    spec = specification()
    spec["ports"] = json.loads(
        '[{"name":"data_in","direction":"input","width":0},'
        '{"name":"data_out","direction":"output","width":0}]'
    )
    return discussion(spec, reply="SYNTHETIC REJECTED CANDIDATE CONTENT")


def partial_port_readiness_proposal() -> JsonObject:
    spec = specification()
    spec["ports"] = json.loads(
        '[{"name":"clk_wr","direction":"input","width":1},'
        '{"name":"rst_n","direction":"input","width":1},'
        '{"name":"wr_data","direction":"input","width":32},'
        '{"name":"wr_valid","direction":"input","width":1},'
        '{"name":"wr_ready","direction":"output","width":1},'
        '{"name":"clk_rd","direction":"input","width":1},'
        '{"name":"rd_data","direction":"output","width":32},'
        '{"name":"rd_valid","direction":"output","width":1},'
        '{"name":"rd_ready","direction":"input","width":1}]'
    )
    names = [row["name"] for row in spec["ports"]]
    spec["readiness"] = {"schema": "openrtl.design-readiness.v1", "items": [
        {"category": category, "status": "specified", "decision": "Synthetic decision",
         "requirement_ids": ["wire.transfer"],
         "ports": ["wr_data", "rd_data"] if category == "widths_signedness" else names}
        for category in CATEGORIES
    ]}
    return discussion(spec)


class ScriptedExpert:
    """Each scripted output is a separately metered invocation."""

    def __init__(self, outputs: list[JsonObject | Exception], *, provider: str = "unit-test",
                 model: str = "fake-not-a-live-model") -> None:
        self.outputs = outputs
        self.provider, self.model = provider, model
        self.seen: list[tuple[str, JsonObject, str]] = []

    async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
        self.seen.append((stage, copy.deepcopy(context), operation_id))
        selected = self.outputs[len(self.seen) - 1]
        if isinstance(selected, Exception):
            raise selected
        return ExpertReply(copy.deepcopy(selected), self.provider, self.model, 100, 50)


class VersionedScriptedExpert(ScriptedExpert):
    discovery_contract_version = "v9"
    specification_contract_version = "v1"


class DiscoveryRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "session"
        self.store = DesignSessionStore(self.root, create=True)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def failures(self) -> list[JsonObject]:
        return [row for row in self.store.events() if row["event"] == "operation.failed"]

    def test_identifier_corrections_identify_only_the_invalid_field(self) -> None:
        for field in ("specification.top", "specification.ports[1].name",
                      "specification.hardware_specification.parameters[1].name"):
            with self.subTest(field=field):
                valid = discussion(parameterized_fifo_spec())
                invalid = copy.deepcopy(valid)
                spec = invalid["specification"]
                if field == "specification.top":
                    # Exact captured invalid scalar and JSON type; all other content is synthetic.
                    spec["top"] = "openrtl.hardware-specification.v1"
                    self.assertIs(type(spec["top"]), str)
                elif field == "specification.ports[1].name":
                    spec["ports"][1]["name"] = "SYNTHETIC PRIVATE PORT NAME"
                else:
                    spec["hardware_specification"]["parameters"][1]["name"] = "SYNTHETIC PRIVATE PARAMETER NAME"
                traces = DesignTraceStore(self.store, enabled=True)
                expert = ScriptedExpert([invalid, valid])
                agent = DesignAgent(self.store, expert, trace_store=traces)
                before_calls = self.store.read()["calls"]
                state = asyncio.run(agent.discuss("Synthetic identifier correction"))

                self.assertEqual(state["spec"], valid["specification"])
                self.assertEqual(state["calls"] - before_calls, 2)
                self.assertIsNone(state["active"])
                correction = expert.seen[1][1]["discovery_correction"]
                self.assertEqual(correction, {
                    "attempt": 1, "validation_code": "identifier_invalid",
                    "candidate": compact_candidate(invalid),
                    "validation_feedback": {"field": field, "expected": IDENTIFIER_EXPECTATION}})
                failure = self.failures()[-1]
                self.assertEqual(failure["fields"]["validation_code"], "identifier_invalid")
                captured = [row["payload"]["output"] for row in traces.records({expert.seen[0][2]})
                            if row["category"] == "assistant_output"]
                self.assertEqual(captured, [invalid])
                events = self.store.events()
                received = [row for row in events if row["event"] == "operation.received"
                            and row["fields"]["operation_id"] in {item[2] for item in expert.seen}]
                self.assertEqual(len(received), 2)
                self.assertLess(received[0]["sequence"], failure["sequence"])
                public = canonical({"state": state, "events": events}).decode()
                for private in ("validation_feedback", "SYNTHETIC PRIVATE", IDENTIFIER_EXPECTATION):
                    self.assertNotIn(private, public)

    def test_captured_identifier_exhaustion_preserves_state_and_accounts_all_attempts(self) -> None:
        invalid = discussion(parameterized_fifo_spec(), memory=[{
            "id": "invalid.only", "kind": "decision", "text": "SYNTHETIC REJECTED MEMORY",
            "provenance": "agent_proposal"}])
        invalid["specification"]["top"] = "openrtl.hardware-specification.v1"
        expert = ScriptedExpert([invalid, invalid, invalid])
        traces = DesignTraceStore(self.store, enabled=True)
        agent = DesignAgent(self.store, expert, trace_store=traces)
        prior = agent.propose(specification())["spec"]

        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            asyncio.run(agent.discuss("Synthetic exhausted identifier correction"))

        state = self.store.read()
        self.assertEqual(state["calls"], 3)
        self.assertEqual(len(expert.seen), 3)
        self.assertEqual(state["spec"], prior)
        self.assertEqual(state["engineering_memory"], [])
        self.assertIsNone(state["active"])
        self.assertEqual([row["fields"]["validation_code"] for row in self.failures()],
                         ["identifier_invalid"] * 3)
        for attempt, (_, context, _) in enumerate(expert.seen[1:], 1):
            self.assertEqual(context["discovery_correction"]["attempt"], attempt)
            self.assertEqual(context["discovery_correction"]["validation_feedback"], {
                "field": "specification.top", "expected": IDENTIFIER_EXPECTATION})
        receipts = [row for row in self.store.events() if row["event"] == "operation.received"]
        self.assertEqual(len(receipts), 3)
        self.assertEqual(sum(row["fields"]["input_tokens"] for row in receipts), 300)
        self.assertEqual(sum(row["fields"]["output_tokens"] for row in receipts), 150)
        outputs = [row["payload"]["output"] for row in traces.records({row[2] for row in expert.seen})
                   if row["category"] == "assistant_output"]
        self.assertEqual(outputs, [invalid] * 3)
        public = canonical({"state": state, "events": self.store.events()}).decode()
        for private in ("validation_feedback", "openrtl.hardware-specification.v1",
                        "SYNTHETIC REJECTED MEMORY", IDENTIFIER_EXPECTATION):
            self.assertNotIn(private, public)

    def test_captured_oversized_reply_is_omitted_from_correction_and_complete_spec_is_required(self) -> None:
        questions = [
            {"id": "fifo.width", "kind": "question", "text": "Choose the FIFO data width",
             "provenance": "agent_proposal"},
            {"id": "fifo.depth", "kind": "question", "text": "Choose the FIFO depth",
             "provenance": "agent_proposal"},
            {"id": "fifo.reset", "kind": "question", "text": "Choose the reset behavior",
             "provenance": "agent_proposal"},
        ]
        initial = {
            "reply": "structured", "specification": None, "engineering_memory": questions,
            "questions_asked": [row["id"] for row in questions], "resolved_questions": [],
            "question_plan": [
                {"id": row["id"], "topic": topic, "reason": "Required for the interface contract"}
                for row, topic in zip(questions, ("interface", "configuration", "clock_reset"), strict=True)
            ],
        }
        decisions = [
            {"id": "fifo.width.value", "kind": "decision", "text": "Data width is 32 bits",
             "provenance": "agent_proposal"},
            {"id": "fifo.depth.value", "kind": "decision", "text": "Depth is 16 entries",
             "provenance": "agent_proposal"},
            {"id": "fifo.reset.value", "kind": "decision", "text": "Reset is active low and synchronous",
             "provenance": "agent_proposal"},
            {"id": "fifo.ordering", "kind": "requirement", "text": "Accepted data remains ordered",
             "provenance": "agent_proposal"},
            {"id": "fifo.backpressure", "kind": "requirement", "text": "Full and empty apply backpressure",
             "provenance": "agent_proposal"},
            {"id": "fifo.simultaneous", "kind": "requirement", "text": "Simultaneous transfers are supported",
             "provenance": "agent_proposal"},
        ]
        resolutions = [
            {"id": question["id"], "resolution_id": decision["id"]}
            for question, decision in zip(questions, decisions[:3], strict=True)
        ]
        captured = {
            "reply": "R" * 8570, "specification": None, "engineering_memory": decisions,
            "questions_asked": [], "question_plan": [], "resolved_questions": resolutions,
        }
        corrected = copy.deepcopy(captured)
        corrected["reply"] = "structured"
        corrected["specification"] = parameterized_fifo_spec()
        self.assertEqual((len(captured["reply"]), type(captured["reply"]),
                          type(captured["specification"]), len(captured["engineering_memory"]),
                          len(captured["resolved_questions"])),
                         (8570, str, type(None), 6, 3))

        expert = VersionedScriptedExpert([initial, captured, corrected])
        traces = DesignTraceStore(self.store, enabled=True)
        agent = DesignAgent(self.store, expert, trace_store=traces)
        asyncio.run(agent.discuss("Design a parameterized FIFO"))
        state = asyncio.run(agent.discuss("Use width 32, depth 16, and active-low synchronous reset"))

        self.assertEqual(state["spec"], corrected["specification"])
        failure = self.failures()[-1]
        self.assertEqual(failure["fields"]["validation_code"], "expert_specification_required")
        correction = expert.seen[2][1]["discovery_correction"]
        self.assertEqual(correction["candidate"]["reply"], "structured")
        self.assertEqual(correction["candidate"]["engineering_memory"], decisions)
        self.assertLess(len(canonical(correction["candidate"])), len(canonical(captured)) - 8000)
        self.assertNotIn("R" * 100, canonical(correction).decode())
        failed_operation = failure["fields"]["operation_id"]
        captured_output = next(row["payload"]["output"] for row in traces.records({failed_operation})
                               if row["category"] == "assistant_output")
        self.assertEqual(len(captured_output["reply"]), 8570)

    def test_repeated_full_policy_question_is_corrected_with_original_request(self) -> None:
        question = {"id": "q_full_policy", "kind": "question",
                    "text": "Choose behavior when the FIFO is full", "provenance": "agent_proposal"}
        initial = specification()
        initial["questions"] = [{"id": question["id"], "text": question["text"]}]
        repeated = discussion(None, reply="Choose the full policy again.",
                              memory=[question], questions=["q_full_policy"])
        expanded = specification()
        expanded["requirements"].append({"id": "wire.stall", "text": "Hold input during a stall",
                                         "acceptance": "Check output stays stable during every stall"})
        decision = {**question, "kind": "decision", "text": "Backpressure preserves queued data"}
        expert = ScriptedExpert([
            discussion(initial, memory=[question], questions=["q_full_policy"]),
            repeated, discussion(expanded, memory=[decision]),
        ])
        agent = DesignAgent(self.store, expert)
        asyncio.run(agent.discuss("Synthetic FIFO design request"))
        replies: list[str] = []
        message = "Please expand the specification and preserve the existing decisions."
        state = asyncio.run(agent.discuss(message, emit_reply=replies.append))

        self.assertEqual(state["spec"], expanded)
        self.assertEqual(state["engineering_memory"], [decision])
        self.assertEqual(state["calls"], 3)
        self.assertIsNone(state["active"])
        self.assertEqual(len(replies), 1)
        self.assertIn("2 requirements and 2 ports", replies[0])
        self.assertIn("0 questions remain open", replies[0])
        self.assertNotIn(repeated["reply"], replies[0])
        self.assertEqual(self.failures()[0]["fields"]["validation_code"], "expert_clarification_repeated")
        first_pack, correction_pack = expert.seen[1][1], expert.seen[2][1]
        self.assertEqual(first_pack["user_message"], message)
        self.assertEqual(correction_pack["user_message"], message)
        self.assertEqual(first_pack["discovery_deadline"], correction_pack["discovery_deadline"])
        self.assertNotIn("discovery_correction", first_pack)
        self.assertEqual(correction_pack["discovery_correction"], {
            "attempt": 1, "validation_code": "expert_clarification_repeated",
            "candidate": compact_candidate(repeated)})
        self.assertEqual(len({row[2] for row in expert.seen}), 3)
        starts = [row for row in self.store.events() if row["event"] == "operation.started"]
        for started, (_, context, operation_id) in zip(starts, expert.seen, strict=True):
            provider_context = {key: value for key, value in context.items() if key != "discovery_deadline"}
            self.assertEqual(started["fields"]["operation_id"], operation_id)
            self.assertEqual(started["fields"]["context_digest"], content_digest(provider_context))
        proposals = [row for row in self.store.events() if row["event"] == "spec.proposed"]
        self.assertEqual(len(proposals), 2)
        self.assertEqual(proposals[-1]["fields"]["question_count"], 0)

    def test_captured_width_and_readiness_failures_each_reach_a_valid_correction(self) -> None:
        for invalid, code in ((zero_width_proposal(), "port_width_invalid"),
                              (partial_port_readiness_proposal(), "readiness_port_decisions_missing")):
            with self.subTest(code=code):
                valid = copy.deepcopy(invalid)
                if code == "port_width_invalid":
                    for port in valid["specification"]["ports"]:
                        port["width"] = 32
                else:
                    spec = valid["specification"]
                    for item in spec["readiness"]["items"]:
                        item["ports"] = [port["name"] for port in spec["ports"]]
                expert = ScriptedExpert([invalid, valid])
                agent = DesignAgent(self.store, expert)
                before_calls = self.store.read()["calls"]
                state = asyncio.run(agent.discuss("Synthetic correction request"))
                self.assertEqual(state["spec"], valid["specification"])
                self.assertEqual(state["calls"] - before_calls, 2)
                self.assertEqual(expert.seen[1][1]["discovery_correction"]["candidate"],
                                 compact_candidate(invalid))
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"], code)
                failed_state = self.store.historical_state(self.failures()[-1]["sequence"])
                self.assertNotEqual(failed_state["spec"], invalid["specification"])

    def test_exhaustion_preserves_previous_spec_and_discards_invalid_memory(self) -> None:
        invalid = zero_width_proposal()
        invalid["engineering_memory"] = [{"id": "invalid.only", "kind": "decision",
            "text": "SYNTHETIC INVALID MEMORY", "provenance": "agent_proposal"}]
        expert = ScriptedExpert([invalid, invalid, invalid, discussion(specification())])
        agent = DesignAgent(self.store, expert)
        prior = agent.propose(specification())["spec"]

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss("Synthetic bounded failure"))

        state = self.store.read()
        self.assertEqual(len(expert.seen), 3)
        self.assertEqual(state["calls"], 3)
        self.assertEqual(state["spec"], prior)
        self.assertEqual(state["engineering_memory"], [])
        self.assertIsNone(state["active"])
        self.assertEqual(len(self.failures()), 3)
        self.assertEqual([row[1]["discovery_correction"]["attempt"] for row in expert.seen[1:]], [1, 2])
        self.assertNotIn("SYNTHETIC INVALID MEMORY", canonical(state).decode())
        self.assertNotIn("SYNTHETIC REJECTED CANDIDATE CONTENT", canonical(self.store.events()).decode())

    def test_call_budget_caps_corrections_and_zero_policy_disables_them(self) -> None:
        expert = ScriptedExpert([zero_width_proposal()] * 3)
        agent = DesignAgent(self.store, expert, policy=DesignPolicy(max_calls=2))
        with self.assertRaises(ValueError):
            asyncio.run(agent.discuss("Synthetic call ceiling"))
        self.assertEqual(len(expert.seen), 2)
        self.assertEqual(self.store.read()["calls"], 2)
        self.assertEqual(len(self.failures()), 2)

        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "session", create=True)
            try:
                expert = ScriptedExpert([zero_width_proposal(), discussion(specification())])
                agent = DesignAgent(store, expert, policy=DesignPolicy(max_discovery_corrections=0))
                with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                    asyncio.run(agent.discuss("Synthetic no-correction policy"))
                self.assertEqual(len(expert.seen), 1)
                self.assertEqual(store.read()["calls"], 1)
            finally:
                store.close()

    def test_one_workspace_request_correlates_attempts_usage_and_final_reply(self) -> None:
        expert = ScriptedExpert([zero_width_proposal(), discussion(specification())])
        workspace = DesignWorkspace(DesignAgent(self.store, expert))
        identifier = uuid.uuid4().hex

        async def run() -> None:
            await workspace.submit_discussion("Synthetic workspace correction", client_operation_id=identifier,
                                              expected_revision=0)
            await workspace._tasks[identifier]

        asyncio.run(run())
        self.assertEqual(workspace.operation(identifier)["phase"], "completed")
        self.assertIn("1 requirements and 2 ports", workspace.operation(identifier)["reply"])
        self.assertIn("0 questions remain open", workspace.operation(identifier)["reply"])
        self.assertEqual(set(self.store.read()["workspace_operations"]), {identifier})
        starts = [row for row in self.store.events() if row["event"] == "operation.started"]
        self.assertEqual(len(starts), 2)
        self.assertEqual({row["fields"]["client_operation_id"] for row in starts}, {identifier})
        detail = workspace.history(self.store.read()["revision"])
        self.assertEqual(len(detail["metrics"]["calls"]), 2)
        self.assertEqual(detail["metrics"]["input_tokens"], 200)
        self.assertEqual(detail["metrics"]["output_tokens"], 100)
        self.assertEqual(len([row for row in detail["trace"] if row["event"] == "operation.failed"]), 1)
        self.assertEqual(len([row for row in detail["trace"] if row["event"] == "spec.proposed"]), 1)

        self.store.close()
        self.store = DesignSessionStore(self.root)
        fresh_expert = ScriptedExpert([])
        reopened = DesignWorkspace(DesignAgent(self.store, fresh_expert))
        reopened.reconcile_interrupted()
        reopened.snapshot()
        reopened.conversation()
        resumed = asyncio.run(reopened.submit_discussion(
            "Synthetic workspace correction", client_operation_id=identifier, expected_revision=0))
        self.assertEqual(resumed["phase"], "completed")
        self.assertEqual(fresh_expert.seen, [])
        self.assertEqual(self.store.read()["calls"], 2)

    def test_each_correction_uses_normal_spend_accounting(self) -> None:
        expert = ScriptedExpert([zero_width_proposal(), discussion(specification())],
                                provider="openai", model="gpt-5.6-terra")
        agent = DesignAgent(self.store, expert, policy=DesignPolicy(provider_model="gpt-5.6-terra"))
        agent.configure_provider("gpt-5.6-terra", 5_000_000_000)
        state = asyncio.run(agent.discuss("Synthetic metered correction"))
        expected = 2 * estimated_cost_nano("gpt-5.6-terra", 100, 50)
        self.assertEqual(state["provider"]["spent_nano_usd"], expected)
        self.assertIsNone(state["provider"]["pending"])
        self.assertFalse(state["provider"]["uncertain"])
        receipts = [row for row in self.store.events() if row["event"] == "operation.received"]
        self.assertEqual(sum(row["fields"]["estimated_cost_nano_usd"] for row in receipts), expected)

    def test_rejected_candidate_is_captured_only_when_details_are_enabled(self) -> None:
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temporary:
                store = DesignSessionStore(Path(temporary).resolve() / "session", create=True)
                try:
                    traces = DesignTraceStore(store, enabled=enabled)
                    invalid = zero_width_proposal()
                    expert = ScriptedExpert([invalid, discussion(specification())])
                    agent = DesignAgent(store, expert, trace_store=traces)
                    asyncio.run(agent.discuss("SYNTHETIC ORIGINAL PRIVATE REQUEST"))
                    records = traces.records({row[2] for row in expert.seen})
                    outputs = [row["payload"]["output"] for row in records
                               if row["category"] == "assistant_output"]
                    self.assertEqual(outputs, [invalid, discussion(specification())] if enabled else [])
                    public = canonical({"state": store.read(), "events": store.events()}).decode()
                    self.assertNotIn("SYNTHETIC ORIGINAL PRIVATE REQUEST", public)
                    self.assertNotIn("SYNTHETIC REJECTED CANDIDATE CONTENT", public)
                    self.assertNotIn("discovery_correction", public)
                finally:
                    store.close()

    def test_provider_exception_does_not_enter_correction_loop(self) -> None:
        expert = ScriptedExpert([RuntimeError("SYNTHETIC PRIVATE PROVIDER FAILURE"),
                                 discussion(specification())])
        agent = DesignAgent(self.store, expert)
        with self.assertRaisesRegex(ValueError, "expert_invocation_failed"):
            asyncio.run(agent.discuss("Synthetic transport failure"))
        self.assertEqual(len(expert.seen), 1)
        self.assertEqual(self.store.read()["calls"], 1)
        self.assertEqual(len(self.failures()), 1)
        self.assertNotIn("validation_code", self.failures()[0]["fields"])
        self.assertNotIn("SYNTHETIC PRIVATE PROVIDER FAILURE", canonical(self.store.events()).decode())

    def test_authority_and_raw_memory_failures_are_not_sent_for_correction(self) -> None:
        message = "Synthetic request reserved for the memory security test"
        reply = "Synthetic proposed response reserved for the memory security test"
        for text, provenance, expected in (
                ("A width decision remains unreviewed", "user_confirmed", "expert_cannot_confirm_user_memory"),
                (message, "agent_proposal", "raw_prompt_in_engineering_memory"),
                (reply, "agent_proposal", "raw_reply_in_engineering_memory")):
            with self.subTest(code=expected):
                invalid = discussion(specification(), reply=reply, memory=[{
                    "id": "security.boundary", "kind": "decision", "text": text, "provenance": provenance}])
                expert = ScriptedExpert([invalid, discussion(specification())])
                agent = DesignAgent(self.store, expert)
                before_calls = self.store.read()["calls"]
                with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                    asyncio.run(agent.discuss(message))
                self.assertEqual(len(expert.seen), 1)
                self.assertEqual(self.store.read()["calls"] - before_calls, 1)
                self.assertEqual(self.store.read()["engineering_memory"], [])
                self.assertIsNone(self.store.read()["spec"])
                self.assertEqual(self.failures()[-1]["fields"]["validation_code"], expected)
                self.assertNotIn("security.boundary", canonical(self.store.read()).decode())

    def test_resolved_question_history_survives_reopen_without_private_traces(self) -> None:
        question = {"id": "fifo.width", "kind": "question", "text": "Choose the FIFO data width",
                    "provenance": "agent_proposal"}
        decision = {"id": "fifo.width_choice", "kind": "decision", "text": "Data width is four bits",
                    "provenance": "agent_proposal"}
        resolved = discussion(specification(), memory=[decision])
        resolved["resolved_questions"] = [{"id": "fifo.width", "resolution_id": "fifo.width_choice"}]
        first_expert = ScriptedExpert([
            discussion(None, memory=[question], questions=["fifo.width"]), resolved,
        ])
        agent = DesignAgent(self.store, first_expert)
        asyncio.run(agent.discuss("Synthetic FIFO question planning"))
        asyncio.run(agent.discuss("Use four bits"))
        self.assertEqual(self.store.read()["spec"]["questions"], [])
        self.assertFalse(DesignTraceStore(self.store).status()["available"])

        self.store.close()
        self.store = DesignSessionStore(self.root)
        renamed = {**question, "id": "renamed.data_width"}
        invalid = discussion(specification(), memory=[renamed], questions=["renamed.data_width"])
        expert = ScriptedExpert([invalid, discussion(specification())])
        reopened = DesignAgent(self.store, expert)
        state = asyncio.run(reopened.discuss("Expand the existing specification details"))

        self.assertEqual(len(expert.seen), 2)
        self.assertIn({"id": "fifo.width", "text": question["text"]}, expert.seen[0][1]["question_history"])
        self.assertEqual(self.failures()[-1]["fields"]["validation_code"], "expert_clarification_repeated")
        self.assertEqual(expert.seen[1][1]["discovery_correction"]["candidate"],
                         compact_candidate(invalid))
        self.assertEqual(state["spec"]["questions"], [])
        self.assertTrue(all(row["kind"] != "question" for row in state["engineering_memory"]))
        self.assertIn(decision, state["engineering_memory"])
        self.assertNotIn("renamed.data_width", canonical(state).decode())
        self.assertFalse(DesignTraceStore(self.store).status()["available"])

    def test_expired_shared_deadline_prevents_another_correction_call(self) -> None:
        class ExpiredExpert(ScriptedExpert):
            timeout_seconds = 0.1

            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                reply = await super().generate(stage, context, operation_id)
                await asyncio.sleep(0.12)
                return reply

        expert = ExpiredExpert([zero_width_proposal(), discussion(specification())])
        agent = DesignAgent(self.store, expert)
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss("Synthetic expired correction deadline"))
        self.assertEqual(len(expert.seen), 1)
        self.assertEqual(self.store.read()["calls"], 1)
        self.assertEqual(len(self.failures()), 1)
        self.assertEqual(self.failures()[0]["fields"]["validation_code"], "port_width_invalid")

    def test_cancellation_during_correction_does_not_start_another_attempt(self) -> None:
        class WaitingExpert(ScriptedExpert):
            async def generate(self, stage: str, context: JsonObject, operation_id: str) -> ExpertReply:
                if not self.seen:
                    return await super().generate(stage, context, operation_id)
                self.seen.append((stage, copy.deepcopy(context), operation_id))
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("cancelled call returned")

        entered = asyncio.Event()
        expert = WaitingExpert([zero_width_proposal()])
        agent = DesignAgent(self.store, expert)

        async def run() -> None:
            task = asyncio.create_task(agent.discuss("Synthetic cancelled correction"))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(run())
        self.assertEqual(len(expert.seen), 2)
        self.assertEqual(self.store.read()["calls"], 2)
        self.assertIsNone(self.store.read()["spec"])


if __name__ == "__main__":
    unittest.main()
