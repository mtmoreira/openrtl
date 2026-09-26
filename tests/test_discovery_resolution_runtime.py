"""Synthetic resolved-question reuse across real adapter composition and reopen."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

from agentrig.integrations.ollama import OllamaAgentRuntime, OllamaRuntimeOptions
from agentrig.integrations.ollama.sdk import OllamaSdkClientFactory
from openrtl.adapters.design_generation import OllamaDesignExpert, _schema
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.domain.design_session import JsonObject, canonical
from tests.test_design_transport_runtime import MODEL, ScriptedRawSdk, keyed_proposal
from tests.test_hardware_specification import parameterized_fifo_spec


class DiscoveryResolutionRuntimeTest(unittest.TestCase):
    def lifecycle(self, *, capture: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve() / "session"
            store = DesignSessionStore(root, create=True)
            try:
                questions = [
                    {"id": "queue.reset", "kind": "question", "text": "Choose reset polarity",
                     "provenance": "agent_proposal"},
                    {"id": "queue.full", "kind": "question", "text": "Choose behavior while full",
                     "provenance": "agent_proposal"},
                ]
                initial: JsonObject = {
                    "reply": "structured", "specification": None,
                    "engineering_memory": questions,
                    "questions_asked": [row["id"] for row in questions],
                    "resolved_questions": [],
                    "question_plan": [
                        {"id": row["id"], "topic": topic, "reason": "Required interface decision"}
                        for row, topic in zip(questions, ("clock_reset", "behavior"), strict=True)
                    ],
                }
                spec = parameterized_fifo_spec()
                proposal = keyed_proposal(spec)
                proposal["engineering_memory"] = [
                    {"id": "queue.reset.answer", "kind": "decision", "text": "Reset is active low",
                     "provenance": "agent_proposal"},
                    {"id": "queue.full.answer", "kind": "decision", "text": "Full queues apply backpressure",
                     "provenance": "agent_proposal"},
                ]
                proposal["resolved_questions"] = [
                    {"id": row["id"], "resolution_id": row["id"] + ".answer"}
                    for row in questions
                ]

                def compose(outputs: list[JsonObject]) -> tuple[DesignAgent, ScriptedRawSdk, DesignTraceStore]:
                    raw = ScriptedRawSdk(outputs)

                    def build(host: str, headers: object) -> ScriptedRawSdk:
                        self.assertEqual(host, "http://127.0.0.1:11434")
                        self.assertEqual(headers, {})
                        return raw

                    schema_id, schema = _schema("discovery", {}, ollama=True)
                    runtime = OllamaAgentRuntime(
                        client_factory=OllamaSdkClientFactory(
                            host="http://127.0.0.1:11434", raw_client_builder=build),
                        model=MODEL, output_schemas={schema_id: schema},
                        options=OllamaRuntimeOptions(temperature=0, max_output_tokens=16000, think=False),
                    )
                    trace = DesignTraceStore(store, enabled=capture)
                    expert = OllamaDesignExpert(runtime, model=MODEL)
                    expert.trace_store = trace
                    return (DesignAgent(store, expert, trace_store=trace,
                                        policy=DesignPolicy(max_discovery_corrections=2)), raw, trace)

                agent, raw, _ = compose([initial, proposal])
                asyncio.run(agent.discuss("Design a queue"))
                accepted = asyncio.run(agent.discuss("Use active-low reset and backpressure"))
                self.assertEqual(len(raw.calls), 2)
                self.assertEqual(accepted["spec"], spec)
                self.assertEqual(accepted["spec"]["questions"], [])
                saved_memory = copy.deepcopy(accepted["engineering_memory"])
                self.assertEqual(len(saved_memory), 4)
                self.assertTrue(all(row["kind"] == "decision" for row in saved_memory))
                store.close()
                store = DesignSessionStore(root)
                agent, repeated_raw, trace = compose([proposal])
                repeated = asyncio.run(agent.discuss("Expand the saved specification"))

                self.assertEqual(len(repeated_raw.calls), 1)
                self.assertEqual(repeated_raw.closed, 1)
                context = json.loads(repeated_raw.calls[0]["messages"][1]["content"])["context"]
                self.assertNotIn("discovery_correction", context)
                self.assertEqual({row["id"] for row in context["question_history"]},
                                 {row["id"] for row in questions})
                self.assertEqual(context["conversation_policy"]["existing_question_ids"], [])
                self.assertEqual(repeated["spec"], accepted["spec"])
                self.assertEqual(repeated["engineering_memory"], saved_memory)
                self.assertEqual(repeated["calls"], 3)
                self.assertIsNone(repeated["approved_spec"])
                self.assertIsNone(repeated["active"])
                self.assertIsNone(repeated["last_error"])
                events = store.events()
                self.assertFalse(any(row["event"] == "operation.failed" for row in events))
                receipts = [row for row in events if row["event"] == "operation.received"]
                self.assertEqual(len(receipts), 3)
                identifiers = {row["fields"]["operation_id"] for row in receipts}
                self.assertEqual(len(identifiers), 3)
                for row in receipts:
                    self.assertEqual((row["fields"]["input_tokens"], row["fields"]["output_tokens"]), (101, 203))
                records = trace.records(identifiers)
                if capture:
                    last_id = receipts[-1]["fields"]["operation_id"]
                    last_records = trace.records({last_id})
                    response = json.loads(next(row["payload"]["content"] for row in last_records
                                               if row["category"] == "provider_trace" and
                                               row["payload"]["kind"] == "provider.response"))
                    self.assertEqual(json.loads(response["content"]), proposal)
                    decoded = next(row["payload"]["output"] for row in last_records
                                   if row["category"] == "assistant_output")
                    self.assertEqual(decoded["specification"], spec)
                    self.assertEqual(decoded["resolved_questions"], proposal["resolved_questions"])
                    self.assertEqual(decoded["engineering_memory"], proposal["engineering_memory"])
                else:
                    self.assertEqual(records, [])
                self.assertNotIn("thinking", canonical(events).decode())
            finally:
                store.close()

    def test_repeated_resolution_survives_reopen_with_private_capture(self) -> None:
        self.lifecycle(capture=True)

    def test_repeated_resolution_survives_reopen_without_private_capture(self) -> None:
        self.lifecycle(capture=False)


if __name__ == "__main__":
    unittest.main()
