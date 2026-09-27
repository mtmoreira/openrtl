"""Explicit stage-relative paths for reference-model provider responses.

OpenRTL owns the model/ destination, not the provider. Saved contributions keep
their canonical project-relative paths. This is a new wire contract, not a
repair mechanism for legacy provider-authored paths or generated source code.
"""

from __future__ import annotations

from openrtl.domain.design_session import JsonObject, object_value, sequence, source_path, text


def decode_reference_model_output(output: JsonObject) -> JsonObject:
    """Restore the advertised root only; never rewrite imports or infer files."""
    contribution = object_value(output, {"summary", "files", "manifest"})
    files = []
    for value in sequence(contribution["files"]):
        row = object_value(value, {"relative_path", "content"})
        relative_path = text(row["relative_path"], maximum=234)
        # No normalization: absolute paths, dot segments, empty components,
        # extensions and controls retain the strict canonical path checks.
        destination = source_path("model/" + relative_path)
        files.append({"path": destination, "content": row["content"]})
    return {"summary": contribution["summary"], "files": files,
            "manifest": contribution["manifest"]}
