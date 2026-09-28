"""Explicit file references for provider simulation manifests.

Saved manifests and simulator runners retain bare Python module identifiers.
The wire contract names their exact flat ``dv/<identifier>.py`` files instead;
the conversion never searches files, normalizes paths, or infers identifiers.
"""

from __future__ import annotations

import copy
import re

from openrtl.domain.design_session import JsonObject, name, sequence


_FILE_FIELD = "test_file_paths"
_MODULE_FIELD = "test_modules"
_FILE_PATTERN = r"dv/([A-Za-z_][A-Za-z0-9_]{0,127})\.py"
TEST_FILE_PATH_EXPECTATION = (
    "A JSON string naming an exact flat dv/<identifier>.py file, where identifier "
    "has 1 to 128 ASCII letters, digits or underscores and starts with a letter "
    "or underscore; no absolute paths, nested directories or dot segments."
)


def uses_manifest_transport(stage: str) -> bool:
    return stage in ("dv", "change_planning")


def manifest_transport_schema(schema: JsonObject) -> JsonObject:
    result = copy.deepcopy(schema)
    manifest = result["properties"]["manifest"]
    manifest["properties"].pop(_MODULE_FIELD)
    manifest["properties"][_FILE_FIELD] = {
        "type": "array", "minItems": 1, "maxItems": 64,
        "items": {"type": "string", "pattern": "^" + _FILE_PATTERN + "$",
                  "maxLength": 134, "description": TEST_FILE_PATH_EXPECTATION},
    }
    manifest["required"] = [
        _FILE_FIELD if field == _MODULE_FIELD else field for field in manifest["required"]
    ]
    return result


def _module(value: object, *, legacy: bool) -> str:
    if isinstance(value, str):
        matched = re.fullmatch(_FILE_PATTERN, value)
        if matched is not None:
            return matched.group(1)
    # Compatibility is deliberately limited to valid old bare names and the
    # exact reversible file spelling. name() retains the strict domain rule.
    if legacy:
        return name(value)
    raise ValueError("identifier_invalid")


def decode_manifest_output(output: JsonObject) -> JsonObject:
    """Decode one unambiguous spelling or preserve the entire rejected output.

    Keep malformed responses available for capture, metering and deterministic
    domain rejection. List order and duplicates survive successful conversion.
    """
    result = copy.deepcopy(output)
    manifest = result.get("manifest")
    if not isinstance(manifest, dict):
        return result
    if _FILE_FIELD in manifest and _MODULE_FIELD in manifest:
        return result
    field = _FILE_FIELD if _FILE_FIELD in manifest else _MODULE_FIELD
    if field not in manifest:
        return result
    try:
        modules = [_module(value, legacy=field == _MODULE_FIELD)
                   for value in sequence(manifest[field])]
    except ValueError:
        return result
    manifest.pop(field)
    manifest[_MODULE_FIELD] = modules
    return result


def _encode_manifest(manifest: object) -> None:
    if not isinstance(manifest, dict) or _FILE_FIELD in manifest or _MODULE_FIELD not in manifest:
        return
    try:
        paths = ["dv/" + _module(value, legacy=True) + ".py"
                 for value in sequence(manifest[_MODULE_FIELD])]
    except ValueError:
        # Preserve a rejected candidate's fields and values without dropping
        # unknown spellings, malformed elements, or conflicting aliases.
        return
    manifest.pop(_MODULE_FIELD)
    manifest[_FILE_FIELD] = paths


def manifest_transport_context(context: JsonObject) -> JsonObject:
    result = copy.deepcopy(context)
    _encode_manifest(result.get("manifest"))
    change_scope = result.get("change_scope")
    if isinstance(change_scope, dict):
        _encode_manifest(change_scope.get("manifest"))
    correction = result.get("dv_correction")
    if isinstance(correction, dict):
        candidate = correction.get("candidate")
        if isinstance(candidate, dict):
            _encode_manifest(candidate.get("manifest"))
        feedback = correction.get("validation_feedback")
        if isinstance(feedback, dict):
            field = feedback.get("field")
            matched = (re.fullmatch(r"manifest\.test_modules(\[(?:[0-9]|[1-5][0-9]|6[0-3])\])?", field)
                       if isinstance(field, str) else None)
            if matched is not None:
                feedback["field"] = "manifest." + _FILE_FIELD + (matched.group(1) or "")
                feedback["expected"] = TEST_FILE_PATH_EXPECTATION
    return result
