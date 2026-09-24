"""AgentRig adapter contracts with scripted responses only; no provider requests."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest

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

    async def execute(self, request: AgentExecutionRequest, context: object) -> AgentExecutionResult:
        self.requests.append(request)
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
        reply = asyncio.run(adapter.generate("discovery", {"schema": "unit_context"}, "b" * 32))
        self.assertEqual(reply.provider, "ollama")
        self.assertEqual(reply.model, "qwen3:8b")
        self.assertEqual((reply.input_tokens, reply.output_tokens), (21, 43))
        request = runtime.requests[0]
        self.assertEqual(request.contract.allowed_tools, ())
        self.assertEqual(request.contract.permissions["workspace"], "denied")
        self.assertEqual(request.contract.permissions["network"], "allowed")
        self.assertEqual(request.contract.output_schema, "openrtl.design.discovery.ollama.v1")
        self.assertEqual(request.contract.prompt_version, "openrtl.design.instructions.v6")
        self.assertEqual(request.contract.limits.max_tool_calls, 0)
        self.assertIn("Never ask for permission to proceed", request.instructions)
        self.assertIn("at most conversation_policy.max_questions_this_round", request.instructions)
        self.assertIn("same stable ID for each open question", request.instructions)
        self.assertIn("port width must be a JSON integer from 1 through 65536", request.instructions)
        self.assertIn("Never use zero", request.instructions)
        self.assertIn("copy that exact complete list", request.instructions)
        self.assertIn("including every clock, reset", request.instructions)

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

    def test_schemas_are_closed_and_role_specific(self) -> None:
        for stage in ("discovery", "architecture", "dv", "signoff", "explain"):
            schema = response_schema(stage)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(response_schema("rtl")["properties"]["manifest"], {"type": "null"})
        self.assertEqual(response_schema("dv")["properties"]["manifest"]["type"], "object")
        self.assertEqual(response_schema("discovery")["properties"]["specification"]["anyOf"][1],
                         {"type": "null"})
        self.assertEqual(response_schema("discovery")["properties"]["questions_asked"]["type"],
                         "array")
        self.assertEqual(response_schema("change_planning")["properties"]["specification"]["type"],
                         "object")

    def test_discovery_schema_expresses_local_bounds_before_provider_generation(self) -> None:
        schema = response_schema("discovery")["properties"]
        spec = schema["specification"]["anyOf"][0]["properties"]
        self.assertEqual(schema["questions_asked"]["maxItems"], 3)
        self.assertEqual(schema["engineering_memory"]["maxItems"], 64)
        self.assertEqual(spec["questions"]["maxItems"], 3)
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
