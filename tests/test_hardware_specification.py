"""Versioned hardware-specification contract and synthetic review fixtures."""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
import tempfile
import unittest

from openrtl.adapters.design_generation import _schema, response_schema
from openrtl.adapters.design_session_store import DesignSessionStore
from openrtl.application.design_agent import DesignAgent, DesignPolicy
from openrtl.application.design_conversation import render_spec
from openrtl.domain.design_delegation import specification_warnings
from openrtl.domain.design_discovery import validate_discovery
from openrtl.domain.design_imports import validate_change_plan
from openrtl.domain.design_readiness import CATEGORIES, require_ready
from openrtl.domain.design_session import STAGES, content_digest, initial_state, validate_spec
from openrtl.domain.hardware_specification import SECTION_IDS, specification_completeness
from tests.test_design_agent import FakeExpert, manifest, specification


def hardware_document(*, parameters: list[dict[str, object]] | None = None,
                      overrides: dict[str, tuple[str, str]] | None = None) -> dict[str, object]:
    selected = parameters or []
    changes = overrides or {}
    sections = []
    for identifier in SECTION_IDS:
        status = "specified"
        content = "The " + identifier.replace("_", " ") + " contract is explicitly documented."
        if identifier == "parameters" and not selected:
            status, content = "not_applicable", "This block has no configurable parameters."
        if identifier == "integration":
            status, content = "not_applicable", "No registers, software interface, or integration sideband exists."
        if identifier in changes:
            status, content = changes[identifier]
        sections.append({"id": identifier, "status": status, "content": content})
    return {"schema": "openrtl.hardware-specification.v1", "parameters": selected,
            "sections": sections}


def reviewed_wire_spec() -> dict[str, object]:
    spec = specification()
    spec["hardware_specification"] = hardware_document()
    ports = [row["name"] for row in spec["ports"]]
    requirements = [row["id"] for row in spec["requirements"]]
    items = []
    for category in CATEGORIES:
        status = "specified" if category in {"interfaces", "widths_signedness", "acceptance"} else "not_applicable"
        items.append({"category": category, "status": status,
                      "decision": ("The wire contract is explicit." if status == "specified" else
                                   "This category does not apply to a combinational wire."),
                      "requirement_ids": requirements if status == "specified" else [],
                      "ports": ports if category in {"interfaces", "widths_signedness"} else []})
    spec["readiness"] = {"schema": "openrtl.design-readiness.v1", "items": items}
    return spec


def parameterized_fifo_spec() -> dict[str, object]:
    parameters = [
        {"name": "DATA_WIDTH", "type": "integer", "default": "32", "legal_values": "1 through 65536",
         "description": "Width of each stored data word and the push/pop data ports."},
        {"name": "DEPTH", "type": "integer", "default": "16", "legal_values": "powers of two, at least 2",
         "description": "Number of entries in the synchronous FIFO."},
    ]
    spec: dict[str, object] = {
        "title": "Parameterized synchronous ready/valid FIFO", "top": "sync_fifo",
        "behavior": "A single-clock FIFO preserves accepted data order under ready/valid backpressure.",
        "clock_reset": "One rising-edge clock and active-low synchronous reset; reset discards queued data.",
        "hardware_specification": hardware_document(parameters=parameters, overrides={
            "parameters": ("specified", "DATA_WIDTH and DEPTH are compile-time parameters with bounded legal values."),
            "interfaces": ("specified", "Push and pop ready/valid channels share clk and rst_n."),
            "clock_reset_cdc": ("specified", "All state uses clk; rst_n is sampled synchronously; no CDC exists."),
            "functional_operation": ("specified", "Accepted pushes are returned in order; simultaneous push/pop is supported."),
            "timing_performance": ("specified", "Backpressure is combinationally visible; accepted data is stable while stalled."),
            "exceptional_behavior": ("specified", "Full blocks pushes, empty suppresses pop_valid, and neither condition overwrites data."),
        }),
        "requirements": [
            {"id": "fifo.ordering", "text": "Return accepted words in FIFO order.",
             "acceptance": "A scoreboard checks ordering across directed and seeded transfers."},
            {"id": "fifo.full_empty", "text": "Block pushes while full and suppress pop_valid while empty.",
             "acceptance": "Boundary tests exercise full and empty without overwrite or underflow."},
            {"id": "fifo.stall", "text": "Hold pop_data stable while pop_valid is asserted and pop_ready is low.",
             "acceptance": "A stall-stability assertion checks every stalled cycle."},
            {"id": "fifo.simultaneous", "text": "Support simultaneous accepted push and pop.",
             "acceptance": "Directed tests cover simultaneous transfer at interior and boundary occupancy."},
            {"id": "fifo.reset", "text": "Reset discards all queued data and returns the FIFO to empty.",
             "acceptance": "Reset is asserted with queued data and empty behavior is checked afterward."},
            {"id": "fifo.latency", "text": "Document and preserve the selected latency and throughput contract.",
             "acceptance": "Tests check the stated first-word latency and one-transfer-per-cycle steady state."},
        ],
        "ports": [
            {"name": "clk", "direction": "input", "width": 1},
            {"name": "rst_n", "direction": "input", "width": 1},
            {"name": "push_valid", "direction": "input", "width": 1},
            {"name": "push_ready", "direction": "output", "width": 1},
            {"name": "push_data", "direction": "input", "width": 32},
            {"name": "pop_valid", "direction": "output", "width": 1},
            {"name": "pop_ready", "direction": "input", "width": 1},
            {"name": "pop_data", "direction": "output", "width": 32},
        ],
        "questions": [], "assumptions": [],
    }
    port_names = [row["name"] for row in spec["ports"]]
    requirement_ids = [row["id"] for row in spec["requirements"]]
    spec["readiness"] = {"schema": "openrtl.design-readiness.v1", "items": [
        {"category": category, "status": "specified",
         "decision": "The FIFO " + category.replace("_", " ") + " decision is explicit.",
         "requirement_ids": requirement_ids,
         "ports": port_names if category in {"interfaces", "widths_signedness"} else []}
        for category in CATEGORIES]}
    return spec


