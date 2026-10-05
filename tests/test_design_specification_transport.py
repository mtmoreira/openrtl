"""Lossless transport for fixed specification inventories, without provider calls."""

from __future__ import annotations

import copy
from typing import Any
import unittest

from openrtl.adapters.design_generation import _ollama_schema, _schema, response_schema
from openrtl.adapters.design_manifest_transport import manifest_transport_schema
from openrtl.adapters.design_specification_transport import (
    decode_specification_module_name,
    decode_specification_output,
    transport_context,
    transport_schema,
    uses_specification_transport,
)
from openrtl.domain.design_readiness import CATEGORIES
from openrtl.domain.design_session import content_digest, validate_spec
from openrtl.domain.hardware_specification import SECTION_IDS


def canonical_specification() -> dict[str, Any]:
    """A valid synthetic fixture authored independently of transport helpers."""
    return {
        "title": "Reviewable wire", "top": "wire_top",
        "behavior": "The output equals the input combinationally.",
        "clock_reset": "There is no clock, reset, or stored state.",
        "requirements": [{"id": "wire.copy", "text": "Copy input a to output y.",
                          "acceptance": "Check both possible input values."}],
        "ports": [{"name": "a", "direction": "input", "width": 1},
                  {"name": "y", "direction": "output", "width": 1}],
        "questions": [], "assumptions": [],
        "hardware_specification": {
            "schema": "openrtl.hardware-specification.v1", "parameters": [],
            "sections": [
                {"id": "purpose_scope", "status": "specified", "content": "Copy one bit."},
                {"id": "parameters", "status": "not_applicable", "content": "No parameters."},
                {"id": "interfaces", "status": "specified", "content": "Unsigned a and y."},
                {"id": "clock_reset_cdc", "status": "not_applicable", "content": "No state or clocks."},
                {"id": "functional_operation", "status": "specified", "content": "Assign y from a."},
                {"id": "timing_performance", "status": "specified", "content": "Combinational propagation."},
                {"id": "exceptional_behavior", "status": "not_applicable", "content": "No transfer errors."},
                {"id": "integration", "status": "not_applicable", "content": "No registers or software."},
            ],
        },
        "readiness": {
            "schema": "openrtl.design-readiness.v1",
            "items": [
                {"category": category, "status": "specified",
                 "decision": "The " + category + " contract is documented.",
                 "requirement_ids": ["wire.copy"],
                 "ports": ["a", "y"] if category in ("interfaces", "widths_signedness") else []}
                for category in ("interfaces", "widths_signedness", "clock_reset", "timing_latency",
                                 "handshake", "exceptional_behavior", "acceptance")
            ],
        },
    }


def wire_output() -> dict[str, Any]:
    """Build provider-shaped input directly, never using the production encoder."""
    spec = canonical_specification()
    spec["rtl_top_module_name"] = spec.pop("top")
    spec["hardware_specification"]["sections"] = {
        row["id"]: {"status": row["status"], "content": row["content"]}
        for row in reversed(spec["hardware_specification"]["sections"])
    }
    spec["readiness"]["items"] = {
        row["category"]: {"status": row["status"], "decision": row["decision"],
                          "requirement_ids": row["requirement_ids"], "ports": row["ports"]}
        for row in reversed(spec["readiness"]["items"])
    }
    return {"reply": "structured", "specification": spec, "questions_asked": [],
            "question_plan": [], "resolved_questions": [], "engineering_memory": []}


