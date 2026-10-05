"""Fixed provider failure codes; never persist provider exception text."""

PROVIDER_FAILURE_CODES = frozenset({
    "provider_credential_unavailable",
    "provider_client_unavailable",
    "provider_authentication_rejected",
    "provider_access_denied",
    "provider_model_unavailable",
    "provider_request_rejected",
    "provider_rate_or_quota_limited",
    "provider_service_unavailable",
    "provider_connection_failed",
    "provider_response_invalid",
    "provider_timeout",
    "provider_cancelled",
    "provider_result_invalid",
})

EXPERT_OPERATION_ERROR_CODES = PROVIDER_FAILURE_CODES | {
    "expert_invocation_failed", "expert_output_invalid",
    "expert_output_readiness_invalid", "expert_output_questions_invalid",
    "expert_output_specification_invalid", "expert_output_memory_invalid",
    "expert_output_shape_invalid",
    "provider_spend_budget_exhausted", "provider_spend_uncertain",
    "expert_call_budget_exhausted",
}
