"""Synthetic document correction through the production provider boundaries.

Ollama replaces only its raw SDK client, and OpenAI uses AgentRig's scripted
generator. These tests make no network or model calls and are not live evidence.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.capabilities import (
    CapabilityDescriptor, CapabilityFeature, CapabilityKind, CapabilityLimit,
    DataRetention, GenerationUsage, ModelMetadata, TextGenerationFinishReason,
)
from agentrig.integrations.ollama import OLLAMA_CLIENT_VERSION
from agentrig.integrations.ollama.sdk import OllamaSdkClientFactory
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters.design_generation import AgentRigDesignExpert, _instructions, _schema, ollama_design_expert
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.domain.design_session import JsonObject, STAGES, content_digest
from openrtl.domain.provider_controls import estimated_cost_nano
from tests.test_design_agent import FakeExpert, contribution, specification
from tests.test_design_transport_runtime import MODEL, THINKING_MARKER, ScriptedRawSdk


OPENAI_MODEL = "gpt-5.6-terra"
REJECTED_MARKER = "synthetic private competing verification draft"


def document_outputs() -> tuple[JsonObject, JsonObject]:
    rejected = contribution("verification_plan")
    rejected["files"] = [
        {"path": "docs/verification-plan.md", "content": "# First draft\n" + REJECTED_MARKER + " A\n"},
        {"path": "docs/verification-plan.md", "content": "# Competing draft\n" + REJECTED_MARKER + " B\n"},
    ]
    corrected = contribution("verification_plan")
    corrected["files"][0]["content"] = (
        "# Complete verification plan\n"
        "For wire.transfer, independently compare all 16 input values with the reference model.\n"
        "Check zero, maximum, alternating patterns and seeded transitions.\n"
    )
    corrected["files"].append({
        "path": "docs/verification-cases.md",
        "content": "# Test matrix\nwire.transfer: directed enumeration and seeded transitions.\n",
    })
    return rejected, corrected


class DocumentRecoveryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = DesignSessionStore(Path(self.temporary.name).resolve() / "project", create=True)
        self.agent = DesignAgent(self.store, FakeExpert())
        self.agent.propose(specification())
        self.agent.approve(content_digest(specification()))
        asyncio.run(self.agent.advance())
        self.before = self.store.read()
        self.assertEqual(self.before["stage"], 1)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def native_ollama(self, outputs: list[JsonObject], *, capture: bool = True
                      ) -> tuple[ScriptedRawSdk, DesignTraceStore]:
        raw = ScriptedRawSdk(outputs)

        def build(host: str, headers: object) -> ScriptedRawSdk:
            self.assertEqual(host, "http://127.0.0.1:11434")
            self.assertEqual(headers, {})
            return raw

        def factory(*, host: str) -> OllamaSdkClientFactory:
            return OllamaSdkClientFactory(host=host, raw_client_builder=build)

        with patch("importlib.metadata.version", return_value=OLLAMA_CLIENT_VERSION), \
                patch("agentrig.integrations.ollama.sdk.OllamaSdkClientFactory", side_effect=factory):
            expert = ollama_design_expert(authorized=True, model=MODEL)
        trace = DesignTraceStore(self.store, enabled=capture)
        expert.trace_store = trace
        self.agent = DesignAgent(self.store, expert, trace_store=trace,
                                 policy=DesignPolicy(max_document_corrections=1))
        self.assertEqual(raw.calls, [])
        self.assertEqual(raw.closed, 0)
        return raw, trace

    def scripted_openai(self, outputs: list[JsonObject]) -> ScriptedStructuredGenerator[JsonObject]:
        generator = ScriptedStructuredGenerator[JsonObject](
            descriptor=CapabilityDescriptor(
                capability_id="openai.responses.structured_generation", version="test",
                kind=CapabilityKind.STRUCTURED_GENERATION,
                features=frozenset({CapabilityFeature.STRUCTURED_OUTPUT}),
                limits={CapabilityLimit.MAX_OUTPUT_TOKENS: 32768},
                data_retention=DataRetention.PROVIDER_MANAGED),
            outcomes=tuple(ScriptedStructuredGeneration(
                encoded_output=copy.deepcopy(output),
                usage=GenerationUsage(input_tokens=101, output_tokens=203),
                model=ModelMetadata(provider="openai", model_id=OPENAI_MODEL),
                finish_reason=TextGenerationFinishReason.COMPLETED) for output in outputs),
        )
        self.agent = DesignAgent(self.store, AgentRigDesignExpert(generator, model=OPENAI_MODEL),
                                 policy=DesignPolicy(provider_model=OPENAI_MODEL,
                                                     max_document_corrections=1))
        self.agent.configure_provider(OPENAI_MODEL, 5_000_000_000)
        return generator

    def new_events(self) -> list[JsonObject]:
        return [row for row in self.store.events() if row["sequence"] > self.before["revision"]]

    def operation_ids(self) -> set[str]:
        return {row["fields"]["operation_id"] for row in self.new_events()
                if row["event"] == "operation.started"}

    def assert_corrected_once(self, state: JsonObject, corrected: JsonObject) -> None:
        self.assertEqual(state["stage"], 2)
        self.assertEqual(state["calls"] - self.before["calls"], 2)
        self.assertIsNone(state["active"])
        self.assertIsNone(state["last_error"])
        self.assertEqual({p: state["files"][p] for p in self.before["files"]}, self.before["files"])
        expected = {row["path"]: row["content"] for row in corrected["files"]}
        self.assertEqual(set(state["files"]) - set(self.before["files"]), set(expected))
        contents = self.store.contents(state)
        self.assertEqual({path: contents[path] for path in expected}, expected)
        completed = [row for row in self.new_events() if row["event"] == "operation.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["fields"]["artifact_count"], 2)
        failures = [row for row in self.new_events() if row["event"] == "operation.failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["fields"]["validation_code"], "contribution_ownership_invalid")
        failed = self.store.historical_state(failures[0]["sequence"])
        self.assertEqual(failed["files"], self.before["files"])
        self.assertEqual(failed["stage"], 1)
        self.assertIsNone(failed["active"])
        received = [row["fields"] for row in self.new_events() if row["event"] == "operation.received"]
        self.assertEqual(len(received), 2)
        for metrics in received:
            self.assertEqual((metrics["input_tokens"], metrics["output_tokens"]), (101, 203))
        starts = [row["fields"] for row in self.new_events() if row["event"] == "operation.started"]
        self.assertEqual(len(starts), 2)
        for fields in starts:
            self.assertEqual(fields["context_schema"], "openrtl.design-context.v10")
            self.assertEqual(fields["prompt_version"], "openrtl.design.instructions.v14")
        persisted = " ".join(row[0] for table in ("snapshots", "events")
                             for row in self.store.connection.execute(f"SELECT payload FROM {table}"))
        for private in (REJECTED_MARKER, THINKING_MARKER, "document_correction", "expert_deadline"):
            self.assertNotIn(private, persisted)
        for row in document_outputs()[0]["files"]:
            self.assertEqual(self.store.connection.execute(
                "SELECT count(*) FROM blobs WHERE content = ?", (row["content"].encode(),)
            ).fetchone()[0], 0)

    def assert_correction_context(self, context: JsonObject, rejected: JsonObject) -> None:
        self.assertEqual(context["schema"], "openrtl.design-context.v10")
        self.assertEqual(context["stage"], "verification_plan")
        correction = context["document_correction"]
        self.assertEqual(correction["attempt"], 1)
        self.assertEqual(correction["validation_code"], "contribution_ownership_invalid")
        self.assertEqual(correction["candidate"], rejected)
        self.assertEqual(correction["validation_feedback"]["field"], "files[1].path")
        self.assertEqual(correction["validation_feedback"]["duplicate_of"], "files[0].path")
        self.assertTrue(correction["validation_feedback"]["expected"])
        self.assertNotIn(REJECTED_MARKER, str(correction["validation_feedback"]))
        self.assertNotIn("expert_deadline", context)
        self.assertNotIn("discovery_deadline", context)
        self.assertNotIn("dv_correction", context)

    def test_native_ollama_duplicate_is_captured_then_replaced_by_complete_multifile_proposal(self) -> None:
        rejected, corrected = document_outputs()
        raw, trace = self.native_ollama([rejected, corrected])

        state = asyncio.run(self.agent.advance())

        self.assert_corrected_once(state, corrected)
        self.assertEqual(raw.outputs, [rejected, corrected])
        self.assertEqual((len(raw.calls), raw.closed), (2, 2))
        for call in raw.calls:
            self.assertEqual(call["model"], MODEL)
            self.assertIs(call["stream"], False)
            self.assertIs(call["think"], False)
            self.assertNotIn("tools", call)
            schema = call["format"]["properties"]["files"]
            self.assertEqual(schema["type"], "array")
            self.assertEqual(set(schema["items"]["properties"]), {"path", "content"})
            self.assertFalse(schema["items"]["additionalProperties"])
        first_context = json.loads(raw.calls[0]["messages"][1]["content"])["context"]
        self.assertNotIn("document_correction", first_context)
        self.assert_correction_context(json.loads(raw.calls[1]["messages"][1]["content"])["context"], rejected)
        self.assertIn("document_correction", raw.calls[1]["messages"][0]["content"])
        records = trace.records(self.operation_ids())
        responses = [json.loads(row["payload"]["content"]) for row in records
                     if row["category"] == "provider_trace" and row["payload"]["kind"] == "provider.response"]
        self.assertEqual([json.loads(row["content"]) for row in responses], [rejected, corrected])
        self.assertTrue(all(row["thinking"] == THINKING_MARKER for row in responses))
        self.assertEqual([row["payload"]["output"] for row in records
                          if row["category"] == "assistant_output"], [rejected, corrected])
        self.assertNotIn("expert_deadline", str(records))

    def test_openai_duplicate_correction_settles_both_attempts_and_keeps_v1_schema(self) -> None:
        rejected, corrected = document_outputs()
        generator = self.scripted_openai([rejected, corrected])

        state = asyncio.run(self.agent.advance())

        self.assert_corrected_once(state, corrected)
        self.assertEqual(len(generator.calls), 2)
        cost = estimated_cost_nano(OPENAI_MODEL, 101, 203)
        self.assertEqual(state["provider"]["spent_nano_usd"], 2 * cost)
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])
        failure = next(row for row in self.new_events() if row["event"] == "operation.failed")
        failed = self.store.historical_state(failure["sequence"])
        self.assertEqual(failed["provider"]["spent_nano_usd"], cost)
        self.assertFalse(failed["provider"]["uncertain"])
        self.assertIsNone(failed["provider"]["pending"])
        for call in generator.calls:
            self.assertEqual(call.request.output_schema.schema_id, "openrtl.design.verification_plan.v1")
        context = json.loads(generator.calls[1].request.input.prompt)["context"]
        self.assert_correction_context(context, rejected)

    def test_native_capture_opt_out_does_not_persist_competing_drafts(self) -> None:
        rejected, corrected = document_outputs()
        raw, trace = self.native_ollama([rejected, corrected], capture=False)

        state = asyncio.run(self.agent.advance())

        self.assert_corrected_once(state, corrected)
        self.assertEqual((len(raw.calls), raw.closed), (2, 2))
        self.assertEqual(trace.records(self.operation_ids()), [])
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='private_traces'"
        ).fetchone()[0], 0)

    def test_document_only_instructions_preserve_the_existing_multifile_v1_contracts(self) -> None:
        for stage in (*STAGES, "discovery", "diagnosis", "signoff", "change_planning", "analyze", "explain"):
            with self.subTest(stage=stage):
                instructions = _instructions(stage, {})
                if stage not in ("architecture", "verification_plan"):
                    self.assertNotIn("document_correction", instructions)
                    continue
                self.assertIn("each document path exactly once", instructions)
                self.assertIn("Multiple distinct documentation paths are supported", instructions)
                self.assertIn("untrusted proposal", instructions)
                self.assertIn("reviewed change_scope paths", instructions)
                for ollama in (False, True):
                    schema_id, schema = _schema(stage, {}, ollama=ollama)
                    self.assertEqual(schema_id, "openrtl.design." + stage +
                                     (".ollama.v1" if ollama else ".v1"))
                    self.assertEqual((schema_id, schema), _schema(
                        stage, {"document_correction": {"candidate": document_outputs()[0]}}, ollama=ollama))
                    self.assertEqual(set(schema["properties"]), {"summary", "files", "manifest"})
                    self.assertEqual(schema["properties"]["manifest"], {"type": "null"})
                    files = schema["properties"]["files"]
                    self.assertEqual(files["type"], "array")
                    self.assertNotEqual(files.get("maxItems"), 1)
                    self.assertEqual(set(files["items"]["properties"]), {"path", "content"})
                    self.assertEqual(set(files["items"]["required"]), {"path", "content"})
                    self.assertFalse(files["items"]["additionalProperties"])

    def test_native_factory_requires_pinned_sdk_and_compatible_model_before_client_creation(self) -> None:
        with patch("agentrig.integrations.ollama.sdk.OllamaSdkClientFactory") as factory:
            with patch("importlib.metadata.version", return_value="0.0.0"):
                with self.assertRaisesRegex(ValueError, "pinned_optional_ollama_sdk_required"):
                    ollama_design_expert(authorized=True, model=MODEL)
            with patch("importlib.metadata.version") as version:
                with self.assertRaisesRegex(ValueError, "provider_model_incompatible"):
                    ollama_design_expert(authorized=True, model="https://untrusted.invalid/model")
                version.assert_not_called()
            factory.assert_not_called()
        raw, _ = self.native_ollama([])
        self.assertEqual(raw.calls, [])
        self.assertEqual(raw.closed, 0)


if __name__ == "__main__":
    unittest.main()
