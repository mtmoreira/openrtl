"""Synthetic DV outputs through production provider transport and orchestration.

The native Ollama factory uses a raw SDK double, and OpenAI uses AgentRig's
scripted generator. No provider, simulator, socket, or credential is used.
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
from openrtl.adapters.design_generation import AgentRigDesignExpert, ollama_design_expert
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.domain.design_session import JsonObject, STAGES, content_digest
from openrtl.domain.provider_controls import estimated_cost_nano
from tests.test_design_agent import FakeExpert, contribution, specification
from tests.test_design_transport_runtime import MODEL, THINKING_MARKER, ScriptedRawSdk


OPENAI_MODEL = "gpt-5.6-terra"


def dv_output(*, legacy: bool = False) -> JsonObject:
    output = contribution("dv")
    output["files"][0]["path"] = "dv/test_sync_fifo.py"
    output["manifest"].pop("test_modules")
    output["manifest"]["test_modules" if legacy else "test_file_paths"] = ["dv/test_sync_fifo.py"]
    return output


class DvManifestRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = DesignSessionStore(Path(self.temporary.name).resolve() / "project", create=True)
        self.agent = DesignAgent(self.store, FakeExpert())
        spec = specification()
        spec["assumptions"] = [{"id": "wire.default", "text": "Four bits suffice for the fixture.",
                                "rationale": "Synthetic bounded fixture."}]
        self.agent.propose(spec)
        self.agent.approve(content_digest(spec))
        for _ in STAGES[:-1]:
            asyncio.run(self.agent.advance())
        self.before = self.store.read()
        self.assertEqual(self.before["stage"], 5)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def native_ollama(self, outputs: list[JsonObject], *, capture: bool = True,
                      corrections: int = 2) -> tuple[ScriptedRawSdk, DesignTraceStore]:
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
                                 policy=DesignPolicy(max_dv_corrections=corrections))
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
                encoded_output=output, usage=GenerationUsage(input_tokens=101, output_tokens=203),
                model=ModelMetadata(provider="openai", model_id=OPENAI_MODEL),
                finish_reason=TextGenerationFinishReason.COMPLETED)
                for output in outputs),
        )
        expert = AgentRigDesignExpert(generator, model=OPENAI_MODEL)
        self.agent = DesignAgent(self.store, expert,
                                 policy=DesignPolicy(provider_model=OPENAI_MODEL, max_dv_corrections=1))
        self.agent.configure_provider(OPENAI_MODEL, 5_000_000_000)
        return generator

    def new_events(self) -> list[JsonObject]:
        return [row for row in self.store.events() if row["sequence"] > self.before["revision"]]

    def operation_ids(self) -> set[str]:
        return {row["fields"]["operation_id"] for row in self.new_events()
                if row["event"] == "operation.started"}

    def assert_saved_once(self, state: JsonObject, attempts: int) -> None:
        self.assertEqual(state["stage"], 6)
        self.assertEqual(state["calls"] - self.before["calls"], attempts)
        self.assertIsNone(state["active"])
        self.assertIsNone(state["last_error"])
        self.assertEqual(state["manifest"]["test_modules"], ["test_sync_fifo"])
        self.assertNotIn("test_file_paths", state["manifest"])
        completed = [row for row in self.new_events() if row["event"] == "operation.completed"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0]["fields"]["artifact_count"], 1)
        self.assertEqual(set(state["files"]) - set(self.before["files"]), {"dv/test_sync_fifo.py"})
        self.assertEqual({p: state["files"][p] for p in self.before["files"]}, self.before["files"])
        self.assertEqual(self.store.contents(state)["dv/test_sync_fifo.py"], dv_output()["files"][0]["content"])
        self.assertNotIn(THINKING_MARKER, str(state) + str(self.store.events()))
        self.assertNotIn("dv_correction", str(state) + str(self.store.events()))

    def assert_accounting(self, attempts: int) -> None:
        received = [row["fields"] for row in self.new_events() if row["event"] == "operation.received"]
        self.assertEqual(len(received), attempts)
        self.assertEqual(sum(row["input_tokens"] for row in received), 101 * attempts)
        self.assertEqual(sum(row["output_tokens"] for row in received), 203 * attempts)

    def test_captured_legacy_path_spelling_is_lossless_and_needs_no_correction(self) -> None:
        output = dv_output(legacy=True)
        raw, trace = self.native_ollama([output])

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 1)
        self.assert_accounting(1)
        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(raw.closed, 1)
        self.assertEqual(raw.outputs, [output])
        records = trace.records(self.operation_ids())
        response = json.loads(next(row["payload"]["content"] for row in records
                                   if row["category"] == "provider_trace" and
                                   row["payload"]["kind"] == "provider.response"))
        self.assertEqual(json.loads(response["content"]), output)
        self.assertEqual(response["thinking"], THINKING_MARKER)
        accepted = next(row["payload"]["output"] for row in records if row["category"] == "assistant_output")
        self.assertEqual(accepted["manifest"]["test_modules"], ["test_sync_fifo"])

    def test_registered_ollama_schema_exposes_explicit_paths_and_accepts_one_turn(self) -> None:
        raw, _ = self.native_ollama([dv_output()])

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 1)
        self.assert_accounting(1)
        schema = raw.calls[0]["format"]["properties"]["manifest"]
        self.assertIn("test_file_paths", schema["required"])
        self.assertNotIn("test_modules", schema["properties"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(raw.calls[0]["model"], MODEL)
        self.assertIs(raw.calls[0]["stream"], False)
        self.assertIs(raw.calls[0]["think"], False)
        self.assertNotIn("tools", raw.calls[0])

    def test_assumption_and_decision_links_are_corrected_after_usage_and_capture(self) -> None:
        rejected = dv_output(legacy=True)
        rejected["manifest"]["requirement_tests"].extend([
            {"requirement_id": "wire.default", "tests": ["transfer"]},
            {"requirement_id": "wire.decision", "tests": ["transfer"]},
        ])
        raw, trace = self.native_ollama([rejected, dv_output()])

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 2)
        self.assert_accounting(2)
        self.assertEqual(raw.closed, 2)
        failures = [row for row in self.new_events() if row["event"] == "operation.failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["fields"]["validation_code"], "requirement_test_links_incomplete")
        failed_state = self.store.historical_state(failures[0]["sequence"])
        self.assertEqual(failed_state["files"], self.before["files"])
        self.assertEqual(failed_state["stage"], 5)
        self.assertIsNone(failed_state["manifest"])
        corrected_context = json.loads(raw.calls[1]["messages"][1]["content"])["context"]
        correction = corrected_context["dv_correction"]
        self.assertEqual(correction["validation_code"], "requirement_test_links_incomplete")
        self.assertEqual(correction["validation_feedback"]["field"], "manifest.requirement_tests")
        self.assertTrue(correction["validation_feedback"]["expected"])
        self.assertEqual(correction["candidate"]["manifest"]["requirement_tests"],
                         rejected["manifest"]["requirement_tests"])
        self.assertNotIn("expert_deadline", corrected_context)
        self.assertNotIn("discovery_deadline", corrected_context)
        records = trace.records(self.operation_ids())
        responses = [json.loads(row["payload"]["content"]) for row in records
                     if row["category"] == "provider_trace" and row["payload"]["kind"] == "provider.response"]
        self.assertEqual([json.loads(row["content"]) for row in responses], [rejected, dv_output()])
        self.assertTrue(all(row["thinking"] == THINKING_MARKER for row in responses))
        self.assertEqual(state["manifest"]["requirement_tests"],
                         [{"requirement_id": "wire.transfer", "tests": ["transfer"]}])

    def test_malformed_advertised_path_is_captured_and_accounted_without_retry(self) -> None:
        rejected = dv_output()
        rejected["manifest"]["test_file_paths"] = ["dv/../test_sync_fifo.py"]
        raw, trace = self.native_ollama([rejected])

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())

        state = self.store.read()
        self.assertEqual(state["stage"], 5)
        self.assertEqual(state["calls"] - self.before["calls"], 1)
        self.assertEqual(state["files"], self.before["files"])
        self.assertIsNone(state["manifest"])
        self.assertIsNone(state["active"])
        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(raw.closed, 1)
        self.assert_accounting(1)
        records = trace.records(self.operation_ids())
        captured = next(row["payload"]["output"] for row in records if row["category"] == "assistant_output")
        self.assertEqual(captured, rejected)
        response = json.loads(next(row["payload"]["content"] for row in records
                                   if row["category"] == "provider_trace" and
                                   row["payload"]["kind"] == "provider.response"))
        self.assertEqual(json.loads(response["content"]), rejected)
        failure = next(row for row in self.new_events() if row["event"] == "operation.failed")
        self.assertEqual(failure["fields"]["validation_code"], "object_fields_invalid")
        self.assertFalse(any(row["event"] == "operation.completed" for row in self.new_events()))
        self.assertNotIn(THINKING_MARKER, str(state) + str(self.store.events()))

    def test_manifest_and_captured_python_syntax_shape_are_reported_together(self) -> None:
        rejected = dv_output(legacy=True)
        rejected["manifest"]["requirement_tests"].append(
            {"requirement_id": "wire.default", "tests": ["transfer"]})
        # Preserve the captured syntax shape and line number using synthetic
        # source only. A compound for cannot follow a semicolon assignment.
        source = ("async def transfer(dut):\n" + "    # Synthetic padding\n" * 198 +
                  "    dut.synthetic.value = 0; for _ in range(2): await synthetic_tick()\n")
        rejected["files"][0]["content"] = source
        only_manifest_fixed = copy.deepcopy(rejected)
        only_manifest_fixed["manifest"]["requirement_tests"] = dv_output()["manifest"]["requirement_tests"]
        outputs = [rejected, only_manifest_fixed, dv_output()]
        raw, trace = self.native_ollama(outputs)

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 3)
        self.assert_accounting(3)
        self.assertEqual(raw.closed, 3)
        self.assertEqual(raw.outputs, outputs)
        first_correction = json.loads(raw.calls[1]["messages"][1]["content"])["context"]["dv_correction"]
        self.assertEqual(first_correction["validation_code"], "requirement_test_links_incomplete")
        self.assertEqual(first_correction["validation_feedback"]["field"], "manifest.requirement_tests")
        self.assertEqual(len(first_correction["additional_feedback"]), 1)
        syntax = first_correction["additional_feedback"][0]
        self.assertEqual(syntax["field"], "files[0].content")
        self.assertEqual(syntax["line"], 200)
        self.assertGreater(syntax["column"], 0)
        self.assertTrue(syntax["expected"])
        self.assertNotIn("dut.synthetic", str(syntax))
        second_correction = json.loads(raw.calls[2]["messages"][1]["content"])["context"]["dv_correction"]
        self.assertEqual(second_correction["validation_code"], "python_syntax_invalid")
        self.assertEqual(second_correction["validation_feedback"], syntax)
        failures = [row for row in self.new_events() if row["event"] == "operation.failed"]
        self.assertEqual([row["fields"]["validation_code"] for row in failures],
                         ["requirement_test_links_incomplete", "python_syntax_invalid"])
        for failure in failures:
            retained = self.store.historical_state(failure["sequence"])
            self.assertEqual(retained["files"], self.before["files"])
            self.assertEqual(retained["stage"], 5)
            self.assertIsNone(retained["manifest"])
        responses = [json.loads(row["payload"]["content"]) for row in trace.records(self.operation_ids())
                     if row["category"] == "provider_trace" and row["payload"]["kind"] == "provider.response"]
        self.assertEqual([json.loads(row["content"]) for row in responses], outputs)
        self.assertNotIn("dut.synthetic", str(state) + str(self.store.events()))
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM blobs WHERE content = ?", (source.encode(),)
        ).fetchone()[0], 0)

    def test_bounded_compiler_recursion_is_accounted_closed_and_never_retried(self) -> None:
        rejected = dv_output()
        # Bounded source can exceed CPython's expression-compilation depth.
        # Keep the actual failure shape, rather than substituting a validator.
        source = "value = " + " + ".join(["x"] * 20000)
        self.assertLess(len(source.encode()), 256 * 1024)
        rejected["files"][0]["content"] = source
        raw, trace = self.native_ollama([rejected])

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())

        state = self.store.read()
        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(raw.closed, 1)
        self.assert_accounting(1)
        self.assertIsNone(state["active"])
        self.assertEqual(state["files"], self.before["files"])
        self.assertEqual(state["stage"], 5)
        self.assertIsNone(state["manifest"])
        self.assertEqual(state["last_error"], "expert_output_invalid")
        failures = [row for row in self.new_events() if row["event"] == "operation.failed"]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["fields"]["validation_code"], "python_compile_resource_limit")
        self.assertFalse(any(row["event"] == "operation.completed" for row in self.new_events()))
        records = trace.records(self.operation_ids())
        captured = next(row["payload"]["output"] for row in records if row["category"] == "assistant_output")
        self.assertEqual(captured["files"], rejected["files"])
        self.assertNotIn(source, str(state) + str(self.store.events()))
        self.assertNotIn(THINKING_MARKER, str(state) + str(self.store.events()))
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM blobs WHERE content = ?", (source.encode(),)
        ).fetchone()[0], 0)

    def test_openai_paths_correct_requirement_links_and_settle_every_attempt(self) -> None:
        rejected = dv_output()
        rejected["manifest"]["requirement_tests"].append(
            {"requirement_id": "wire.default", "tests": ["transfer"]})
        generator = self.scripted_openai([rejected, dv_output()])

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 2)
        self.assert_accounting(2)
        self.assertEqual(len(generator.calls), 2)
        cost = estimated_cost_nano(OPENAI_MODEL, 101, 203)
        self.assertEqual(state["provider"]["spent_nano_usd"], 2 * cost)
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])
        correction_context = json.loads(generator.calls[1].request.input.prompt)["context"]
        self.assertEqual(correction_context["dv_correction"]["validation_code"],
                         "requirement_test_links_incomplete")
        self.assertNotIn("expert_deadline", correction_context)
        failure = next(row for row in self.new_events() if row["event"] == "operation.failed")
        failed_state = self.store.historical_state(failure["sequence"])
        self.assertEqual(failed_state["provider"]["spent_nano_usd"], cost)
        self.assertFalse(failed_state["provider"]["uncertain"])
        self.assertEqual(failed_state["files"], self.before["files"])

    def test_openai_valid_paths_save_without_a_correction(self) -> None:
        generator = self.scripted_openai([dv_output()])

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 1)
        self.assert_accounting(1)
        self.assertEqual(len(generator.calls), 1)
        self.assertEqual(state["provider"]["spent_nano_usd"], estimated_cost_nano(OPENAI_MODEL, 101, 203))
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])

    def test_openai_malformed_path_settles_known_spend_without_retry(self) -> None:
        rejected = dv_output()
        rejected["manifest"]["test_file_paths"] = ["dv/../test_sync_fifo.py"]
        generator = self.scripted_openai([rejected])

        with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
            asyncio.run(self.agent.advance())

        state = self.store.read()
        self.assertEqual(len(generator.calls), 1)
        self.assert_accounting(1)
        self.assertEqual(state["provider"]["spent_nano_usd"], estimated_cost_nano(OPENAI_MODEL, 101, 203))
        self.assertFalse(state["provider"]["uncertain"])
        self.assertIsNone(state["provider"]["pending"])
        self.assertEqual(state["files"], self.before["files"])
        self.assertEqual(state["stage"], 5)
        self.assertIsNone(state["manifest"])
        self.assertIsNone(state["active"])

    def test_rejected_candidate_is_not_saved_when_capture_is_disabled(self) -> None:
        rejected = dv_output()
        marker = "synthetic private rejected DV source marker"
        rejected["files"][0]["content"] = "# " + marker + "\n"
        rejected["manifest"]["requirement_tests"].append(
            {"requirement_id": "wire.default", "tests": ["transfer"]})
        raw, trace = self.native_ollama([rejected, dv_output()], capture=False)

        state = asyncio.run(self.agent.advance())

        self.assert_saved_once(state, 2)
        self.assert_accounting(2)
        self.assertEqual(len(raw.calls), 2)
        self.assertEqual(trace.records(self.operation_ids()), [])
        persisted = " ".join(row[0] for table in ("snapshots", "events")
                             for row in self.store.connection.execute(f"SELECT payload FROM {table}"))
        for private in (marker, THINKING_MARKER):
            self.assertNotIn(private, persisted)
        self.assertEqual(self.store.connection.execute(
            "SELECT count(*) FROM blobs WHERE content = ?", (rejected["files"][0]["content"].encode(),)
        ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
