"""Bounded, non-applying expert invocation contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from agentrig.capabilities import DataRetention, GenerationUsage
from openrtl.application.expert_edits import ExpertSourceEditReport, ExpertSourceEditRequest
from openrtl.domain._validation import digest, identifier, nonempty


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def invocation_payload_digest(payload: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


@dataclass(frozen=True)
class ExpertInvocationPolicy:
    """Exact runtime identity and resource bounds selected by the caller."""

    runtime_binding_id: str
    capability_id: str
    provider: str
    model: str
    data_retention: DataRetention
    timeout_seconds: int = 120
    max_input_bytes: int = 64 * 1024
    max_output_bytes: int = 64 * 1024
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "runtime_binding_id",
            identifier(self.runtime_binding_id, "runtime_binding_id"),
        )
        object.__setattr__(self, "capability_id", nonempty(self.capability_id, "capability_id"))
        object.__setattr__(self, "provider", identifier(self.provider, "provider"))
        object.__setattr__(self, "model", nonempty(self.model, "model"))
        if not isinstance(self.data_retention, DataRetention):
            raise TypeError("expert invocation data_retention must be a DataRetention")
        for field_name, upper_bound in (
            ("timeout_seconds", 600),
            ("max_input_bytes", 256 * 1024),
            ("max_output_bytes", 1024 * 1024),
            ("max_output_tokens", 8192),
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper_bound:
                raise ValueError(f"expert invocation {field_name} is outside its allowed range")

    def payload(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "data_retention": self.data_retention.value,
            "max_input_bytes": self.max_input_bytes,
            "max_output_bytes": self.max_output_bytes,
            "max_output_tokens": self.max_output_tokens,
            "max_turns": 1,
            "model": self.model,
            "provider": self.provider,
            "runtime_binding_id": self.runtime_binding_id,
            "timeout_seconds": self.timeout_seconds,
            "tool_ids": [],
        }


@dataclass(frozen=True)
class ExpertInvocationReport:
    invocation_id: str
    run_id: str
    request_id: str
    request_digest: str
    envelope_digest: str
    response_digest: str
    policy: ExpertInvocationPolicy
    usage: GenerationUsage
    suggestion_id: str
    suggestion_digest: str

    def __post_init__(self) -> None:
        for field_name in ("invocation_id", "request_id", "suggestion_id"):
            object.__setattr__(self, field_name, identifier(getattr(self, field_name), field_name))
        object.__setattr__(self, "run_id", nonempty(self.run_id, "run_id"))
        for field_name in (
            "request_digest",
            "envelope_digest",
            "response_digest",
            "suggestion_digest",
        ):
            object.__setattr__(self, field_name, digest(getattr(self, field_name), field_name))
        if not isinstance(self.policy, ExpertInvocationPolicy):
            raise TypeError("expert invocation report policy is invalid")
        if not isinstance(self.usage, GenerationUsage):
            raise TypeError("expert invocation report usage is invalid")

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "envelope_digest": self.envelope_digest,
            "finish_reason": "completed",
            "invocation_id": self.invocation_id,
            "next_gate": {
                "operation": "repair draft-source-edits",
                "qualification_required": True,
            },
            "provider_output_trusted": False,
            "request": {
                "content_digest": self.request_digest,
                "request_id": self.request_id,
            },
            "response_digest": self.response_digest,
            "run_id": self.run_id,
            "runtime": self.policy.payload(),
            "schema": "openrtl.expert-source-edit-invocation-report.v1",
            "status": "awaiting_qualification",
            "suggestion": {
                "content_digest": self.suggestion_digest,
                "suggestion_id": self.suggestion_id,
            },
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
                "total_tokens": self.usage.total_tokens,
            },
        }


def build_expert_invocation_report(
    request: ExpertSourceEditRequest,
    *,
    run_id: str,
    envelope_digest: str,
    response_digest: str,
    policy: ExpertInvocationPolicy,
    usage: GenerationUsage,
    suggestion: ExpertSourceEditReport,
) -> ExpertInvocationReport:
    seed = {
        "envelope_digest": envelope_digest,
        "policy": policy.payload(),
        "request_digest": request.content_digest,
        "response_digest": response_digest,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return ExpertInvocationReport(
        f"repair.expert-invocation.{token}",
        run_id,
        request.request_id,
        request.content_digest,
        envelope_digest,
        response_digest,
        policy,
        usage,
        suggestion.suggestion_id,
        invocation_payload_digest(suggestion.payload()),
    )
