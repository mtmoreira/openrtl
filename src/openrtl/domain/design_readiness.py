"""Explicit, anchored engineering decisions; presence is not semantic proof."""

from __future__ import annotations

from openrtl.domain.design_session import JsonObject, object_value, require, sequence, text


CATEGORIES = ("interfaces", "widths_signedness", "clock_reset", "timing_latency",
              "handshake", "exceptional_behavior", "acceptance")


def validate_readiness(value: object, spec: JsonObject) -> JsonObject:
    result = object_value(value, {"schema", "items"})
    require(result["schema"] == "openrtl.design-readiness.v1", "readiness_schema_invalid")
    items = sequence(result["items"], maximum=len(CATEGORIES))
    require(len(items) == len(CATEGORIES), "readiness_categories_missing")
    seen: set[str] = set()
    requirements = {row["id"] for row in spec["requirements"]}
    ports = {row["name"] for row in spec["ports"]}
    for item in items:
        row = object_value(item, {"category", "status", "decision", "requirement_ids", "ports"})
        require(isinstance(row["category"], str) and row["category"] in CATEGORIES and
                row["category"] not in seen, "readiness_category_invalid")
        seen.add(row["category"])
        require(row["status"] in ("specified", "not_applicable", "unresolved"), "readiness_status_invalid")
        text(row["decision"], maximum=4000)
        for field, allowed in (("requirement_ids", requirements), ("ports", ports)):
            refs = sequence(row[field])
            require(all(isinstance(ref, str) and ref in allowed for ref in refs) and
                    len(set(refs)) == len(refs), "readiness_anchor_invalid")
        if row["status"] == "specified":
            require(bool(row["requirement_ids"]), "readiness_requirement_anchor_missing")
        if row["category"] in ("interfaces", "widths_signedness") and row["status"] != "unresolved":
            require(row["status"] == "specified" and set(row["ports"]) == ports and bool(ports),
                    "readiness_port_decisions_missing")
        if row["category"] == "acceptance" and row["status"] != "unresolved":
            require(row["status"] == "specified" and set(row["requirement_ids"]) == requirements,
                    "readiness_acceptance_decisions_missing")
    return result


def require_ready(spec: JsonObject) -> None:
    require("readiness" in spec, "readiness_review_required_for_legacy_specification")
    result = validate_readiness(spec["readiness"], spec)
    require(all(row["status"] != "unresolved" for row in result["items"]) and
            not spec["questions"] and bool(spec["ports"]), "readiness_decisions_unresolved")
    if "hardware_specification" in spec:
        from openrtl.domain.hardware_specification import require_hardware_specification_complete
        require_hardware_specification_complete(spec)
