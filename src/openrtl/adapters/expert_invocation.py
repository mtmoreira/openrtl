"""Controlled AgentRig invocation for untrusted expert source-edit output."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, cast

from agentrig.capabilities import (
    CapabilityFeature,
    CapabilityKind,
    StructuredGenerationRequest,
    StructuredGenerator,
    StructuredOutputSchema,
    TextGenerationFinishReason,
    TextGenerationRequest,
)
from agentrig.core import JsonValue, RunContext
from openrtl.adapters.expert_source_edits import (
    accept_expert_source_edit_payload,
    load_expert_source_edit_request,
    prepare_expert_source_edit_request,
    validate_expert_source_edit_response,
)
from openrtl.application.expert_edits import ExpertSourceEditRequest
from openrtl.application.expert_invocation import (
    ExpertInvocationPolicy,
    ExpertInvocationReport,
    build_expert_invocation_report,
    invocation_payload_digest,
)


_MAX_CHANGES = 16
_MAX_FINDINGS = 32
_MAX_OBSERVATIONS = 64
_MAX_EXCERPT_BYTES = 32 * 1024


@dataclass(frozen=True)
class ExpertInvocationArtifacts:
    envelope: dict[str, Any]
    response: dict[str, Any]
    edit_spec: dict[str, Any]
    suggestion: dict[str, Any]
    report: ExpertInvocationReport


async def invoke_expert_source_edits(
    root: Path,
    *,
    request_path: Path,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
    generator: StructuredGenerator[dict[str, Any]],
    policy: ExpertInvocationPolicy,
    context: RunContext,
) -> ExpertInvocationArtifacts:
    """Invoke one tool-free structured turn and feed only strict output to M17."""

    resolved_root = root.resolve(strict=True)
    if not isinstance(policy, ExpertInvocationPolicy):
        raise TypeError("expert invocation policy is invalid")
    if not isinstance(context, RunContext):
        raise TypeError("expert invocation context is invalid")
    request = load_expert_source_edit_request(resolved_root, request_path)
    rebuilt = prepare_expert_source_edit_request(
        resolved_root,
        proposal_path=proposal_path,
        debug_session_path=debug_session_path,
        source_path=source_path,
    )
    if rebuilt.payload() != request.payload():
        raise ValueError("expert invocation evidence does not match the reviewed request")
    _require_generator(generator, policy)
    envelope = _build_envelope(resolved_root, request, policy)
    encoded_envelope = _canonical_json(envelope)
    if len(encoded_envelope) > policy.max_input_bytes:
        raise ValueError("expert invocation envelope exceeds the input byte limit")
    schema = StructuredOutputSchema[dict[str, Any]](
        schema_id="openrtl.expert-source-edit-output.v1",
        json_schema=_response_schema(request),
        decoder=lambda value: _decode_response(request, value, policy.max_output_bytes),
    )
    generation_request = StructuredGenerationRequest(
        input=TextGenerationRequest(
            prompt=encoded_envelope.decode("utf-8"),
            max_output_tokens=policy.max_output_tokens,
        ),
        output_schema=schema,
    )
    generation_request.require_supported_by(generator.descriptor)
    child = context.derive_child(
        timeout_seconds=policy.timeout_seconds,
        labels={"openrtl_operation": "expert_source_edit_invocation"},
        correlation={"expert_request_id": request.request_id},
    )
    try:
        result = await asyncio.wait_for(
            generator.generate(generation_request, child),
            timeout=policy.timeout_seconds,
        )
    except TimeoutError:
        raise ValueError("expert invocation exceeded its timeout") from None
    if result.finish_reason is not TextGenerationFinishReason.COMPLETED:
        raise ValueError("expert invocation did not complete its strict output")
    if result.model.provider != policy.provider or result.model.model_id != policy.model:
        raise ValueError("expert invocation result model identity differs from policy")
    response = result.output
    encoded_response = _canonical_json(response)
    if len(encoded_response) > policy.max_output_bytes:
        raise ValueError("expert invocation response exceeds the output byte limit")
    edit_spec, suggestion = accept_expert_source_edit_payload(
        request,
        response,
        response_bytes=encoded_response,
    )
    envelope_digest = invocation_payload_digest(envelope)
    response_digest = invocation_payload_digest(response)
    report = build_expert_invocation_report(
        request,
        run_id=str(child.run_id),
        envelope_digest=envelope_digest,
        response_digest=response_digest,
        policy=policy,
        usage=result.usage,
        suggestion=suggestion,
    )
    return ExpertInvocationArtifacts(
        envelope,
        response,
        edit_spec,
        suggestion.payload(),
        report,
    )


def _require_generator(
    generator: StructuredGenerator[dict[str, Any]],
    policy: ExpertInvocationPolicy,
) -> None:
    descriptor = generator.descriptor
    if descriptor.capability_id != policy.capability_id:
        raise ValueError("expert invocation capability identity differs from policy")
    if descriptor.kind is not CapabilityKind.STRUCTURED_GENERATION:
        raise ValueError("expert invocation requires structured generation")
    if CapabilityFeature.STRUCTURED_OUTPUT not in descriptor.features:
        raise ValueError("expert invocation requires strict structured output")
    if CapabilityFeature.TOOL_USE in descriptor.features:
        raise ValueError("expert invocation generator must not expose tool use")
    if descriptor.data_retention is not policy.data_retention:
        raise ValueError("expert invocation data retention differs from policy")


def _build_envelope(
    root: Path,
    request: ExpertSourceEditRequest,
    policy: ExpertInvocationPolicy,
) -> dict[str, Any]:
    items = request.context_pack.items
    if len(items) != 3:
        raise ValueError("expert invocation requires the canonical three-item context")
    items_by_type = {item.item_type: item for item in items}
    if set(items_by_type) != {"repair.proposal", "debug.session", "source.rtl"}:
        raise ValueError("expert invocation context item types are invalid")
    proposal = _read_json(
        _contained(root, Path(items_by_type["repair.proposal"].uri)),
        "repair proposal",
    )
    debug = _read_json(
        _contained(root, Path(items_by_type["debug.session"].uri)),
        "debug session",
    )
    source_file = _contained(root, Path(request.source_path))
    source_text = source_file.read_text(encoding="utf-8")
    changes = proposal.get("changes")
    findings = debug.get("findings")
    observations = debug.get("observations")
    if not isinstance(changes, list) or not 1 <= len(changes) <= _MAX_CHANGES:
        raise ValueError("expert invocation repair changes exceed their bound")
    if not isinstance(findings, list) or len(findings) > _MAX_FINDINGS:
        raise ValueError("expert invocation findings exceed their bound")
    if not isinstance(observations, list) or len(observations) > _MAX_OBSERVATIONS:
        raise ValueError("expert invocation observations exceed their bound")
    selected_changes = [value for value in changes if isinstance(value, dict) and value.get("change_id") in request.change_ids]
    if [value.get("change_id") for value in selected_changes] != list(request.change_ids):
        raise ValueError("expert invocation changes differ from the reviewed request")
    excerpts = _source_excerpts(source_text, selected_changes, request.source_path, request.source_digest)
    envelope: dict[str, Any] = {
        "constraints": {
            "allowed_operations": ["replace_exact_bytes"],
            "applies_changes": False,
            "output_schema": "openrtl.expert-source-edit-output.v1",
            "provider_output_trusted": False,
            "tools": [],
        },
        "diagnosis": {
            "findings": findings,
            "observations": observations,
            "waveform_anchor": debug.get("waveform_anchor"),
        },
        "objective": "Return exact source-edit specifications for every requested repair change.",
        "proposal": {
            "changes": selected_changes,
            "failure_signature": proposal.get("failure_signature"),
            "proposal_id": request.proposal_id,
            "validation_steps": proposal.get("validation_steps"),
        },
        "request": {
            "content_digest": request.content_digest,
            "context_pack_digest": request.context_pack_digest,
            "request_id": request.request_id,
        },
        "runtime": policy.payload(),
        "schema": "openrtl.expert-source-edit-invocation.v1",
        "source": {
            "content_digest": request.source_digest,
            "excerpts": excerpts,
            "path": request.source_path,
        },
    }
    return envelope


def _source_excerpts(
    source_text: str,
    changes: list[dict[str, Any]],
    source_path: str,
    source_digest: str,
) -> list[dict[str, Any]]:
    lines = source_text.splitlines(keepends=True)
    anchors: list[tuple[int, int]] = []
    for change in changes:
        raw_anchors = change.get("source_anchors")
        if not isinstance(raw_anchors, list) or not raw_anchors:
            raise ValueError("expert invocation change lacks source anchors")
        for anchor in raw_anchors:
            if (
                not isinstance(anchor, dict)
                or anchor.get("path") != source_path
                or anchor.get("content_digest") != source_digest
                or isinstance(anchor.get("line_start"), bool)
                or not isinstance(anchor.get("line_start"), int)
                or isinstance(anchor.get("line_end"), bool)
                or not isinstance(anchor.get("line_end"), int)
            ):
                raise ValueError("expert invocation source anchor is invalid")
            start = cast(int, anchor["line_start"])
            end = cast(int, anchor["line_end"])
            if start < 1 or end < start or end > len(lines):
                raise ValueError("expert invocation source anchor is outside the source")
            anchors.append((start, end))
    unique = tuple(dict.fromkeys(anchors))
    excerpts: list[dict[str, Any]] = [
        {
            "line_end": end,
            "line_start": start,
            "text": "".join(lines[start - 1 : end]),
        }
        for start, end in unique
    ]
    excerpt_bytes = sum(
        len(cast(str, value["text"]).encode("utf-8")) for value in excerpts
    )
    if excerpt_bytes > _MAX_EXCERPT_BYTES:
        raise ValueError("expert invocation source excerpts exceed their byte bound")
    return excerpts


def _response_schema(request: ExpertSourceEditRequest) -> dict[str, Any]:
    digest_pattern = "^sha256:[0-9a-f]{64}$"
    return {
        "additionalProperties": False,
        "properties": {
            "applies_changes": {"const": False},
            "change_ids": {"const": list(request.change_ids)},
            "context_pack_digest": {"const": request.context_pack_digest},
            "context_pack_id": {"const": request.context_pack.pack_id},
            "debug_session_id": {"const": request.debug_session_id},
            "edits": {
                "items": {
                    "additionalProperties": False,
                    "properties": {
                        "change_id": {"enum": list(request.change_ids)},
                        "edit_id": {"pattern": "^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", "type": "string"},
                        "expected_before": {"maxLength": 16384, "minLength": 1, "type": "string"},
                        "operation": {"const": "replace_exact_bytes"},
                        "replacement": {"maxLength": 16384, "type": "string"},
                    },
                    "required": ["change_id", "edit_id", "expected_before", "operation", "replacement"],
                    "type": "object",
                },
                "maxItems": 32,
                "minItems": 1,
                "type": "array",
            },
            "expert_role": {"const": "diagnosis_closure_engineer"},
            "proposal_id": {"const": request.proposal_id},
            "request_digest": {"const": request.content_digest, "pattern": digest_pattern},
            "request_id": {"const": request.request_id},
            "schema": {"const": "openrtl.expert-source-edit-output.v1"},
            "source": {
                "additionalProperties": False,
                "properties": {
                    "content_digest": {"const": request.source_digest, "pattern": digest_pattern},
                    "path": {"const": request.source_path},
                },
                "required": ["content_digest", "path"],
                "type": "object",
            },
            "status": {"const": "proposed_untrusted"},
        },
        "required": [
            "applies_changes", "change_ids", "context_pack_digest", "context_pack_id",
            "debug_session_id", "edits", "expert_role", "proposal_id", "request_digest",
            "request_id", "schema", "source", "status",
        ],
        "type": "object",
    }


def _plain_object(value: JsonValue) -> dict[str, Any]:
    def plain(item: JsonValue) -> Any:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, (list, tuple)):
            return [plain(child) for child in item]
        return item

    result = plain(value)
    if not isinstance(result, dict):
        raise ValueError("expert source edit response must be an object")
    return result


def _decode_response(
    request: ExpertSourceEditRequest,
    value: JsonValue,
    max_output_bytes: int,
) -> dict[str, Any]:
    response = _plain_object(value)
    if len(_canonical_json(response)) > max_output_bytes:
        raise ValueError("expert invocation response exceeds the output byte limit")
    return validate_expert_source_edit_response(request, response)


def _contained(root: Path, relative: Path) -> Path:
    selected = (root / relative).resolve(strict=True)
    if not selected.is_relative_to(root) or not selected.is_file():
        raise ValueError("expert invocation input must be a contained regular file")
    return selected


def _read_json(selected: Path, label: str) -> dict[str, Any]:
    content = selected.read_bytes()
    if len(content) > 1024 * 1024:
        raise ValueError(f"{label} exceeds the byte limit")
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
