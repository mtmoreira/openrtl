"""AgentRig adapter contracts with scripted responses only; no provider requests."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.agents import AgentExecutionRequest, AgentExecutionResult, AgentRuntimeUsage
from agentrig.capabilities import (
    CapabilityDescriptor, CapabilityFeature, CapabilityKind, CapabilityLimit, DataRetention,
    GenerationUsage, ModelMetadata, TextGenerationFinishReason,
)
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters.design_generation import (
    AgentRigDesignExpert, OllamaDesignExpert, _schema, ollama_design_expert, openai_design_expert,
    response_schema,
)
from openrtl.domain.design_session import JsonObject
from tests.test_design_agent import specification


def generator(*, tools: bool = False, model: str = "test-model",
              finish: TextGenerationFinishReason = TextGenerationFinishReason.COMPLETED) -> ScriptedStructuredGenerator[JsonObject]:
    features = {CapabilityFeature.STRUCTURED_OUTPUT}
    if tools:
        features.add(CapabilityFeature.TOOL_USE)
    return ScriptedStructuredGenerator(
        descriptor=CapabilityDescriptor(capability_id="openai.responses.structured_generation", version="test",
            kind=CapabilityKind.STRUCTURED_GENERATION, features=frozenset(features),
            limits={CapabilityLimit.MAX_OUTPUT_TOKENS: 32768}, data_retention=DataRetention.PROVIDER_MANAGED),
        outcomes=(ScriptedStructuredGeneration(encoded_output=specification(),
            usage=GenerationUsage(input_tokens=12, output_tokens=34),
            model=ModelMetadata(provider="openai", model_id=model), finish_reason=finish),),
    )


class ScriptedOllamaRuntime:
    def __init__(self) -> None:
        self.requests: list[AgentExecutionRequest] = []
        self.contexts: list[object] = []

    async def execute(self, request: AgentExecutionRequest, context: object) -> AgentExecutionResult:
        self.requests.append(request)
        self.contexts.append(context)
        return AgentExecutionResult.succeeded(
            specification(), usage=AgentRuntimeUsage(input_tokens=21, output_tokens=43),
            provider_metadata={"provider": "ollama", "model": "qwen3:8b", "finish_reason": "stop"})


class DesignGenerationTest(unittest.TestCase):
    def test_opted_in_runtime_capture_survives_rejected_output_and_preserves_usage(self) -> None:
        from agentrig.core.errors import AgentRigError, Failure, FailureKind
        from openrtl.adapters.design_session_store import DesignSessionStore
        from openrtl.adapters.design_trace_store import DesignTraceStore

        class CapturingRuntime:
            async def execute(self, request: AgentExecutionRequest, context: object) -> AgentExecutionResult:
                capture = context.private_trace_capture
                if capture is not None:
                    capture.record(context, kind="provider.response",
                                   content={"text": "synthetic invalid JSON response"},
                                   metadata={"provider": "ollama", "model": "qwen3:8b"})
                return AgentExecutionResult.from_failure(
                    Failure(kind=FailureKind.UNEXPECTED, message="Response invalid", code="ollama.invalid_output"),
                    usage=AgentRuntimeUsage(input_tokens=21, output_tokens=43),
                    provider_metadata={"provider": "ollama", "model": "qwen3:8b", "elapsed_ms": "7"})

        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                trace = DesignTraceStore(store, enabled=True)
                adapter = OllamaDesignExpert(CapturingRuntime(), model="qwen3:8b")
                adapter.trace_store = trace
                with self.assertRaises(AgentRigError):
                    asyncio.run(adapter.generate("discovery", {}, "d" * 32))
                records = trace.records({"d" * 32})
                self.assertIn("synthetic invalid JSON response", str(records))
                metrics = next(row["payload"] for row in records if row["category"] == "provider_metrics")
                self.assertEqual((metrics["input_tokens"], metrics["output_tokens"]), (21, 43))
                self.assertNotIn("synthetic invalid", str(store.events()) + str(store.read()))
                trace.set_enabled(False)
                with self.assertRaises(AgentRigError):
                    asyncio.run(adapter.generate("discovery", {}, "e" * 32))
                self.assertEqual(trace.records({"e" * 32}), [])
            finally:
                store.close()

    def test_normalized_runtime_timeout_and_cancellation_are_not_lost(self) -> None:
        from agentrig.core.errors import AgentRigError, Failure, FailureKind
        from openrtl.application.provider_failures import classify_provider_failure

        class StoppedRuntime:
            async def execute(self, request: AgentExecutionRequest, context: object) -> AgentExecutionResult:
                return AgentExecutionResult.from_failure(Failure(kind=kind, message="safe scripted failure"))

        for kind, expected in ((FailureKind.DEADLINE_EXCEEDED, "provider_timeout"),
                               (FailureKind.CANCELLED, "provider_cancelled")):
            with self.subTest(kind=kind):
                adapter = OllamaDesignExpert(StoppedRuntime(), model="qwen3:8b", timeout_seconds=300)
                with self.assertRaises(AgentRigError) as caught:
                    asyncio.run(adapter.generate("discovery", {}, "c" * 32))
                self.assertEqual(classify_provider_failure(caught.exception), expected)

    def test_one_tool_free_structured_turn_returns_usage_and_untrusted_output(self) -> None:
        adapter = AgentRigDesignExpert(generator(), model="test-model")
        self.assertEqual(adapter.discovery_contract_version, "v9")
        self.assertEqual(adapter.specification_contract_version, "v1")
        reply = asyncio.run(adapter.generate("discovery", {"schema": "unit_context"}, "a" * 32))
        self.assertEqual(reply.output, specification())
        self.assertEqual(reply.input_tokens, 12)
        self.assertEqual(reply.output_tokens, 34)

    def test_capability_and_returned_model_drift_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "capability_mismatch"):
            AgentRigDesignExpert(generator(tools=True), model="test-model")
        adapter = AgentRigDesignExpert(generator(model="different-model"), model="test-model")
        with self.assertRaisesRegex(ValueError, "identity_or_finish"):
            asyncio.run(adapter.generate("discovery", {}, "a" * 32))

    def test_explicit_provider_authorization_precedes_optional_runtime_resolution(self) -> None:
        with self.assertRaisesRegex(ValueError, "authorization_required"):
            openai_design_expert(authorized=False, model="test-model", credential_environment="OPENAI_API_KEY")
        with self.assertRaisesRegex(ValueError, "authorization_required"):
            ollama_design_expert(authorized=False, model="qwen3:8b")

    def test_native_ollama_turn_uses_a_tool_free_schema_contract(self) -> None:
        runtime = ScriptedOllamaRuntime()
        adapter = OllamaDesignExpert(runtime, model="qwen3:8b")
        self.assertEqual(adapter.discovery_contract_version, "v9")
        self.assertEqual(adapter.specification_contract_version, "v1")
        reply = asyncio.run(adapter.generate("discovery", {"schema": "unit_context"}, "b" * 32))
        self.assertEqual(reply.provider, "ollama")
        self.assertEqual(reply.model, "qwen3:8b")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (21, 43))
        request = runtime.requests[0]
        self.assertEqual(request.contract.allowed_tools, ())
        self.assertEqual(request.contract.permissions["workspace"], "denied")
        self.assertEqual(request.contract.permissions["network"], "allowed")
        self.assertEqual(request.contract.output_schema, "openrtl.design.discovery.ollama.v4")
        self.assertEqual(request.contract.prompt_version, "openrtl.design.instructions.v9")
        self.assertEqual(request.contract.limits.max_tool_calls, 0)
        self.assertIn("Never ask for permission to proceed", request.instructions)
        self.assertIn("at most conversation_policy.max_questions_this_round", request.instructions)
        self.assertIn("same stable ID for each open question", request.instructions)
        self.assertIn("port width must be a JSON integer from 1 through 65536", request.instructions)
        self.assertIn("Never use zero", request.instructions)
        self.assertIn("copy that exact complete list", request.instructions)
        self.assertIn("including every clock, reset", request.instructions)
        self.assertIn("openrtl.hardware-specification.v1", request.instructions)
        self.assertIn("without creating duplicate inventories or links", request.instructions)
        self.assertIn("Set reply exactly to 'structured'", request.instructions)

    def test_every_specification_prompt_carries_the_local_port_width_contract(self) -> None:
        for stage in ("discovery", "change_planning"):
            with self.subTest(stage=stage):
                runtime = ScriptedOllamaRuntime()
                adapter = OllamaDesignExpert(runtime, model="qwen3:8b")
                asyncio.run(adapter.generate(stage, {}, "b" * 32))
                instructions = runtime.requests[0].instructions
                self.assertIn("port width must be a JSON integer from 1 through 65536", instructions)
                self.assertIn("Never use zero", instructions)
                self.assertIn("copy that exact complete list", instructions)
                self.assertIn("rather than only multi-bit data ports", instructions)
                self.assertIn("openrtl.hardware-specification.v1", instructions)

    def test_schemas_are_closed_and_role_specific(self) -> None:
        for stage in ("discovery", "architecture", "dv", "signoff", "explain"):
            schema = response_schema(stage)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(response_schema("rtl")["properties"]["manifest"], {"type": "null"})
        self.assertEqual(response_schema("dv")["properties"]["manifest"]["type"], "object")
        self.assertEqual(response_schema("discovery")["properties"]["specification"]["anyOf"][1],
                         {"type": "null"})
        self.assertEqual(response_schema("discovery")["properties"]["reply"],
                         {"type": "string", "enum": ["structured"]})
        self.assertEqual(response_schema("discovery")["properties"]["questions_asked"]["type"],
                         "array")
        self.assertEqual(response_schema("change_planning")["properties"]["specification"]["type"],
                         "object")

    def test_discovery_contract_exposes_question_plans_and_explicit_resolutions(self) -> None:
        for ollama in (False, True):
            with self.subTest(ollama=ollama):
                schema_id, schema = _schema("discovery", {}, ollama=ollama)
                self.assertEqual(schema_id, "openrtl.design.discovery" +
                                 (".ollama.v4" if ollama else ".v9"))
                self.assertEqual(schema["properties"]["reply"]["enum"], ["structured"])
                self.assertIn("question_plan", schema["required"])
                self.assertIn("resolved_questions", schema["required"])
                plan = schema["properties"]["question_plan"]["items"]
                self.assertEqual(plan["required"], ["id", "topic", "reason"])
                self.assertFalse(plan["additionalProperties"])
                self.assertEqual(plan["properties"]["topic"]["enum"],
                                 ["interface", "clock_reset", "behavior", "acceptance", "configuration"])
                resolution = schema["properties"]["resolved_questions"]["items"]
                self.assertEqual(resolution["required"], ["id", "resolution_id"])
                self.assertFalse(resolution["additionalProperties"])
        properties = response_schema("discovery")["properties"]
        self.assertEqual(properties["question_plan"]["maxItems"], 3)
        self.assertEqual(properties["resolved_questions"]["maxItems"], 32)

    def test_discovery_prompt_uses_persistent_decisions_and_a_consolidated_question_plan(self) -> None:
        runtime = ScriptedOllamaRuntime()
        adapter = OllamaDesignExpert(runtime, model="qwen3:8b")
        asyncio.run(adapter.generate("discovery", {}, "b" * 32))
        instructions = runtime.requests[0].instructions
        for rule in ("Omitted saved facts are retained", "omission does not resolve a question",
                     "Preserve pending specification.questions without asking them again",
                     "Never repeat any existing open or resolved question",
                     "Read question_history", "Never reopen a settled topic as a new question ID",
                     "resolution_id naming a non-question engineering_memory entry",
                     "Its IDs must exactly equal questions_asked in the same order",
                     "After three clarification rounds", "configuration questions are not permitted",
                     "All user-facing questions are rendered by the application",
                     "Keep reply explanatory", "complete updated specification, never null",
                     "ephemeral rejected, untrusted output", "exact deterministic validation_code",
                     "Do not invent choices, grant approvals, weaken validation"):
            with self.subTest(rule=rule):
                self.assertIn(rule, instructions)

    def test_shared_deadline_caps_each_provider_attempt_without_serializing_local_timing(self) -> None:
        for ollama in (False, True):
            for now, expected in ((100.0, 12.0), (117.5, 2.5)):
                with self.subTest(ollama=ollama, now=now):
                    runtime = ScriptedOllamaRuntime() if ollama else generator()
                    adapter = (OllamaDesignExpert(runtime, model="qwen3:8b", timeout_seconds=12)
                               if ollama else AgentRigDesignExpert(runtime, model="test-model",
                                                                    timeout_seconds=12))
                    context = {"discovery_deadline": 120.0,
                               "discovery_correction": {"attempt": 1,
                                   "validation_code": "expert_clarification_repeated",
                                   "candidate": {"reply": "synthetic rejected candidate"}}}
                    original = copy.deepcopy(context)
                    with patch("openrtl.adapters.design_generation.time.monotonic", return_value=now):
                        asyncio.run(adapter.generate("discovery", context, "a" * 32))
                    child = runtime.contexts[0] if ollama else runtime.calls[0].context
                    self.assertEqual(child.deadline.monotonic_deadline, now + expected)
                    payload = (runtime.requests[0].input if ollama else
                               json.loads(runtime.calls[0].request.input.prompt))
                    self.assertNotIn("discovery_deadline", payload["context"])
                    self.assertEqual(payload["context"]["discovery_correction"],
                                     context["discovery_correction"])
                    self.assertEqual(context, original)

    def test_exhausted_shared_deadline_fails_normalized_before_any_provider_dispatch(self) -> None:
        from agentrig.core.errors import AgentRigError
        from openrtl.application.provider_failures import classify_provider_failure

        for ollama in (False, True):
            with self.subTest(ollama=ollama):
                runtime = ScriptedOllamaRuntime() if ollama else generator()
                adapter = (OllamaDesignExpert(runtime, model="qwen3:8b") if ollama else
                           AgentRigDesignExpert(runtime, model="test-model"))
                with patch("openrtl.adapters.design_generation.time.monotonic", return_value=120.0):
                    with self.assertRaises(AgentRigError) as caught:
                        asyncio.run(adapter.generate("discovery", {"discovery_deadline": 120.0}, "a" * 32))
                self.assertEqual(classify_provider_failure(caught.exception), "provider_timeout")
                self.assertEqual(len(runtime.requests if ollama else runtime.calls), 0)

    def test_correction_context_is_only_retained_by_opted_in_private_capture(self) -> None:
        from openrtl.adapters.design_session_store import DesignSessionStore
        from openrtl.adapters.design_trace_store import DesignTraceStore

        class CapturingRuntime(ScriptedOllamaRuntime):
            async def execute(self, request: AgentExecutionRequest, context: object) -> AgentExecutionResult:
                if context.private_trace_capture is not None:
                    context.private_trace_capture.record(context, kind="provider.request",
                        content=request.input, metadata={"provider": "ollama", "model": "qwen3:8b"})
                return await super().execute(request, context)

        marker = "synthetic rejected candidate for private correction"
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                trace = DesignTraceStore(store, enabled=True)
                runtime = CapturingRuntime()
                adapter = OllamaDesignExpert(runtime, model="qwen3:8b")
                adapter.trace_store = trace
                context = {"discovery_deadline": 120.0,
                           "discovery_correction": {"attempt": 1,
                               "validation_code": "expert_clarification_repeated",
                               "candidate": {"reply": marker}}}
                for enabled, operation_id in ((True, "d" * 32), (False, "e" * 32)):
                    trace.set_enabled(enabled)
                    with patch("openrtl.adapters.design_generation.time.monotonic", return_value=100.0):
                        asyncio.run(adapter.generate("discovery", context, operation_id))
                    records = str(trace.records({operation_id}))
                    self.assertEqual(marker in records, enabled)
                    self.assertNotIn("discovery_deadline", records)
                    self.assertNotIn(marker, str(store.events()) + str(store.read()))
            finally:
                store.close()

    def test_discovery_schema_expresses_local_bounds_before_provider_generation(self) -> None:
        schema = response_schema("discovery")["properties"]
        spec = schema["specification"]["anyOf"][0]["properties"]
        self.assertEqual(schema["questions_asked"]["maxItems"], 3)
        self.assertEqual(schema["engineering_memory"]["maxItems"], 64)
        self.assertEqual(spec["questions"]["maxItems"], 32)
        self.assertEqual(spec["readiness"]["properties"]["items"]["minItems"], 7)
        self.assertEqual(spec["readiness"]["properties"]["items"]["maxItems"], 7)
        self.assertEqual(spec["ports"]["items"]["properties"]["width"]["minimum"], 1)
        self.assertEqual(spec["requirements"]["items"]["properties"]["id"]["pattern"],
                         "^[A-Za-z][A-Za-z0-9_.-]*$")
        self.assertEqual(response_schema("change_planning")["properties"]["specification"]
                         ["properties"]["questions"]["maxItems"], 32)

    def test_ollama_format_schema_omits_value_constraints_but_retains_structure(self) -> None:
        unsupported = {"pattern", "minLength", "maxLength", "minItems", "maxItems",
                       "minimum", "maximum"}

        def check(value: object) -> None:
            if isinstance(value, dict):
                self.assertFalse(unsupported.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for stage in ("discovery", "change_planning"):
            with self.subTest(stage=stage):
                _, schema = _schema(stage, {}, ollama=True)
                check(schema)
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
        _, rich = _schema("discovery", {})
        self.assertEqual(rich["properties"]["questions_asked"]["maxItems"], 3)
        self.assertNotEqual(_schema("change_planning", {}, ollama=True)[0],
                            _schema("change_planning", {"specification": {"readiness": {}}},
                                    ollama=True)[0])


if __name__ == "__main__":
    unittest.main()
