"""Lossless manifest spelling and provider boundaries; no provider calls."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import unittest

from agentrig.agents import AgentExecutionResult, AgentRuntimeUsage
from agentrig.capabilities import GenerationUsage, ModelMetadata, TextGenerationFinishReason
from agentrig.testing import ScriptedStructuredGeneration, ScriptedStructuredGenerator

from openrtl.adapters.design_generation import (
    AgentRigDesignExpert, OllamaDesignExpert, _provider_context, _schema,
)
from openrtl.adapters.design_manifest_transport import (
    TEST_FILE_PATH_EXPECTATION, decode_manifest_output, manifest_transport_context,
)
from openrtl.domain.design_session import validate_manifest
from openrtl.domain.design_coaching import validate_intent
from tests.test_design_agent import contribution, manifest, specification
from tests.test_design_generation import generator


def wire_contribution():
    output = contribution("dv")
    output["manifest"]["test_file_paths"] = ["dv/test_sync_fifo.py"]
    del output["manifest"]["test_modules"]
    output["files"] = [{"path": "dv/test_sync_fifo.py", "content": "# Synthetic test fixture.\n"}]
    return output


class ManifestTransportTest(unittest.TestCase):
    def test_captured_legacy_path_and_new_file_field_have_one_lossless_mapping(self) -> None:
        for field in ("test_modules", "test_file_paths"):
            output = wire_contribution()
            output["manifest"][field] = output["manifest"].pop("test_file_paths")
            original = copy.deepcopy(output)
            decoded = decode_manifest_output(output)
            self.assertEqual(output, original)
            self.assertEqual(decoded["manifest"]["test_modules"], ["test_sync_fifo"])
            self.assertNotIn("test_file_paths", decoded["manifest"])
            files = {path: "" for path in decoded["manifest"]["sources"]}
            files["dv/test_sync_fifo.py"] = ""
            self.assertEqual(validate_manifest(decoded["manifest"], files, specification()),
                             decoded["manifest"])
            if field == "test_modules":
                with self.assertRaisesRegex(ValueError, "^identifier_invalid$"):
                    validate_manifest(output["manifest"], files, specification())

    def test_legacy_canonical_names_and_order_are_unchanged(self) -> None:
        output = contribution("dv")
        output["manifest"]["test_modules"] = ["test_z", "test_a", "_test_2"]
        self.assertEqual(decode_manifest_output(output), output)
        encoded = manifest_transport_context({"manifest": output["manifest"]})
        self.assertEqual(encoded["manifest"]["test_file_paths"],
                         ["dv/test_z.py", "dv/test_a.py", "dv/_test_2.py"])
        self.assertEqual(decode_manifest_output(encoded)["manifest"], output["manifest"])

    def test_transport_cannot_supply_missing_files_or_change_optimization_scope(self) -> None:
        decoded = decode_manifest_output(wire_contribution())
        files = {path: "" for path in decoded["manifest"]["sources"]}
        with self.assertRaisesRegex(ValueError, "^test_module_unavailable$"):
            validate_manifest(decoded["manifest"], files, specification())
        state = {"spec": specification(), "manifest": manifest()}
        plan = {"specification": specification(), "stage_paths": {"rtl": ["rtl/wire_top.sv"]},
                "manifest": manifest()}
        transported = manifest_transport_context(plan)
        decoded = decode_manifest_output(transported)
        self.assertEqual(decoded, plan)
        validate_intent(decoded, "optimization", state)
        decoded["manifest"]["test_modules"] = ["test_other"]
        with self.assertRaisesRegex(ValueError, "^optimization_workload_must_be_retained$"):
            validate_intent(decoded, "optimization", state)

    def test_ambiguous_aliases_and_invalid_paths_are_preserved_for_rejection(self) -> None:
        invalid = ("test_sync_fifo", "dv/test_sync_fifo", "dv/test_sync_fifo.PY",
                   "/dv/test_sync_fifo.py", "dv/../test_sync_fifo.py", "dv/./test_sync_fifo.py",
                   "dv//test_sync_fifo.py", "dv/nested/test_sync_fifo.py", "dv.test_sync_fifo",
                   "model/test_sync_fifo.py", "dv/test-sync-fifo.py", "dv/1test.py",
                   "dv/é.py", "dv\\test_sync_fifo.py", " dv/test_sync_fifo.py",
                   "dv/test_sync_fifo.py\n", "dv/" + "a" * 129 + ".py", None, True, 32)
        for value in invalid:
            with self.subTest(value=value):
                output = wire_contribution()
                output["manifest"]["test_file_paths"].append(value)
                original = copy.deepcopy(output)
                self.assertEqual(decode_manifest_output(output), original)
                self.assertEqual(output, original)
        for bad_list in (None, "dv/test_sync_fifo.py", ["dv/test_sync_fifo.py"] * 65):
            output = wire_contribution()
            output["manifest"]["test_file_paths"] = bad_list
            self.assertEqual(decode_manifest_output(output), output)
        output = wire_contribution()
        output["manifest"]["test_modules"] = ["test_sync_fifo"]
        self.assertEqual(decode_manifest_output(output), output)
        self.assertEqual(manifest_transport_context({"manifest": output["manifest"]}),
                         {"manifest": output["manifest"]})

    def test_legacy_invalid_names_are_not_normalized_and_collisions_stay_duplicates(self) -> None:
        for value in ("test_sync_fifo.py", "dv/nested/test_sync_fifo.py", "test-sync-fifo", None):
            output = contribution("dv")
            output["manifest"]["test_modules"] = ["test_wire", value]
            self.assertEqual(decode_manifest_output(output), output)
            self.assertEqual(manifest_transport_context({"manifest": output["manifest"]}),
                             {"manifest": output["manifest"]})
        output = contribution("dv")
        output["manifest"]["test_modules"] = ["test_wire", "dv/test_wire.py"]
        decoded = decode_manifest_output(output)
        self.assertEqual(decoded["manifest"]["test_modules"], ["test_wire", "test_wire"])
        files = {path: "" for path in decoded["manifest"]["sources"]}
        files["dv/test_wire.py"] = ""
        with self.assertRaisesRegex(ValueError, "^test_modules_invalid$"):
            validate_manifest(decoded["manifest"], files, specification())

    def test_current_reviewed_and_correction_manifests_share_wire_vocabulary(self) -> None:
        context = {"specification": specification(), "manifest": manifest(),
                   "change_scope": {"manifest": manifest()},
                   "dv_correction": {"candidate": contribution("dv"),
                                     "validation_feedback": {"field": "manifest.test_modules[0]",
                                                             "expected": "canonical rule"}}}
        original = copy.deepcopy(context)
        encoded = manifest_transport_context(context)
        self.assertEqual(context, original)
        self.assertEqual(encoded["specification"], context["specification"])
        for selected in (encoded["manifest"], encoded["change_scope"]["manifest"],
                         encoded["dv_correction"]["candidate"]["manifest"]):
            self.assertEqual(selected["test_file_paths"], ["dv/test_wire.py"])
            self.assertNotIn("test_modules", selected)
        self.assertEqual(encoded["dv_correction"]["validation_feedback"], {
            "field": "manifest.test_file_paths[0]", "expected": TEST_FILE_PATH_EXPECTATION})
        for field in ("manifest.test_modules", "manifest.test_modules[63]"):
            context["dv_correction"]["validation_feedback"]["field"] = field
            self.assertEqual(manifest_transport_context(context)["dv_correction"]["validation_feedback"]["field"],
                             field.replace("test_modules", "test_file_paths"))
        for field in ("manifest.test_modules[64]", "manifest.test_modules.extra", "manifest.top"):
            context["dv_correction"]["validation_feedback"]["field"] = field
            self.assertEqual(manifest_transport_context(context)["dv_correction"]["validation_feedback"]["field"], field)

    def test_schema_versions_keep_every_manifest_and_specification_variant_distinct(self) -> None:
        cases = (("dv", {}, 2), ("change_planning", {}, 7),
                 ("change_planning", {"specification": {"readiness": {}}}, 8),
                 ("change_planning", {"specification": {"hardware_specification": {}, "readiness": {}}}, 9),
                 ("change_planning", {"improvement_intent": "feature"}, 10))
        for ollama in (False, True):
            identifiers = set()
            for stage, context, version in cases:
                with self.subTest(stage=stage, version=version, ollama=ollama):
                    schema_id, schema = _schema(stage, context, ollama=ollama)
                    self.assertTrue(schema_id.endswith((".ollama.v" if ollama else ".v") + str(version)))
                    self.assertNotIn(schema_id, identifiers)
                    identifiers.add(schema_id)
                    selected = schema["properties"]["manifest"]
                    self.assertIn("test_file_paths", selected["required"])
                    self.assertNotIn("test_modules", selected["properties"])
                    item = selected["properties"]["test_file_paths"]["items"]
                    self.assertEqual(item["description"], TEST_FILE_PATH_EXPECTATION)
                    self.assertEqual("pattern" in item, not ollama)
                    if stage == "change_planning":
                        expected_top = "rtl_top_module_name" if version == 10 else "top"
                        self.assertIn(expected_top, schema["properties"]["specification"]["required"])

    def test_each_provider_decodes_manifest_and_strips_local_deadlines(self) -> None:
        for provider in ("openai", "ollama"):
            for stage, intent in (("dv", None), ("change_planning", "dv"),
                                  ("change_planning", "optimization"), ("change_planning", "feature")):
                with self.subTest(provider=provider, stage=stage, intent=intent):
                    output = wire_contribution()
                    context = {"manifest": manifest(), "change_scope": {"manifest": manifest()},
                               "specification": specification(), "improvement_intent": intent,
                               "expert_deadline": time.monotonic() + 60.0}
                    if provider == "openai":
                        scripted = ScriptedStructuredGenerator(
                            descriptor=generator().descriptor,
                            outcomes=(ScriptedStructuredGeneration(encoded_output=output,
                                usage=GenerationUsage(input_tokens=12, output_tokens=34),
                                model=ModelMetadata(provider="openai", model_id="test-model"),
                                finish_reason=TextGenerationFinishReason.COMPLETED),))
                        adapter = AgentRigDesignExpert(scripted, model="test-model")
                    else:
                        class Runtime:
                            async def execute(self, request, run_context):
                                self.request, self.context = request, run_context
                                return AgentExecutionResult.succeeded(output,
                                    usage=AgentRuntimeUsage(input_tokens=12, output_tokens=34),
                                    provider_metadata={"provider": "ollama", "model": "qwen3:8b", "finish_reason": "stop"})
                        runtime = Runtime()
                        adapter = OllamaDesignExpert(runtime, model="qwen3:8b")
                    reply = asyncio.run(adapter.generate(stage, context, "a" * 32))
                    self.assertEqual(reply.output["manifest"]["test_modules"], ["test_sync_fifo"])
                    self.assertEqual((reply.input_tokens, reply.output_tokens), (12, 34))
                    if provider == "openai":
                        payload = json.loads(scripted.calls[0].request.input.prompt)
                        sent, instruction = payload["context"], payload["instruction"]
                    else:
                        sent, instruction = runtime.request.input["context"], runtime.request.instructions
                    self.assertEqual(list(sent["manifest"]["test_file_paths"]), ["dv/test_wire.py"])
                    self.assertEqual(list(sent["change_scope"]["manifest"]["test_file_paths"]), ["dv/test_wire.py"])
                    self.assertNotIn("expert_deadline", sent)
                    self.assertNotIn("discovery_deadline", sent)
                    self.assertIn("engineering_memory IDs are not approved requirements", instruction)
                    self.assertIn("dv_correction", instruction)

    def test_deadline_fields_are_process_local_and_cannot_compete(self) -> None:
        for field in ("expert_deadline", "discovery_deadline"):
            context = {field: 25.0, "manifest": manifest()}
            stripped, deadline = _provider_context(context)
            self.assertEqual(deadline, 25.0)
            self.assertNotIn(field, stripped)
            self.assertIn(field, context)
            for invalid in (False, 1, "25", float("inf"), float("nan")):
                with self.assertRaisesRegex(ValueError, "^expert_timeout_invalid$"):
                    _provider_context({field: invalid})
        for value in (25.0, None):
            with self.assertRaisesRegex(ValueError, "^expert_timeout_invalid$"):
                _provider_context({"expert_deadline": value, "discovery_deadline": value})


if __name__ == "__main__":
    unittest.main()
