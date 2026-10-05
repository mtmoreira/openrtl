"""Synthetic raw SDK responses through the real Ollama/runtime/application stack.

Ollama replaces only the raw SDK client; OpenAI uses AgentRig's scripted generator.
No provider, socket, credential, or optional Ollama SDK dependency is used, and
these tests do not establish live reliability.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from typing import Any
from unittest.mock import patch

from agentrig.capabilities import (
    CapabilityDescriptor, CapabilityFeature, CapabilityKind, CapabilityLimit,
    DataRetention, GenerationUsage, ModelMetadata, TextGenerationFinishReason,
)
from agentrig.integrations.ollama import OLLAMA_CLIENT_VERSION
from agentrig.integrations.ollama.sdk import OllamaSdkClientFactory
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters.design_generation import AgentRigDesignExpert, ollama_design_expert
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.domain.design_readiness import CATEGORIES
from openrtl.domain.design_session import JsonObject, canonical, content_digest
from openrtl.domain.discovery_validation import IDENTIFIER_EXPECTATION
from openrtl.domain.hardware_specification import SECTION_IDS
from openrtl.domain.provider_controls import estimated_cost_nano
from tests.test_hardware_specification import parameterized_fifo_spec


MODEL = "qwen3.5:27b"
THINKING_MARKER = "synthetic private SDK reasoning marker"


def keyed_proposal(specification: JsonObject) -> JsonObject:
    """Build provider wire data independently of the production converter."""
    spec = copy.deepcopy(specification)
    spec["rtl_top_module_name"] = spec.pop("top")
    # Deliberately scramble JSON member insertion order: canonical persisted
    # ordering comes from the domain contract, not the provider's object order.
    spec["hardware_specification"]["sections"] = {
        row["id"]: {"status": row["status"], "content": row["content"]}
        for row in reversed(specification["hardware_specification"]["sections"])
    }
    spec["readiness"]["items"] = {
        row["category"]: {key: copy.deepcopy(row[key]) for key in
                          ("status", "decision", "requirement_ids", "ports")}
        for row in reversed(specification["readiness"]["items"])
    }
    return {"reply": "structured", "specification": spec, "engineering_memory": [],
            "questions_asked": [], "resolved_questions": [], "question_plan": []}


class ScriptedRawSdk:
    def __init__(self, outputs: list[JsonObject]) -> None:
        self.outputs = copy.deepcopy(outputs)
        self.calls: list[JsonObject] = []
        self.closed = 0

    async def chat(self, **kwargs: Any) -> object:
        index = len(self.calls)
        self.calls.append(copy.deepcopy(kwargs))
        if index >= len(self.outputs):
            raise AssertionError("unexpected extra synthetic provider attempt")
        return SimpleNamespace(
            message=SimpleNamespace(content=canonical(self.outputs[index]).decode(),
                                    thinking=THINKING_MARKER),
            model=MODEL, done_reason="stop", prompt_eval_count=101, eval_count=203,
            total_duration=900, load_duration=100, prompt_eval_duration=200,
            eval_duration=600, created_at="2026-09-24T12:00:00Z",
        )

    async def close(self) -> None:
        self.closed += 1


class DesignTransportRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve() / "project"
        self.store = DesignSessionStore(self.root, create=True)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def composed_agent(self, outputs: list[JsonObject], *, capture: bool = True,
                       corrections: int = 0) -> tuple[DesignAgent, ScriptedRawSdk, DesignTraceStore]:
        raw = ScriptedRawSdk(outputs)

        def build(host: str, headers: object) -> ScriptedRawSdk:
            self.assertEqual(host, "http://127.0.0.1:11434")
            self.assertEqual(headers, {})
            return raw

        # Exercise the production schema registry, not a test-only registration.
        def factory(*, host: str) -> OllamaSdkClientFactory:
            return OllamaSdkClientFactory(host=host, raw_client_builder=build)

        with patch("importlib.metadata.version", return_value=OLLAMA_CLIENT_VERSION), \
                patch("agentrig.integrations.ollama.sdk.OllamaSdkClientFactory", side_effect=factory):
            expert = ollama_design_expert(authorized=True, model=MODEL)
        trace = DesignTraceStore(self.store, enabled=capture)
        expert.trace_store = trace
        agent = DesignAgent(self.store, expert, trace_store=trace,
                            policy=DesignPolicy(max_discovery_corrections=corrections))
        return agent, raw, trace

    def operation_ids(self) -> set[str]:
        return {event["fields"]["operation_id"] for event in self.store.events()
                if event["event"] == "operation.started"}

    def test_complete_keyed_sdk_output_is_saved_canonically_with_original_private_capture(self) -> None:
        expected = parameterized_fifo_spec()
        provider_output = keyed_proposal(expected)
        agent, raw, trace = self.composed_agent([provider_output])

        state = asyncio.run(agent.discuss("Design a parameterized FIFO"))

        self.assertEqual(state["spec"], expected)
        self.assertEqual(content_digest(state["spec"]), content_digest(expected))
        self.assertEqual(state["calls"], 1)
        self.assertIsNone(state["active"])
        self.assertEqual(raw.closed, 1)
        self.assertEqual(raw.outputs, [provider_output])
        self.assertEqual(self.store.read()["spec"], expected)
        records = trace.records(self.operation_ids())
        response = json.loads(next(row["payload"]["content"] for row in records
                                   if row["category"] == "provider_trace" and
                                   row["payload"]["kind"] == "provider.response"))
        self.assertEqual(json.loads(response["content"]), provider_output)
        self.assertEqual(response["thinking"], THINKING_MARKER)
        self.assertEqual((response["input_tokens"], response["output_tokens"]), (101, 203))
        accepted = next(row["payload"]["output"] for row in records
                        if row["category"] == "assistant_output")
        self.assertEqual(accepted["specification"], expected)
        self.assertIsInstance(response["content"], str)
        self.assertIsInstance(accepted["specification"]["hardware_specification"]["sections"], list)
        metrics = next(event["fields"] for event in self.store.events()
                       if event["event"] == "operation.received")
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"]), (101, 203))
        self.assertNotIn(THINKING_MARKER, str(self.store.events()) + str(state))

    def test_required_inventory_keys_reach_the_actual_sdk_format_argument(self) -> None:
        agent, raw, _ = self.composed_agent([keyed_proposal(parameterized_fifo_spec())])
        asyncio.run(agent.discuss("Design a parameterized FIFO"))

        self.assertEqual(len(raw.calls), 1)
        call = raw.calls[0]
        self.assertEqual(call["model"], MODEL)
        self.assertIs(call["stream"], False)
        self.assertIs(call["think"], False)
        self.assertNotIn("tools", call)
        self.assertEqual(call["options"], {"temperature": 0.0, "num_predict": 16000})
        spec = call["format"]["properties"]["specification"]["anyOf"][0]["properties"]
        self.assertNotIn("top", spec)
        self.assertIn("RTL top-level module", spec["rtl_top_module_name"]["description"])
        for block, field, identifiers, forbidden in (
            ("hardware_specification", "sections", SECTION_IDS, "id"),
            ("readiness", "items", CATEGORIES, "category"),
        ):
            inventory = spec[block]["properties"][field]
            self.assertEqual(inventory["type"], "object")
            self.assertFalse(inventory["additionalProperties"])
            self.assertEqual(set(inventory["required"]), set(identifiers))
            self.assertEqual(set(inventory["properties"]), set(identifiers))
            for row in inventory["properties"].values():
                self.assertFalse(row["additionalProperties"])
                self.assertEqual(set(row["required"]), set(row["properties"]))
                self.assertNotIn(forbidden, row["properties"])

    def test_captured_seven_section_array_shape_is_rejected_without_filling_content(self) -> None:
        spec = parameterized_fifo_spec()
        spec["hardware_specification"]["sections"] = [
            row for row in spec["hardware_specification"]["sections"] if row["id"] != "integration"]
        rejected = keyed_proposal(parameterized_fifo_spec())
        rejected["specification"] = spec
        self.assertEqual(len(spec["hardware_specification"]["sections"]), 7)
        agent, raw, trace = self.composed_agent([rejected])

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss("Design a parameterized FIFO"))

        state = self.store.read()
        self.assertIsNone(state["spec"])
        self.assertIsNone(state["active"])
        self.assertEqual(state["calls"], 1)
        self.assertEqual(raw.closed, 1)
        self.assertFalse(any(event["event"] == "spec.proposed" for event in self.store.events()))
        response = json.loads(next(row["payload"]["content"] for row in trace.records(self.operation_ids())
                                   if row["category"] == "provider_trace" and
                                   row["payload"]["kind"] == "provider.response"))
        self.assertEqual(json.loads(response["content"]), rejected)
        metrics = next(row["payload"] for row in trace.records(self.operation_ids())
                       if row["category"] == "provider_metrics")
        self.assertEqual((metrics["input_tokens"], metrics["output_tokens"]), (101, 203))
        received = next(event["fields"] for event in self.store.events()
                        if event["event"] == "operation.received")
        self.assertEqual((received["input_tokens"], received["output_tokens"]), (101, 203))
        failure = next(event for event in self.store.events() if event["event"] == "operation.failed")
        self.assertEqual(failure["fields"]["validation_code"], "hardware_specification_sections_missing")
        assistant = next(row["payload"]["output"] for row in trace.records(self.operation_ids())
                         if row["category"] == "assistant_output")
        self.assertEqual(assistant, rejected)

    def test_partial_keyed_sdk_output_preserves_usage_and_is_corrected_without_filling_content(self) -> None:
        expected = parameterized_fifo_spec()
        rejected = keyed_proposal(expected)
        del rejected["specification"]["hardware_specification"]["sections"]["integration"]
        agent, raw, trace = self.composed_agent([rejected, keyed_proposal(expected)], corrections=1)

        state = asyncio.run(agent.discuss("Design a parameterized FIFO"))

        self.assertEqual(state["spec"], expected)
        self.assertEqual(state["calls"], 2)
        self.assertEqual(raw.closed, 2)
        received = [event for event in self.store.events() if event["event"] == "operation.received"]
        self.assertEqual(len(received), 2)
        for event in received:
            self.assertEqual((event["fields"]["input_tokens"], event["fields"]["output_tokens"]), (101, 203))
        failure = next(event for event in self.store.events() if event["event"] == "operation.failed")
        self.assertEqual(failure["fields"]["validation_code"], "list_invalid")
        self.assertIsNone(self.store.historical_state(failure["sequence"])["spec"])
        correction = json.loads(raw.calls[1]["messages"][1]["content"])["context"]["discovery_correction"]
        self.assertEqual(correction["validation_code"], "list_invalid")
        self.assertEqual(correction["candidate"], rejected)
        failed_id = received[0]["fields"]["operation_id"]
        captured = next(row["payload"]["output"] for row in trace.records({failed_id})
                        if row["category"] == "assistant_output")
        decoded_rejected = copy.deepcopy(rejected)
        decoded_rejected["specification"]["top"] = decoded_rejected["specification"].pop("rtl_top_module_name")
        self.assertEqual(captured, decoded_rejected)

    def test_captured_schema_label_is_located_and_corrected_through_native_ollama(self) -> None:
        expected = parameterized_fifo_spec()
        invalid = "openrtl.hardware-specification.v1"
        for provider_field in ("top", "rtl_top_module_name"):
            with self.subTest(provider_field=provider_field):
                rejected = keyed_proposal(expected)
                rejected["specification"].pop("rtl_top_module_name")
                rejected["specification"][provider_field] = invalid
                before_calls = self.store.read()["calls"]
                agent, raw, trace = self.composed_agent([rejected, keyed_proposal(expected)], corrections=1)

                state = asyncio.run(agent.discuss("Refine the FIFO specification"))

                self.assertEqual(state["spec"], expected)
                self.assertEqual(state["calls"] - before_calls, 2)
                self.assertEqual(raw.closed, 2)
                correction = json.loads(raw.calls[1]["messages"][1]["content"])["context"]["discovery_correction"]
                self.assertEqual(correction["validation_code"], "identifier_invalid")
                self.assertEqual(correction["validation_feedback"], {
                    "field": "specification.rtl_top_module_name", "expected": IDENTIFIER_EXPECTATION})
                candidate = correction["candidate"]["specification"]
                self.assertNotIn("top", candidate)
                self.assertEqual(candidate["rtl_top_module_name"], invalid)
                self.assertIs(type(candidate["rtl_top_module_name"]), str)
                failures = [row for row in self.store.events() if row["event"] == "operation.failed"]
                failed_id = failures[-1]["fields"]["operation_id"]
                self.assertEqual(failures[-1]["fields"]["validation_code"], "identifier_invalid")
                records = trace.records({failed_id})
                response = json.loads(next(row["payload"]["content"] for row in records
                                           if row["category"] == "provider_trace" and
                                           row["payload"]["kind"] == "provider.response"))
                self.assertEqual(json.loads(response["content"]), rejected)
                captured = next(row["payload"]["output"] for row in records
                                if row["category"] == "assistant_output")
                self.assertEqual(captured["specification"]["top"], invalid)
                self.assertIs(type(captured["specification"]["top"]), str)
                received = next(row["fields"] for row in self.store.events()
                                if row["event"] == "operation.received" and row["fields"]["operation_id"] == failed_id)
                self.assertEqual((received["input_tokens"], received["output_tokens"]), (101, 203))
                self.assertNotIn("validation_feedback", str(self.store.events()))
                self.assertNotIn(THINKING_MARKER, str(self.store.events()) + str(state))

    def test_ambiguous_module_fields_are_captured_and_rejected_after_accounting(self) -> None:
        rejected = keyed_proposal(parameterized_fifo_spec())
        rejected["specification"]["top"] = "different_top"
        agent, raw, trace = self.composed_agent([rejected])
        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss("Design a parameterized FIFO"))
        self.assertEqual(self.store.read()["calls"], 1)
        self.assertIsNone(self.store.read()["spec"])
        self.assertEqual(raw.closed, 1)
        records = trace.records(self.operation_ids())
        captured = next(row["payload"]["output"] for row in records if row["category"] == "assistant_output")
        self.assertEqual(captured, rejected)
        received = next(row["fields"] for row in self.store.events() if row["event"] == "operation.received")
        self.assertEqual((received["input_tokens"], received["output_tokens"]), (101, 203))
        failure = next(row["fields"] for row in self.store.events() if row["event"] == "operation.failed")
        self.assertEqual(failure["validation_code"], "object_fields_invalid")

    def test_legacy_canonical_response_still_passes_strict_domain_validation(self) -> None:
        expected = parameterized_fifo_spec()
        legacy = keyed_proposal(expected)
        legacy["specification"] = copy.deepcopy(expected)
        agent, raw, _ = self.composed_agent([legacy])
        state = asyncio.run(agent.discuss("Design a parameterized FIFO"))
        self.assertEqual(state["spec"], expected)
        self.assertEqual(raw.outputs, [legacy])

    def test_feature_schema_registration_and_context_alias_use_the_same_contract(self) -> None:
        expected = parameterized_fifo_spec()
        output = {"summary": "Proposed feature", "specification": keyed_proposal(expected)["specification"],
                  "stage_paths": {}, "manifest": None}
        agent, raw, _ = self.composed_agent([output])
        reply = asyncio.run(agent.expert.generate("change_planning", {
            "improvement_intent": "feature", "specification": expected}, "a" * 32))
        self.assertEqual(reply.output["specification"], expected)
        schema = raw.calls[0]["format"]["properties"]["specification"]
        self.assertIn("rtl_top_module_name", schema["required"])
        self.assertNotIn("top", schema["properties"])
        context = json.loads(raw.calls[0]["messages"][1]["content"])["context"]
        self.assertEqual(context["specification"]["rtl_top_module_name"], expected["top"])
        self.assertNotIn("top", context["specification"])

    def test_partial_keyed_openai_output_settles_known_spend_before_correction(self) -> None:
        expected = parameterized_fifo_spec()
        rejected = keyed_proposal(expected)
        del rejected["specification"]["hardware_specification"]["sections"]["integration"]
        model = "gpt-5.6-terra"
        generator = ScriptedStructuredGenerator[JsonObject](
            descriptor=CapabilityDescriptor(
                capability_id="openai.responses.structured_generation", version="test",
                kind=CapabilityKind.STRUCTURED_GENERATION,
                features=frozenset({CapabilityFeature.STRUCTURED_OUTPUT}),
                limits={CapabilityLimit.MAX_OUTPUT_TOKENS: 32768},
                data_retention=DataRetention.PROVIDER_MANAGED),
            outcomes=tuple(ScriptedStructuredGeneration(
                encoded_output=output,
                usage=GenerationUsage(input_tokens=101, output_tokens=203),
                model=ModelMetadata(provider="openai", model_id=model),
                finish_reason=TextGenerationFinishReason.COMPLETED)
                for output in (rejected, keyed_proposal(expected))),
        )
        expert = AgentRigDesignExpert(generator, model=model)
        agent = DesignAgent(self.store, expert,
                            policy=DesignPolicy(provider_model=model, max_discovery_corrections=1))
        agent.configure_provider(model, 5_000_000_000)

        state = asyncio.run(agent.discuss("Design a parameterized FIFO"))

        cost = estimated_cost_nano(model, 101, 203)
        self.assertEqual(state["spec"], expected)
        self.assertEqual(state["calls"], 2)
        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(state["provider"]["spent_nano_usd"], 2 * cost)
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])
        received = [event["fields"] for event in self.store.events() if event["event"] == "operation.received"]
        self.assertEqual(len(received), 2)
        for fields in received:
            self.assertEqual((fields["input_tokens"], fields["output_tokens"]), (101, 203))
            self.assertEqual(fields["estimated_cost_nano_usd"], cost)
        failure = next(event for event in self.store.events() if event["event"] == "operation.failed")
        self.assertEqual(failure["fields"]["validation_code"], "list_invalid")
        after_failure = self.store.historical_state(failure["sequence"])
        self.assertIsNone(after_failure["spec"])
        self.assertEqual(after_failure["provider"]["spent_nano_usd"], cost)
        self.assertFalse(after_failure["provider"]["uncertain"])
        self.assertIsNone(after_failure["provider"]["pending"])
        correction = json.loads(generator.calls[1].request.input.prompt)["context"]["discovery_correction"]
        self.assertEqual(correction["validation_code"], "list_invalid")
        self.assertEqual(correction["candidate"], rejected)

    def test_v10_keyed_output_still_requires_the_question_plan(self) -> None:
        rejected = keyed_proposal(parameterized_fifo_spec())
        del rejected["question_plan"]
        agent, raw, _ = self.composed_agent([rejected])

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss("Design a parameterized FIFO"))

        self.assertEqual(len(raw.calls), 1)
        self.assertIsNone(self.store.read()["spec"])
        failure = next(event for event in self.store.events() if event["event"] == "operation.failed")
        self.assertEqual(failure["fields"]["validation_code"], "expert_discussion_fields_invalid")

    def test_keyed_output_keeps_width_and_readiness_anchor_validation_and_correction(self) -> None:
        expected = parameterized_fifo_spec()
        for case, validation_code in (("width", "port_width_invalid"),
                                      ("anchors", "readiness_port_decisions_missing")):
            with self.subTest(case=case):
                rejected = keyed_proposal(expected)
                if case == "width":
                    rejected["specification"]["ports"][4]["width"] = 0
                else:
                    rejected["specification"]["readiness"]["items"]["widths_signedness"]["ports"] = [
                        "push_data", "pop_data"]
                corrected = keyed_proposal(expected)
                agent, raw, _ = self.composed_agent([rejected, corrected], corrections=1)
                previous_calls = self.store.read()["calls"]

                state = asyncio.run(agent.discuss("Refine the FIFO specification"))

                self.assertEqual(state["spec"], expected)
                self.assertEqual(state["calls"] - previous_calls, 2)
                self.assertEqual(len(raw.calls), 2)
                self.assertEqual(raw.closed, 2)
                failures = [event for event in self.store.events() if event["event"] == "operation.failed"]
                self.assertEqual(failures[-1]["fields"]["validation_code"], validation_code)
                request = json.loads(raw.calls[1]["messages"][1]["content"])["context"]
                correction = request["discovery_correction"]
                self.assertEqual(correction["validation_code"], validation_code)
                self.assertEqual(correction["candidate"], rejected)
                self.assertNotIn("discovery_deadline", request)
                if case == "anchors":
                    self.assertEqual(request["specification"], corrected["specification"])
                # Domain snapshots retain canonical arrays even though the
                # rejected proposal is sent back to the model as keyed objects.
                self.assertIsInstance(state["spec"]["readiness"]["items"], list)

    def test_capture_disabled_does_not_persist_rejected_provider_content(self) -> None:
        rejected = keyed_proposal(parameterized_fifo_spec())
        marker = "synthetic rejected private proposal marker"
        message = "synthetic private request marker"
        rejected["specification"]["title"] = marker
        rejected["specification"]["ports"][4]["width"] = 0
        agent, raw, trace = self.composed_agent([rejected], capture=False)

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(agent.discuss(message))

        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(raw.closed, 1)
        self.assertEqual(trace.records(self.operation_ids()), [])
        self.assertFalse(trace.status()["available"])
        persisted = " ".join(row[0] for table in ("snapshots", "events")
                             for row in self.store.connection.execute(f"SELECT payload FROM {table}"))
        for private in (marker, message, THINKING_MARKER):
            self.assertNotIn(private, persisted)
        self.assertIsNone(self.store.read()["spec"])


if __name__ == "__main__":
    unittest.main()
