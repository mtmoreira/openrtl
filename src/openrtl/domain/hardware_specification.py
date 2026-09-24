"""Versioned, block-neutral structure for reviewable hardware specifications."""

from __future__ import annotations

from openrtl.domain.design_session import JsonObject, name, object_value, require, sequence, text


HARDWARE_SPECIFICATION_SCHEMA = "openrtl.hardware-specification.v1"
SECTION_IDS = (
    "purpose_scope",
    "parameters",
    "interfaces",
    "clock_reset_cdc",
    "functional_operation",
    "timing_performance",
    "exceptional_behavior",
    "integration",
)
SECTION_STATUSES = ("specified", "not_applicable", "unresolved")
PARAMETER_TYPES = ("integer", "boolean", "bit_vector", "string")


def validate_hardware_specification(value: object) -> JsonObject:
    """Validate document structure without inventing block-specific semantics.

    Ports, requirements, assumptions, open questions, acceptance criteria, and
    readiness anchors remain authoritative in their existing specification fields.
    This block supplies only the missing parameter inventory and fixed narrative
    sections.
    """
    document = object_value(value, {"schema", "parameters", "sections"})
    require(document["schema"] == HARDWARE_SPECIFICATION_SCHEMA,
            "hardware_specification_schema_invalid")
    parameters = sequence(document["parameters"], maximum=64)
    parameter_names: list[str] = []
    for item in parameters:
        row = object_value(item, {"name", "type", "default", "legal_values", "description"})
        parameter_names.append(name(row["name"]))
        require(row["type"] in PARAMETER_TYPES, "parameter_type_invalid")
        text(row["default"], maximum=1024)
        text(row["legal_values"], maximum=2048)
        text(row["description"], maximum=4000)
    require(len(parameter_names) == len(set(parameter_names)), "parameter_names_duplicate")

    sections = sequence(document["sections"], maximum=len(SECTION_IDS))
    require(len(sections) == len(SECTION_IDS), "hardware_specification_sections_missing")
    observed: list[str] = []
    statuses: dict[str, str] = {}
    for item in sections:
        row = object_value(item, {"id", "status", "content"})
        require(isinstance(row["id"], str) and row["id"] in SECTION_IDS,
                "hardware_specification_section_invalid")
        observed.append(row["id"])
        require(row["status"] in SECTION_STATUSES,
                "hardware_specification_section_status_invalid")
        statuses[row["id"]] = row["status"]
        text(row["content"], maximum=8000)
    require(tuple(observed) == SECTION_IDS,
            "hardware_specification_section_order_invalid")
    require(statuses["purpose_scope"] != "not_applicable",
            "hardware_specification_purpose_missing")
    require((bool(parameters) and statuses["parameters"] != "not_applicable") or
            (not parameters and statuses["parameters"] != "specified"),
            "hardware_specification_parameter_section_invalid")
    return document


def require_hardware_specification_complete(spec: JsonObject) -> None:
    require("hardware_specification" in spec,
            "hardware_specification_required")
    document = validate_hardware_specification(spec["hardware_specification"])
    require(all(row["status"] != "unresolved" for row in document["sections"]),
            "hardware_specification_unresolved")


def specification_completeness(spec: JsonObject) -> tuple[str, tuple[str, ...]]:
    if "hardware_specification" not in spec:
        return "legacy", ()
    document = validate_hardware_specification(spec["hardware_specification"])
    unresolved = [row["id"] for row in document["sections"]
                  if row["status"] == "unresolved"]
    if not spec.get("ports"):
        unresolved.append("ports")
    if spec.get("questions"):
        unresolved.append("open_questions")
    if "readiness" not in spec:
        unresolved.append("readiness")
    else:
        unresolved.extend("readiness." + row["category"] for row in spec["readiness"]["items"]
                          if row["status"] == "unresolved")
    return ("unresolved" if unresolved else "complete"), tuple(unresolved)
