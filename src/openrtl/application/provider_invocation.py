"""Explicit authorization contracts for one live expert provider call."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from agentrig.capabilities import DataRetention
from openrtl.application.expert_edits import ExpertSourceEditRequest
from openrtl.application.expert_invocation import ExpertInvocationPolicy
from openrtl.domain._validation import digest, identifier, nonempty


OPENAI_RESPONSES_ADAPTER_ID = "openai.responses.structured_generation"
OPENAI_RESPONSES_RUNTIME_BINDING_ID = "runtime.openai.responses.expert-edits"
_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def provider_invocation_digest(payload: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(payload)).hexdigest()}"


@dataclass(frozen=True)
class ExpertProviderInvocationPlan:
    """Reviewed inputs and runtime identity awaiting an exact live opt-in."""

    plan_id: str
    request_id: str
    request_digest: str
    policy: ExpertInvocationPolicy
    adapter_version: str
    credential_environment: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", identifier(self.plan_id, "plan_id"))
        object.__setattr__(self, "request_id", identifier(self.request_id, "request_id"))
        object.__setattr__(
            self,
            "request_digest",
            digest(self.request_digest, "request_digest"),
        )
        if not isinstance(self.policy, ExpertInvocationPolicy):
            raise TypeError("expert provider invocation policy is invalid")
        if self.policy.runtime_binding_id != OPENAI_RESPONSES_RUNTIME_BINDING_ID:
            raise ValueError("expert provider invocation runtime binding is invalid")
        if self.policy.capability_id != OPENAI_RESPONSES_ADAPTER_ID:
            raise ValueError("expert provider invocation capability is invalid")
        if self.policy.provider != "openai":
            raise ValueError("expert provider invocation provider is invalid")
        if self.policy.data_retention is not DataRetention.PROVIDER_MANAGED:
            raise ValueError("expert provider invocation retention policy is invalid")
        object.__setattr__(
            self,
            "adapter_version",
            nonempty(self.adapter_version, "adapter_version"),
        )
        if _ENVIRONMENT_NAME.fullmatch(self.credential_environment) is None:
            raise ValueError("credential environment name is invalid")

    def _content(self) -> dict[str, Any]:
        return {
            "adapter": {
                "adapter_id": OPENAI_RESPONSES_ADAPTER_ID,
                "version": self.adapter_version,
            },
            "authorization": {
                "credential_environment": self.credential_environment,
                "credential_resolution": "deferred_until_client_creation",
                "explicit_digest_required": True,
                "max_provider_calls": 1,
                "network_access_required": True,
            },
            "constraints": {
                "applies_changes": False,
                "provider_output_trusted": False,
                "tools": [],
            },
            "plan_id": self.plan_id,
            "request": {
                "content_digest": self.request_digest,
                "request_id": self.request_id,
            },
            "runtime": self.policy.payload(),
            "schema": "openrtl.expert-provider-invocation-plan.v1",
            "status": "awaiting_explicit_approval",
        }

    @property
    def content_digest(self) -> str:
        return provider_invocation_digest(self._content())

    def payload(self) -> dict[str, Any]:
        return {**self._content(), "content_digest": self.content_digest}


@dataclass(frozen=True)
class ExpertProviderInvocationApproval:
    """One exact, caller-supplied authorization for a prepared plan."""

    plan_id: str
    plan_digest: str
    review_note: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", identifier(self.plan_id, "plan_id"))
        object.__setattr__(self, "plan_digest", digest(self.plan_digest, "plan_digest"))
        note = nonempty(self.review_note, "review_note")
        if len(note) > 512:
            raise ValueError("provider invocation review note exceeds its bound")
        object.__setattr__(self, "review_note", note)

    def require_matches(self, plan: ExpertProviderInvocationPlan) -> None:
        if self.plan_id != plan.plan_id or self.plan_digest != plan.content_digest:
            raise ValueError("provider invocation approval does not match the exact plan")


@dataclass(frozen=True)
class ExpertProviderExecutionReport:
    """Value-safe lifecycle evidence for the authorized provider call."""

    plan_id: str
    plan_digest: str
    invocation_id: str
    invocation_report_digest: str
    review_note_digest: str

    def __post_init__(self) -> None:
        for field_name in ("plan_id", "invocation_id"):
            object.__setattr__(
                self,
                field_name,
                identifier(getattr(self, field_name), field_name),
            )
        for field_name in (
            "plan_digest",
            "invocation_report_digest",
            "review_note_digest",
        ):
            object.__setattr__(
                self,
                field_name,
                digest(getattr(self, field_name), field_name),
            )

    def payload(self) -> dict[str, Any]:
        return {
            "applies_changes": False,
            "authorization": {
                "credential_value_persisted": False,
                "explicit_plan_digest_matched": True,
                "provider_call_count": 1,
                "review_note_digest": self.review_note_digest,
            },
            "invocation": {
                "content_digest": self.invocation_report_digest,
                "invocation_id": self.invocation_id,
            },
            "plan": {
                "content_digest": self.plan_digest,
                "plan_id": self.plan_id,
            },
            "provider_output_trusted": False,
            "schema": "openrtl.expert-provider-execution-report.v1",
            "status": "awaiting_qualification",
        }


def build_expert_provider_invocation_plan(
    request: ExpertSourceEditRequest,
    *,
    policy: ExpertInvocationPolicy,
    adapter_version: str,
    credential_environment: str,
) -> ExpertProviderInvocationPlan:
    seed = {
        "adapter_version": adapter_version,
        "credential_environment": credential_environment,
        "policy": policy.payload(),
        "request_digest": request.content_digest,
    }
    token = hashlib.sha256(_canonical_json(seed)).hexdigest()[:20]
    return ExpertProviderInvocationPlan(
        f"repair.provider-plan.{token}",
        request.request_id,
        request.content_digest,
        policy,
        adapter_version,
        credential_environment,
    )


def build_expert_provider_execution_report(
    plan: ExpertProviderInvocationPlan,
    approval: ExpertProviderInvocationApproval,
    *,
    invocation_id: str,
    invocation_report_payload: object,
) -> ExpertProviderExecutionReport:
    approval.require_matches(plan)
    return ExpertProviderExecutionReport(
        plan.plan_id,
        plan.content_digest,
        invocation_id,
        provider_invocation_digest(invocation_report_payload),
        provider_invocation_digest({"review_note": approval.review_note}),
    )
