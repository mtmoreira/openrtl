"""Reference-model wire contract, using synthetic responses and no provider calls."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agentrig.capabilities import GenerationUsage, ModelMetadata, TextGenerationFinishReason
from agentrig.integrations.ollama import OLLAMA_CLIENT_VERSION
from agentrig.integrations.ollama.sdk import OllamaSdkClientFactory
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator
from openrtl.adapters.design_generation import (
    AgentRigDesignExpert, _reference_model_output, _schema, ollama_design_expert,
)
from openrtl.adapters.design_reference_transport import decode_reference_model_output
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.adapters.design_trace_store import DesignTraceStore
from openrtl.domain.design_session import JsonObject, STAGES, validate_files
from tests.test_design_generation import generator
from tests.test_design_transport_runtime import MODEL, ScriptedRawSdk


def provider_output() -> JsonObject:
    return {"summary": "Synthetic independent model", "manifest": None, "files": [
        {"relative_path": "__init__.py", "content": "# Model package.\n"},
        {"relative_path": "sync_fifo.py", "content": "# Synthetic model — never executed.\n"},
        {"relative_path": "test_model.py", "content": "# Synthetic unittest entry point.\n"},
        {"relative_path": "checks/__init__.py", "content": "# Test package.\n"},
        {"relative_path": "checks/test_sync_fifo.py", "content":
            "from model.sync_fifo import SyncFifo\n# Synthetic import; never executed.\n"},
    ]}


class ReferenceModelTransportTest(unittest.TestCase):
    def test_schema_owns_the_root_and_is_static_for_reviewed_changes(self) -> None:
        contexts = ({}, {"change_scope": None}, {"change_scope": {
            "stage_paths": {stage: ["model/sync_fifo.py"] if stage == "reference_model" else []
                            for stage in STAGES}}})
        for ollama in (False, True):
            identifier, expected = _schema("reference_model", {}, ollama=ollama)
            self.assertEqual(identifier, "openrtl.design.reference_model" +
                             (".ollama.v2" if ollama else ".v2"))
            for context in contexts:
                self.assertEqual(_schema("reference_model", context, ollama=ollama),
                                 (identifier, expected))
            files = expected["properties"]["files"]
            row = files["items"]
            self.assertEqual(row["required"], ["relative_path", "content"])
            self.assertEqual(set(row["properties"]), {"relative_path", "content"})
            self.assertFalse(row["additionalProperties"])
            self.assertEqual(expected["properties"]["manifest"], {"type": "null"})
            self.assertEqual(expected["required"], ["summary", "files", "manifest"])
            if ollama:
                self.assertNotIn("minItems", files)
                self.assertNotIn("maxItems", files)
            else:
                self.assertEqual((files["minItems"], files["maxItems"]), (1, 64))
        for stage in ("architecture", "verification_plan", "rtl", "assertions", "dv", "diagnosis"):
            self.assertEqual(_schema(stage, {})[1]["properties"]["files"]["items"]["required"],
                             ["path", "content"])

    def test_multifile_nested_content_and_order_are_preserved(self) -> None:
        raw = provider_output()
        original = copy.deepcopy(raw)
        converted = decode_reference_model_output(raw)
        self.assertEqual(converted["files"], [
            {"path": "model/" + row["relative_path"], "content": row["content"]}
            for row in raw["files"]])
        self.assertEqual(validate_files(converted["files"], "reference_model"),
                         converted["files"])
        self.assertEqual(converted["summary"], raw["summary"])
        self.assertIsNone(converted["manifest"])
        self.assertEqual(raw, original)
        self.assertIn("model/test_model.py", [row["path"] for row in converted["files"]])

    def test_relative_paths_never_escape_or_normalize(self) -> None:
        for value in ("../rtl/core.py", "/tmp/model.py", "a//b.py", "./core.py", "a/../core.py",
                      "a/.hidden.py", "a\\b.py", "a.py\n", "a.txt", "", 1, None, True):
            with self.subTest(value=value):
                raw = provider_output()
                raw["files"][0]["relative_path"] = value
                before = copy.deepcopy(raw)
                with self.assertRaises(ValueError):
                    decode_reference_model_output(raw)
                self.assertEqual(_reference_model_output(raw), before)
                with self.assertRaises(ValueError):
                    validate_files(_reference_model_output(raw)["files"], "reference_model")

    def test_counts_duplicates_and_content_remain_domain_validated(self) -> None:
        for files, expected in (([], "contribution_files_missing"),
                               ([{"relative_path": "x.py", "content": "# Synthetic\n"}] * 2,
                                "contribution_ownership_invalid"),
                               ([{"relative_path": "x.py", "content": 3}], "text_type_invalid")):
            with self.subTest(expected=expected):
                converted = _reference_model_output({"summary": "Synthetic", "files": files,
                                                     "manifest": None})
                with self.assertRaisesRegex(ValueError, "^" + expected + "$"):
                    validate_files(converted["files"], "reference_model")
        raw = provider_output()
        raw["files"] = [{"relative_path": f"m{i}.py", "content": "# Synthetic\n"}
                        for i in range(65)]
        self.assertEqual(_reference_model_output(raw), raw)
        with self.assertRaisesRegex(ValueError, "^list_invalid$"):
            validate_files(raw["files"], "reference_model")
        raw["files"].pop()
        self.assertEqual(len(validate_files(_reference_model_output(raw)["files"], "reference_model")), 64)

    def test_legacy_captured_path_shape_is_not_relocated_or_rewritten(self) -> None:
        raw = {"summary": "Synthetic captured shape", "manifest": None,
               "files": [{"path": name, "content": "# Synthetic; never executed.\n"}
                         for name in ("model/__init__.py", "model/sync_fifo.py",
                                      "test_model/__init__.py", "test_model/test_sync_fifo.py")]}
        converted = _reference_model_output(raw)
        self.assertEqual(converted, raw)
        with self.assertRaisesRegex(ValueError, "^source_path_invalid$"):
            validate_files(converted["files"], "reference_model")

    def test_openai_transport_preserves_known_usage_on_malformed_output(self) -> None:
        good = provider_output()
        malformed = copy.deepcopy(good)
        malformed["files"][0]["extra"] = "Synthetic unsupported field"
        for raw in (good, malformed):
            runtime = ScriptedStructuredGenerator(descriptor=generator().descriptor, outcomes=(
                ScriptedStructuredGeneration(encoded_output=raw,
                    usage=GenerationUsage(input_tokens=12, output_tokens=34),
                    model=ModelMetadata(provider="openai", model_id="test-model"),
                    finish_reason=TextGenerationFinishReason.COMPLETED),))
            expert = AgentRigDesignExpert(runtime, model="test-model")
            reply = asyncio.run(expert.generate("reference_model", {}, "a" * 32))
            self.assertEqual((reply.input_tokens, reply.output_tokens), (12, 34))
            self.assertEqual(reply.output, decode_reference_model_output(good) if raw == good else raw)
            request = runtime.calls[0].request
            self.assertEqual(request.output_schema.schema_id, "openrtl.design.reference_model.v2")
            self.assertIn("relative_path", request.output_schema.json_schema["properties"]["files"]
                          ["items"]["properties"])

    def test_factory_registered_schema_reaches_native_ollama_and_private_capture(self) -> None:
        good = provider_output()
        malformed = copy.deepcopy(good)
        malformed["files"][0]["relative_path"] = "../rtl/escape.py"
        raw = ScriptedRawSdk([good, good, malformed])
        native_factory = OllamaSdkClientFactory

        def factory(*, host: str) -> OllamaSdkClientFactory:
            self.assertEqual(host, "http://127.0.0.1:11434")
            return native_factory(host=host, raw_client_builder=lambda _host, _headers: raw)

        with patch("importlib.metadata.version", return_value=OLLAMA_CLIENT_VERSION), \
             patch("agentrig.integrations.ollama.sdk.OllamaSdkClientFactory", side_effect=factory):
            expert = ollama_design_expert(authorized=True, model=MODEL)
        with tempfile.TemporaryDirectory() as temporary:
            store = DesignSessionStore(Path(temporary).resolve() / "project", create=True)
            try:
                trace = DesignTraceStore(store, enabled=True)
                expert.trace_store = trace
                reviewed = {"stage_paths": {stage: [row["path"] for row in
                    decode_reference_model_output(good)["files"]] if stage == "reference_model"
                    else [] for stage in STAGES}}
                contexts = ({"change_scope": None}, {"change_scope": reviewed}, {})
                for index, context in enumerate(contexts):
                    before = copy.deepcopy(context)
                    reply = asyncio.run(expert.generate("reference_model", context, f"{index:032x}"))
                    self.assertEqual(context, before)
                    self.assertEqual((reply.input_tokens, reply.output_tokens), (101, 203))
                    self.assertEqual(reply.output, malformed if index == 2 else
                                     decode_reference_model_output(good))
                    self.assertEqual(raw.calls[index]["format"],
                                     _schema("reference_model", context, ollama=True)[1])
                    self.assertNotIn("tools", raw.calls[index])
                    payload = json.loads(raw.calls[index]["messages"][1]["content"])
                    self.assertEqual(payload["context"], before)
                    self.assertIn("files[].relative_path", raw.calls[index]["messages"][0]["content"])
                records = trace.records({f"{index:032x}" for index in range(3)})
                responses = [json.loads(row["payload"]["content"]) for row in records
                             if row["category"] == "provider_trace" and
                             row["payload"]["kind"] == "provider.response"]
                self.assertEqual([json.loads(response["content"]) for response in responses],
                                 [good, good, malformed])
                self.assertEqual(len(raw.calls), 3)
                self.assertEqual(raw.closed, 3)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
