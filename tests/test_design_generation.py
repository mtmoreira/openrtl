"""AgentRig adapter contracts with scripted responses only; no provider requests."""

from __future__ import annotations

import asyncio
import unittest

from agentrig.agents import AgentExecutionRequest, AgentExecutionResult, AgentRuntimeUsage
from agentrig.capabilities import (
    CapabilityDescriptor, CapabilityFeature, CapabilityKind, CapabilityLimit, DataRetention,
    GenerationUsage, ModelMetadata, TextGenerationFinishReason,
)
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters.design_generation import (
    AgentRigDesignExpert, OllamaDesignExpert, ollama_design_expert, openai_design_expert,
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
        self.assertEqual(request.contract.output_schema, "openrtl.design.discovery.v4")

    def test_schemas_are_closed_and_role_specific(self) -> None:
        for stage in ("discovery", "architecture", "dv", "signoff", "explain"):
            schema = response_schema(stage)
            self.assertFalse(schema["additionalProperties"])
            self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertEqual(response_schema("rtl")["properties"]["manifest"], {"type": "null"})
        self.assertEqual(response_schema("dv")["properties"]["manifest"]["type"], "object")
        self.assertEqual(response_schema("discovery")["properties"]["specification"]["anyOf"][1],
                         {"type": "null"})
        self.assertEqual(response_schema("change_planning")["properties"]["specification"]["type"],
                         "object")


if __name__ == "__main__":
    unittest.main()