class DesignSpecificationTransportTest(unittest.TestCase):
    def test_transport_selection_is_limited_to_discovery_and_feature_changes(self) -> None:
        self.assertTrue(uses_specification_transport("discovery", {}))
        self.assertTrue(uses_specification_transport("change_planning", {"improvement_intent": "feature"}))
        for stage, intent in (("change_planning", "dv"), ("change_planning", "optimization"),
                              ("change_planning", None), ("dv", "feature"), ("rtl", "feature"),
                              ("analyze", None), ("explain", None)):
            with self.subTest(stage=stage, intent=intent):
                self.assertFalse(uses_specification_transport(stage, {"improvement_intent": intent}))

    def test_required_named_inventories_survive_the_ollama_schema_reduction(self) -> None:
        for stage, context, expected_id in (
            ("discovery", {}, "openrtl.design.discovery"),
            ("change_planning", {"improvement_intent": "feature"}, "openrtl.design.change_planning"),
        ):
            for ollama in (False, True):
                with self.subTest(stage=stage, ollama=ollama):
                    identifier, schema = _schema(stage, context, ollama=ollama)
                    suffix = ((".ollama.v6" if ollama else ".v11") if stage == "discovery"
                              else ".ollama.v10" if ollama else ".v10")
                    self.assertEqual(identifier, expected_id + suffix)
                    spec = schema["properties"]["specification"]
                    if stage == "discovery":
                        spec = spec["anyOf"][0]
                    self.assertIn("rtl_top_module_name", spec["required"])
                    self.assertNotIn("top", spec["required"])
                    self.assertNotIn("top", spec["properties"])
                    self.assertEqual(spec["properties"]["rtl_top_module_name"]["type"], "string")
                    sections = spec["properties"]["hardware_specification"]["properties"]["sections"]
                    readiness = spec["properties"]["readiness"]["properties"]["items"]
                    for inventory, names, row_keys in (
                        (sections, SECTION_IDS, {"status", "content"}),
                        (readiness, CATEGORIES, {"status", "decision", "requirement_ids", "ports"}),
                    ):
                        self.assertEqual(inventory["type"], "object")
                        self.assertEqual(inventory["required"], list(names))
                        self.assertEqual(set(inventory["properties"]), set(names))
                        self.assertIs(inventory["additionalProperties"], False)
                        for row in inventory["properties"].values():
                            self.assertEqual(set(row["properties"]), row_keys)
                            self.assertEqual(set(row["required"]), row_keys)
                            self.assertIs(row["additionalProperties"], False)
                        self.assertEqual(_ollama_schema(inventory)["required"], list(names))

    def test_schema_conversion_copies_and_preserves_unrelated_constraints(self) -> None:
        original = response_schema("discovery")
        before = copy.deepcopy(original)
        converted = transport_schema(original)
        self.assertEqual(original, before)
        spec = converted["properties"]["specification"]["anyOf"][0]
        original_spec = original["properties"]["specification"]["anyOf"][0]
        self.assertEqual(spec["properties"]["rtl_top_module_name"], original_spec["properties"]["top"])
        self.assertEqual(spec["properties"]["ports"], original_spec["properties"]["ports"])
        self.assertEqual(spec["properties"]["hardware_specification"]["properties"]["parameters"],
                         original_spec["properties"]["hardware_specification"]["properties"]["parameters"])
        converted["properties"]["reply"]["enum"].append("must not leak")
        self.assertEqual(original, before)

    def test_complete_unordered_records_decode_to_canonical_lists_without_mutation(self) -> None:
        original = wire_output()
        before = copy.deepcopy(original)
        decoded = decode_specification_output(original)
        self.assertEqual(decoded["specification"], canonical_specification())
        self.assertEqual(validate_spec(decoded["specification"], require_hardware_specification=True),
                         canonical_specification())
        self.assertEqual(original, before)
        decoded["specification"]["readiness"]["items"][0]["ports"].append("must_not_leak")
        self.assertEqual(original, before)

    def test_captured_seven_section_shape_cannot_silently_invent_integration(self) -> None:
        captured = canonical_specification()
        captured["hardware_specification"]["sections"].pop()
        self.assertEqual(len(captured["hardware_specification"]["sections"]), 7)
        with self.assertRaisesRegex(ValueError, "hardware_specification_sections_missing"):
            validate_spec(captured, require_hardware_specification=True)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            decode_specification_output({"specification": captured})
        before = copy.deepcopy(captured)
        correction = transport_context({"discovery_correction": {"candidate": {"specification": captured}}})
        candidate = correction["discovery_correction"]["candidate"]
        sections = candidate["specification"]["hardware_specification"]["sections"]
        self.assertEqual(set(sections), set(SECTION_IDS) - {"integration"})
        self.assertEqual(len(sections), 7)
        self.assertEqual(captured, before)
        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
            decode_specification_output(candidate)

    def test_missing_and_extra_inventory_and_row_keys_are_rejected(self) -> None:
        inventories = (("hardware_specification", "sections", "integration", "content", "id"),
                       ("readiness", "items", "acceptance", "decision", "category"))
        for block, field, name, row_key, extra_key in inventories:
            for mode in ("missing_inventory_key", "extra_inventory_key", "missing_row_key", "extra_row_key"):
                with self.subTest(block=block, mode=mode):
                    output = wire_output()
                    rows = output["specification"][block][field]
                    if mode == "missing_inventory_key":
                        del rows[name]
                    elif mode == "extra_inventory_key":
                        rows["unknown"] = copy.deepcopy(rows[name])
                    elif mode == "missing_row_key":
                        del rows[name][row_key]
                    else:
                        rows[name][extra_key] = name
                    before = copy.deepcopy(output)
                    with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                        decode_specification_output(output)
                    self.assertEqual(output, before)

    def test_wrong_inventory_or_row_container_types_are_rejected(self) -> None:
        for block, field, name in (("hardware_specification", "sections", "integration"),
                                   ("readiness", "items", "acceptance")):
            for value in (None, [], "invalid", 7, True):
                for at_row in (False, True):
                    with self.subTest(block=block, value=value, at_row=at_row):
                        output = wire_output()
                        if at_row:
                            output["specification"][block][field][name] = value
                        else:
                            output["specification"][block][field] = value
                        with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                            decode_specification_output(output)

    def test_decoder_preserves_invalid_semantics_for_strict_domain_validation(self) -> None:
        output = wire_output()
        output["specification"]["ports"][0]["width"] = 0
        decoded = decode_specification_output(output)
        self.assertEqual(decoded["specification"]["ports"][0]["width"], 0)
        self.assertIs(type(decoded["specification"]["ports"][0]["width"]), int)
        with self.assertRaisesRegex(ValueError, "^port_width_invalid$"):
            validate_spec(decoded["specification"])

    def test_captured_schema_label_is_preserved_and_rejected_as_a_module_identifier(self) -> None:
        output = wire_output()
        output["specification"]["rtl_top_module_name"] = "openrtl.hardware-specification.v1"
        before = copy.deepcopy(output)
        decoded = decode_specification_output(output)
        self.assertEqual(decoded["specification"]["top"], "openrtl.hardware-specification.v1")
        self.assertIs(type(decoded["specification"]["top"]), str)
        self.assertNotIn("rtl_top_module_name", decoded["specification"])
        with self.assertRaisesRegex(ValueError, "^identifier_invalid$"):
            validate_spec(decoded["specification"], require_hardware_specification=True)
        self.assertEqual(output, before)

    def test_module_field_values_are_never_sanitized_or_coerced(self) -> None:
        for value, error in (("wire.top", "identifier_invalid"),
                             (" wire_top ", "identifier_invalid"),
                             (1, "text_type_invalid"), (True, "text_type_invalid"),
                             (None, "text_type_invalid"), ({"name": "wire_top"}, "text_type_invalid")):
            with self.subTest(value=value):
                output = wire_output()
                output["specification"]["rtl_top_module_name"] = value
                decoded = decode_specification_output(output)
                self.assertEqual(decoded["specification"]["top"], value)
                self.assertIs(type(decoded["specification"]["top"]), type(value))
                with self.assertRaisesRegex(ValueError, "^" + error + "$"):
                    validate_spec(decoded["specification"])

    def test_missing_or_ambiguous_module_fields_are_rejected_without_mutation(self) -> None:
        for mode in ("missing", "canonical_only", "both_equal", "both_different"):
            with self.subTest(mode=mode):
                output = wire_output()
                spec = output["specification"]
                if mode == "missing":
                    del spec["rtl_top_module_name"]
                elif mode == "canonical_only":
                    spec["top"] = spec.pop("rtl_top_module_name")
                else:
                    spec["top"] = "wire_top" if mode == "both_equal" else "other_top"
                before = copy.deepcopy(output)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    decode_specification_output(output)
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    decode_specification_module_name(output)
                self.assertEqual(output, before)

    def test_module_only_decode_preserves_malformed_inventory_for_domain_diagnostics(self) -> None:
        for block, field, identifier in (("hardware_specification", "sections", "integration"),
                                        ("readiness", "items", "acceptance")):
            with self.subTest(block=block):
                output = wire_output()
                del output["specification"][block][field][identifier]
                before = copy.deepcopy(output)
                decoded = decode_specification_module_name(output)
                expected = copy.deepcopy(output)
                expected["specification"]["top"] = expected["specification"].pop("rtl_top_module_name")
                self.assertEqual(decoded, expected)
                self.assertEqual(decoded["specification"][block][field], before["specification"][block][field])
                with self.assertRaisesRegex(ValueError, "^expert_output_invalid$"):
                    decode_specification_output(output)
                self.assertEqual(output, before)

    def test_context_translates_only_top_feedback_path_and_preserves_invalid_value(self) -> None:
        for field in ("specification.top", "specification.ports[0].name",
                      "specification.hardware_specification.parameters[0].name"):
            with self.subTest(field=field):
                spec = canonical_specification()
                spec["top"] = "openrtl.hardware-specification.v1"
                context = {"discovery_correction": {
                    "attempt": 1, "validation_code": "identifier_invalid",
                    "validation_feedback": {"field": field, "expected": "synthetic trusted rule"},
                    "candidate": {"specification": spec}}}
                before = copy.deepcopy(context)
                correction = transport_context(context)["discovery_correction"]
                expected_field = "specification.rtl_top_module_name" if field == "specification.top" else field
                self.assertEqual(correction["validation_feedback"],
                                 {"field": expected_field, "expected": "synthetic trusted rule"})
                encoded = correction["candidate"]["specification"]
                self.assertNotIn("top", encoded)
                self.assertEqual(encoded["rtl_top_module_name"], "openrtl.hardware-specification.v1")
                self.assertEqual(context, before)

    def test_context_preserves_both_module_fields_and_already_encoded_candidates(self) -> None:
        for canonical in (False, True):
            for saved in (False, True):
                with self.subTest(canonical=canonical, saved=saved):
                    spec = wire_output()["specification"]
                    if canonical:
                        spec["top"] = "different_top"
                    context = ({"specification": spec} if saved else
                               {"discovery_correction": {"candidate": {"specification": spec}}})
                    before = copy.deepcopy(context)
                    self.assertEqual(transport_context(context), before)
                    self.assertEqual(context, before)

    def test_context_encodes_saved_and_rejected_specs_without_mutation(self) -> None:
        context = {"specification": canonical_specification(),
                   "discovery_correction": {"attempt": 1, "validation_code": "hardware_specification_sections_missing",
                                            "candidate": {"specification": canonical_specification()}},
                   "unrelated": {"sections": ["keep", "these"]}}
        before = copy.deepcopy(context)
        encoded = transport_context(context)
        expected = wire_output()["specification"]
        self.assertEqual(encoded["specification"], expected)
        self.assertEqual(encoded["discovery_correction"]["candidate"]["specification"], expected)
        self.assertEqual(encoded["unrelated"], context["unrelated"])
        self.assertEqual(context, before)
        encoded["specification"]["readiness"]["items"]["interfaces"]["ports"].append("must_not_leak")
        self.assertEqual(context, before)

    def test_context_preserves_ambiguous_or_malformed_lists_without_data_loss(self) -> None:
        for block, field, identity in (("hardware_specification", "sections", "id"),
                                       ("readiness", "items", "category")):
            for mode in ("duplicate", "unknown", "missing_identity", "wrong_identity_type",
                         "extra_field", "wrong_row_type"):
                with self.subTest(block=block, mode=mode):
                    spec = canonical_specification()
                    rows = spec[block][field]
                    if mode == "duplicate":
                        rows.append(copy.deepcopy(rows[0]))
                    elif mode == "unknown":
                        rows[0][identity] = "unknown"
                    elif mode == "missing_identity":
                        del rows[0][identity]
                    elif mode == "wrong_identity_type":
                        rows[0][identity] = {"invalid": "identity"}
                    elif mode == "extra_field":
                        rows[0]["extra"] = "must survive"
                    else:
                        rows[0] = None
                    context = {"specification": spec}
                    before = copy.deepcopy(context)
                    encoded = transport_context(context)
                    self.assertEqual(encoded["specification"][block][field], rows)
                    self.assertEqual(context, before)

    def test_absent_optional_blocks_and_null_specification_are_not_invented(self) -> None:
        for value in ({"specification": None}, {"summary": "No specification in this response."}):
            self.assertEqual(decode_specification_output(value), value)
            self.assertEqual(transport_context(value), value)
        legacy = canonical_specification()
        del legacy["hardware_specification"]
        del legacy["readiness"]
        encoded = copy.deepcopy(legacy)
        encoded["rtl_top_module_name"] = encoded.pop("top")
        self.assertEqual(decode_specification_output({"specification": encoded}), {"specification": legacy})
        self.assertEqual(transport_context({"specification": legacy}), {"specification": encoded})

    def test_dv_and_optimization_preserve_existing_list_schemas_and_identity(self) -> None:
        for intent in ("dv", "optimization"):
            for with_readiness, with_hardware in ((False, False), (True, False), (True, True)):
                for ollama in (False, True):
                    with self.subTest(intent=intent, readiness=with_readiness, hardware=with_hardware,
                                      ollama=ollama):
                        existing: dict[str, Any] = {}
                        if with_readiness:
                            existing["readiness"] = {}
                        if with_hardware:
                            existing["hardware_specification"] = {}
                        context = {"improvement_intent": intent, "specification": existing}
                        identifier, actual = _schema("change_planning", context, ollama=ollama)
                        expected = response_schema("change_planning", include_readiness=with_readiness,
                                                   include_hardware_specification=with_hardware)
                        expected = manifest_transport_schema(expected)
                        if ollama:
                            expected = _ollama_schema(expected)
                        version = 9 if with_hardware else 8 if with_readiness else 7
                        self.assertEqual(identifier, "openrtl.design.change_planning." +
                                         ("ollama." if ollama else "") + "v" + str(version))
                        self.assertEqual(actual, expected)
                        self.assertEqual(content_digest(actual), content_digest(expected))
                        props = actual["properties"]["specification"]["properties"]
                        self.assertIn("top", props)
                        self.assertNotIn("rtl_top_module_name", props)
                        if with_readiness:
                            self.assertEqual(props["readiness"]["properties"]["items"]["type"], "array")
                        if with_hardware:
                            self.assertEqual(props["hardware_specification"]["properties"]["sections"]["type"], "array")

    def test_dv_optimization_schema_selection_preserves_saved_order_and_digest(self) -> None:
        saved = canonical_specification()
        # Readiness order is legal domain data and must remain exact for these
        # intents; unlike new proposals it must not be canonicalized by transport.
        saved["readiness"]["items"].reverse()
        self.assertNotEqual([row["category"] for row in saved["readiness"]["items"]], list(CATEGORIES))
        before = copy.deepcopy(saved)
        digest = content_digest(saved)
        for intent in ("dv", "optimization"):
            context = {"improvement_intent": intent, "specification": saved}
            self.assertFalse(uses_specification_transport("change_planning", context))
            for ollama in (False, True):
                _schema("change_planning", context, ollama=ollama)
                self.assertEqual(saved, before)
                self.assertEqual(content_digest(validate_spec(saved)), digest)


if __name__ == "__main__":
    unittest.main()
