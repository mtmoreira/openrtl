"""Lossless provider names and records for specification proposals.

The saved domain contract retains ``top`` and ordered arrays. An explicit wire
name distinguishes the RTL module from schema labels; required object properties
express fixed inventories without generating their IDs or counts.
No engineering values, missing sections, or readiness anchors are inferred here.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator

from openrtl.domain.design_readiness import CATEGORIES
from openrtl.domain.design_session import JsonObject, require
from openrtl.domain.hardware_specification import SECTION_IDS


_INVENTORIES = (
    ("hardware_specification", "sections", SECTION_IDS, "id", ("status", "content")),
    ("readiness", "items", CATEGORIES, "category",
     ("status", "decision", "requirement_ids", "ports")),
)
_TOP_MODULE_FIELD = "rtl_top_module_name"


def uses_specification_transport(stage: str, context: JsonObject) -> bool:
    # DV/optimization must retain the exact saved specification, including order.
    return stage == "discovery" or (stage == "change_planning" and
                                    context.get("improvement_intent") == "feature")


def transport_schema(schema: JsonObject) -> JsonObject:
    result = copy.deepcopy(schema)
    specification = result["properties"]["specification"]
    variants = specification.get("anyOf", [specification])
    for variant in variants:
        if variant.get("type") != "object":
            continue
        variant["properties"][_TOP_MODULE_FIELD] = variant["properties"].pop("top")
        variant["required"] = [
            _TOP_MODULE_FIELD if field == "top" else field for field in variant["required"]
        ]
        for block, field, identifiers, discriminator, _ in _INVENTORIES:
            if block not in variant["properties"]:
                continue
            properties = variant["properties"][block]["properties"]
            row = properties[field]["items"]
            row["properties"].pop(discriminator)
            row["required"].remove(discriminator)
            properties[field] = {
                "type": "object",
                "properties": {identifier: copy.deepcopy(row) for identifier in identifiers},
                "required": list(identifiers), "additionalProperties": False,
            }
    return result


def _blocks(specification: object) -> Iterator[tuple[JsonObject, str, tuple[str, ...],
                                                   str, tuple[str, ...]]]:
    if isinstance(specification, dict):
        for block, field, identifiers, discriminator, fields in _INVENTORIES:
            container = specification.get(block)
            if isinstance(container, dict) and field in container:
                yield container, field, identifiers, discriminator, fields


def decode_specification_module_name(output: JsonObject) -> JsonObject:
    """Restore only an unambiguous field name, preserving all engineering values."""
    result = copy.deepcopy(output)
    specification = result.get("specification")
    if specification is not None:
        require(isinstance(specification, dict) and _TOP_MODULE_FIELD in specification and
                "top" not in specification, "expert_output_invalid")
        specification["top"] = specification.pop(_TOP_MODULE_FIELD)
    return result


def decode_specification_output(output: JsonObject) -> JsonObject:
    """Require the explicit module field and restore structural names/IDs/order.

    The ordinary domain validators still check every engineering value and link.
    Malformed records fail before they can be treated as a canonical specification.
    """
    result = decode_specification_module_name(output)
    for container, field, identifiers, discriminator, fields in _blocks(result.get("specification")):
        records = container[field]
        require(isinstance(records, dict) and set(records) == set(identifiers),
                "expert_output_invalid")
        rows = []
        for identifier in identifiers:
            row = records[identifier]
            require(isinstance(row, dict) and set(row) == set(fields), "expert_output_invalid")
            rows.append({discriminator: identifier, **row})
        container[field] = rows
    return result


def _encode_specification(specification: object) -> None:
    if (isinstance(specification, dict) and "top" in specification and
            _TOP_MODULE_FIELD not in specification):
        specification[_TOP_MODULE_FIELD] = specification.pop("top")
    # If both names occur, preserve both values for deterministic rejection and
    # correction. No field takes precedence over contradictory provider data.
    for container, field, identifiers, discriminator, fields in _blocks(specification):
        rows = container[field]
        if not isinstance(rows, list):
            continue
        records: JsonObject = {}
        for row in rows:
            if (not isinstance(row, dict) or set(row) != {discriminator, *fields} or
                    not isinstance(row[discriminator], str) or
                    row[discriminator] not in identifiers or row[discriminator] in records):
                # Preserve an ambiguous rejected candidate verbatim for correction;
                # never discard duplicate/unknown IDs or additional provider fields.
                break
            records[row[discriminator]] = {key: row[key] for key in fields}
        else:
            # A partial rejected inventory stays partial; the response schema, not
            # this encoder, requires the provider to supply the missing content.
            container[field] = records


def transport_context(context: JsonObject) -> JsonObject:
    """Present saved and rejected proposals in the response's transport shape."""
    result = copy.deepcopy(context)
    _encode_specification(result.get("specification"))
    correction = result.get("discovery_correction")
    if isinstance(correction, dict):
        candidate = correction.get("candidate")
        if isinstance(candidate, dict):
            _encode_specification(candidate.get("specification"))
        feedback = correction.get("validation_feedback")
        if isinstance(feedback, dict) and feedback.get("field") == "specification.top":
            feedback["field"] = "specification." + _TOP_MODULE_FIELD
    return result