class HardwareSpecificationTest(unittest.TestCase):
    def test_legacy_specification_remains_valid_and_digest_stable(self) -> None:
        legacy = specification()
        before = content_digest(legacy)
        self.assertEqual(validate_spec(legacy), legacy)
        self.assertEqual(content_digest(validate_spec(legacy)), before)
        self.assertEqual(specification_completeness(legacy), ("legacy", ()))
        with self.assertRaisesRegex(ValueError, "hardware_specification_required"):
            validate_spec(legacy, require_hardware_specification=True)

    def test_fixed_sections_parameters_and_completion_are_strict(self) -> None:
        spec = reviewed_wire_spec()
        self.assertEqual(specification_completeness(validate_spec(spec)), ("complete", ()))
        require_ready(spec)
        reordered = copy.deepcopy(spec)
        reordered["hardware_specification"]["sections"][0:2] = list(reversed(
            reordered["hardware_specification"]["sections"][0:2]))
        with self.assertRaisesRegex(ValueError, "section_order_invalid"):
            validate_spec(reordered)
        inconsistent = copy.deepcopy(spec)
        inconsistent["hardware_specification"]["parameters"] = [{
            "name": "WIDTH", "type": "integer", "default": "8", "legal_values": "positive",
            "description": "Data width."}]
        with self.assertRaisesRegex(ValueError, "parameter_section_invalid"):
            validate_spec(inconsistent)
        unresolved = copy.deepcopy(spec)
        unresolved["hardware_specification"]["sections"][5]["status"] = "unresolved"
        self.assertEqual(specification_completeness(unresolved), ("unresolved", ("timing_performance",)))
        with self.assertRaisesRegex(ValueError, "hardware_specification_unresolved"):
            require_ready(unresolved)

    def test_provider_schemas_require_the_versioned_document(self) -> None:
        properties = response_schema("discovery")["properties"]["specification"]["anyOf"][0]
        self.assertIn("hardware_specification", properties["required"])
        sections = properties["properties"]["hardware_specification"]["properties"]["sections"]
        self.assertEqual(sections["minItems"], len(SECTION_IDS))
        self.assertEqual(sections["items"]["properties"]["id"]["enum"], list(SECTION_IDS))
        self.assertEqual(_schema("discovery", {}, ollama=True)[0],
                         "openrtl.design.discovery.ollama.v3")

    def test_live_discovery_contract_rejects_a_legacy_shaped_proposal(self) -> None:
        response = {"reply": "Prepared a reviewable draft.", "specification": specification(),
                    "questions_asked": [], "question_plan": [], "resolved_questions": [],
                    "engineering_memory": []}
        with self.assertRaisesRegex(ValueError, "hardware_specification_required"):
            validate_discovery(response, initial_state(), "Build a wire", require_plan=True,
                               require_hardware_specification=True)
        response["specification"] = reviewed_wire_spec()
        candidate = validate_discovery(response, initial_state(), "Build a wire", require_plan=True,
                                       require_hardware_specification=True)
        self.assertEqual(candidate.specification["hardware_specification"]["schema"],
                         "openrtl.hardware-specification.v1")

    def test_design_agent_enforces_the_versioned_provider_contract(self) -> None:
        class VersionedFakeExpert(FakeExpert):
            discovery_contract_version = "v8"
            specification_contract_version = "v1"

        response = {"reply": "Prepared a reviewable draft.", "specification": specification(),
                    "questions_asked": [], "question_plan": [], "resolved_questions": [],
                    "engineering_memory": []}
        with tempfile.TemporaryDirectory() as directory:
            store = DesignSessionStore(Path(directory).resolve() / "project", create=True)
            expert = VersionedFakeExpert()
            expert.responses["discovery"] = response
            agent = DesignAgent(store, expert, None,
                                policy=DesignPolicy(max_discovery_corrections=0))
            try:
                with self.assertRaisesRegex(ValueError, "expert_output_invalid"):
                    asyncio.run(agent.discuss("Build a wire"))
                self.assertIsNone(store.read()["spec"])
                response["specification"] = reviewed_wire_spec()
                state = asyncio.run(agent.discuss("Build a wire"))
                self.assertEqual(state["spec"]["hardware_specification"]["schema"],
                                 "openrtl.hardware-specification.v1")
            finally:
                store.close()

    def test_provider_change_validation_requires_explicit_legacy_migration(self) -> None:
        plan = {"schema": "openrtl.design-change.v1", "base_input_digest": "sha256:" + "1" * 64,
                "base_files": {"rtl/wire_top.sv": "sha256:" + "2" * 64,
                               "rtl/properties.sv": "sha256:" + "3" * 64,
                               "dv/test_wire.py": "sha256:" + "4" * 64},
                "specification": specification(),
                "stage_paths": {stage: (["docs/architecture.md"] if stage == "architecture" else [])
                                for stage in STAGES},
                "manifest": manifest()}
        with self.assertRaisesRegex(ValueError, "hardware_specification_required"):
            validate_change_plan(plan, require_hardware_specification=True)
        plan["specification"] = reviewed_wire_spec()
        self.assertEqual(validate_change_plan(plan, require_hardware_specification=True), plan)

    def test_dv_and_optimization_schema_can_retain_an_exact_legacy_specification(self) -> None:
        legacy_id, legacy_schema = _schema("change_planning", {
            "improvement_intent": "dv", "specification": specification()})
        feature_id, feature_schema = _schema("change_planning", {
            "improvement_intent": "feature", "specification": specification()})
        self.assertEqual(legacy_id, "openrtl.design.change_planning.v2")
        self.assertNotIn("hardware_specification",
                         legacy_schema["properties"]["specification"]["required"])
        self.assertEqual(feature_id, "openrtl.design.change_planning.v4")
        self.assertIn("hardware_specification",
                      feature_schema["properties"]["specification"]["required"])

    def test_delegated_format_migration_is_a_reviewable_warning(self) -> None:
        warnings = specification_warnings(specification(), reviewed_wire_spec())
        self.assertIn("hardware_specification", {row["subject"] for row in warnings})

    def test_parameterized_fifo_fixture_covers_review_material_without_generic_fifo_rules(self) -> None:
        spec = validate_spec(parameterized_fifo_spec(), require_hardware_specification=True)
        require_ready(spec)
        self.assertEqual([row["name"] for row in spec["hardware_specification"]["parameters"]],
                         ["DATA_WIDTH", "DEPTH"])
        acceptance = " ".join(row["acceptance"].casefold() for row in spec["requirements"])
        for phrase in ("ordering", "full and empty", "stall-stability", "simultaneous transfer",
                       "reset is asserted with queued data", "latency"):
            self.assertIn(phrase, acceptance)

    def test_text_review_includes_every_authoritative_layer(self) -> None:
        output: list[str] = []
        render_spec(parameterized_fifo_spec(), output.append)
        rendered = "\n".join(output)
        for expected in ("openrtl.hardware-specification.v1", "completeness: complete",
                         "Parameter DATA_WIDTH", "Section purpose scope", "Port push_data",
                         "Requirement fifo.ordering", "Acceptance:", "Readiness interfaces"):
            self.assertIn(expected, rendered)


if __name__ == "__main__":
    unittest.main()
