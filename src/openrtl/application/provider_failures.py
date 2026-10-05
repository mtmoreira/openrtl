"""Map only typed AgentRig failures to bounded OpenRTL support categories."""

from __future__ import annotations

import asyncio

from agentrig.core.errors import AgentRigError, FailureKind
from agentrig.core.deadline import DeadlineExceeded
from agentrig.core.cancellation import RunCancelled


def classify_provider_failure(error: BaseException) -> str:
    if isinstance(error, (asyncio.TimeoutError, DeadlineExceeded)):
        return "provider_timeout"
    if isinstance(error, RunCancelled):
        return "provider_cancelled"
    if isinstance(error, AgentRigError):
        failure = error.failure
        if failure.kind is FailureKind.DEADLINE_EXCEEDED:
            return "provider_timeout"
        if failure.kind is FailureKind.CANCELLED:
            return "provider_cancelled"
        if failure.code == "openai.responses.authentication_resolution_failed":
            return "provider_credential_unavailable"
        if failure.code == "openai.responses.client_creation_failed":
            return "provider_client_unavailable"
        if failure.code in ("openai.responses.invalid_response", "openai.responses.invalid_output"):
            return "provider_response_invalid"
        if failure.code == "openai.responses.request_failed":
            status = failure.metadata.get("status_code")
            categories = {
                "400": "provider_request_rejected",
                "401": "provider_authentication_rejected",
                "402": "provider_rate_or_quota_limited",
                "403": "provider_access_denied",
                "404": "provider_model_unavailable",
                "408": "provider_timeout",
                "409": "provider_request_rejected",
                "422": "provider_request_rejected",
                "429": "provider_rate_or_quota_limited",
            }
            if status in categories:
                return categories[status]
            if type(status) is str and len(status) == 3 and status.isdecimal() and 500 <= int(status) <= 599:
                return "provider_service_unavailable"
            return "provider_connection_failed"
        if failure.code == "ollama.client_creation_failed":
            return "provider_client_unavailable"
        if failure.code in ("ollama.invalid_response", "ollama.invalid_output"):
            return "provider_response_invalid"
        if failure.code in ("ollama.transport_failed", "ollama.client_close_failed"):
            return "provider_connection_failed"
        if failure.code == "ollama.request_failed":
            status = failure.metadata.get("status_code")
            categories = {"400": "provider_request_rejected", "404": "provider_model_unavailable",
                          "408": "provider_timeout", "422": "provider_request_rejected",
                          "429": "provider_rate_or_quota_limited"}
            if status in categories:
                return categories[status]
            if type(status) is str and len(status) == 3 and status.isdecimal() and 500 <= int(status) <= 599:
                return "provider_service_unavailable"
            return "provider_connection_failed"
    if type(error) is ValueError and str(error) in {
        "expert_output_exceeds_bound", "expert_reply_invalid", "expert_usage_invalid",
        "provider_usage_unavailable", "provider_spend_reserve_exceeded",
        "provider_result_identity_invalid", "expert_result_identity_or_finish_invalid",
    }:
        return "provider_result_invalid"
    return "expert_invocation_failed"
