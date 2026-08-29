"""Explicitly authorized OpenAI Responses composition for expert edits."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
import json
import os
from pathlib import Path
from typing import Any, cast

from agentrig.capabilities import DataRetention
from agentrig.core import ArtifactRef, ArtifactResolver, ResolvedArtifact, RunContext
from agentrig.integrations.openai import (
    OPENAI_RESPONSES_SDK_VERSION,
    OpenAIResponsesAuthenticationSource,
    OpenAIResponsesClientFactory,
    OpenAIResponsesStructuredGenerator,
)
from openrtl.adapters.expert_invocation import (
    ExpertInvocationArtifacts,
    invoke_expert_source_edits,
)
from openrtl.adapters.expert_source_edits import load_expert_source_edit_request
from openrtl.application.expert_invocation import ExpertInvocationPolicy
from openrtl.application.provider_invocation import (
    OPENAI_RESPONSES_ADAPTER_ID,
    OPENAI_RESPONSES_RUNTIME_BINDING_ID,
    ExpertProviderExecutionReport,
    ExpertProviderInvocationApproval,
    ExpertProviderInvocationPlan,
    build_expert_provider_execution_report,
    build_expert_provider_invocation_plan,
)


@dataclass(frozen=True)
class ApprovedProviderInvocationArtifacts:
    invocation: ExpertInvocationArtifacts
    provider_report: ExpertProviderExecutionReport


class EnvironmentOpenAIAuthenticationSource:
    """Resolve one explicitly named environment variable only on demand."""

    def __init__(
        self,
        environment_name: str,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if (
            not isinstance(environment_name, str)
            or not environment_name
            or environment_name != environment_name.strip()
        ):
            raise ValueError("credential environment name is invalid")
        self._environment_name = environment_name
        self._environment = environment

    @property
    def environment_name(self) -> str:
        return self._environment_name

    def resolve_api_key(self) -> str:
        environment = os.environ if self._environment is None else self._environment
        value = environment.get(self._environment_name)
        if not isinstance(value, str) or not value:
            raise ValueError("configured provider credential is unavailable")
        return value


class RejectingArtifactResolver:
    """Fail closed because the M19 expert envelope is text-only."""

    async def resolve(self, artifact: ArtifactRef) -> ResolvedArtifact:
        raise ValueError("expert provider invocation does not allow input artifacts")


def prepare_expert_provider_invocation_plan(
    root: Path,
    *,
    request_path: Path,
    model: str,
    credential_environment: str,
    timeout_seconds: int = 120,
    max_input_bytes: int = 64 * 1024,
    max_output_bytes: int = 64 * 1024,
    max_output_tokens: int = 4096,
) -> ExpertProviderInvocationPlan:
    resolved_root = root.resolve(strict=True)
    request = load_expert_source_edit_request(resolved_root, request_path)
    policy = ExpertInvocationPolicy(
        OPENAI_RESPONSES_RUNTIME_BINDING_ID,
        OPENAI_RESPONSES_ADAPTER_ID,
        "openai",
        model,
        DataRetention.PROVIDER_MANAGED,
        timeout_seconds,
        max_input_bytes,
        max_output_bytes,
        max_output_tokens,
    )
    return build_expert_provider_invocation_plan(
        request,
        policy=policy,
        adapter_version=OPENAI_RESPONSES_SDK_VERSION,
        credential_environment=credential_environment,
    )


def load_expert_provider_invocation_plan(
    root: Path,
    plan_path: Path,
) -> ExpertProviderInvocationPlan:
    resolved_root = root.resolve(strict=True)
    selected = _contained(resolved_root, plan_path)
    payload = _read_json(selected, "expert provider invocation plan")
    expected_keys = {
        "adapter",
        "authorization",
        "constraints",
        "content_digest",
        "plan_id",
        "request",
        "runtime",
        "schema",
        "status",
    }
    if set(payload) != expected_keys:
        raise ValueError("expert provider invocation plan fields are invalid")
    adapter = _object(payload["adapter"], "expert provider invocation adapter")
    authorization = _object(
        payload["authorization"],
        "expert provider invocation authorization",
    )
    request = _object(payload["request"], "expert provider invocation request")
    runtime = _object(payload["runtime"], "expert provider invocation runtime")
    if set(runtime) != {
        "capability_id",
        "data_retention",
        "max_input_bytes",
        "max_output_bytes",
        "max_output_tokens",
        "max_turns",
        "model",
        "provider",
        "runtime_binding_id",
        "timeout_seconds",
        "tool_ids",
    }:
        raise ValueError("expert provider invocation runtime fields are invalid")
    if runtime.get("max_turns") != 1 or runtime.get("tool_ids") != []:
        raise ValueError("expert provider invocation runtime authority is invalid")
    try:
        retention = DataRetention(cast(str, runtime["data_retention"]))
        policy = ExpertInvocationPolicy(
            cast(str, runtime["runtime_binding_id"]),
            cast(str, runtime["capability_id"]),
            cast(str, runtime["provider"]),
            cast(str, runtime["model"]),
            retention,
            cast(int, runtime["timeout_seconds"]),
            cast(int, runtime["max_input_bytes"]),
            cast(int, runtime["max_output_bytes"]),
            cast(int, runtime["max_output_tokens"]),
        )
        plan = ExpertProviderInvocationPlan(
            cast(str, payload["plan_id"]),
            cast(str, request["request_id"]),
            cast(str, request["content_digest"]),
            policy,
            cast(str, adapter["version"]),
            cast(str, authorization["credential_environment"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("expert provider invocation plan values are invalid") from None
    if plan.payload() != payload:
        raise ValueError("expert provider invocation plan is not in canonical form")
    return plan


async def invoke_approved_openai_expert_source_edits(
    root: Path,
    *,
    request_path: Path,
    proposal_path: Path,
    debug_session_path: Path,
    source_path: Path,
    plan_path: Path,
    approval: ExpertProviderInvocationApproval,
    context: RunContext,
    authentication_source: OpenAIResponsesAuthenticationSource,
    client_factory: OpenAIResponsesClientFactory | None = None,
) -> ApprovedProviderInvocationArtifacts:
    """Run one approved provider call; never qualify or apply its output."""

    resolved_root = root.resolve(strict=True)
    plan = load_expert_provider_invocation_plan(resolved_root, plan_path)
    approval.require_matches(plan)
    request = load_expert_source_edit_request(resolved_root, request_path)
    if request.request_id != plan.request_id or request.content_digest != plan.request_digest:
        raise ValueError("provider invocation plan does not match the reviewed request")
    if not isinstance(authentication_source, OpenAIResponsesAuthenticationSource):
        raise TypeError("OpenAI authentication source is invalid")
    selected_factory = (
        client_factory
        if client_factory is not None
        else _sdk_client_factory(authentication_source)
    )
    generator = OpenAIResponsesStructuredGenerator[dict[str, Any]](
        client_factory=selected_factory,
        artifact_resolver=cast(ArtifactResolver, RejectingArtifactResolver()),
        model=plan.policy.model,
    )
    invocation = await invoke_expert_source_edits(
        resolved_root,
        request_path=request_path,
        proposal_path=proposal_path,
        debug_session_path=debug_session_path,
        source_path=source_path,
        generator=generator,
        policy=plan.policy,
        context=context,
    )
    provider_report = build_expert_provider_execution_report(
        plan,
        approval,
        invocation_id=invocation.report.invocation_id,
        invocation_report_payload=invocation.report.payload(),
    )
    return ApprovedProviderInvocationArtifacts(invocation, provider_report)


def _sdk_client_factory(
    authentication_source: OpenAIResponsesAuthenticationSource,
) -> OpenAIResponsesClientFactory:
    """Load the optional SDK bridge only after local authorization preflight."""

    try:
        module = import_module("agentrig.integrations.openai.responses_sdk")
        factory_type = cast(Any, module).OpenAIResponsesSdkClientFactory
        factory = factory_type(authentication_source=authentication_source)
    except (AttributeError, ImportError, TypeError):
        raise RuntimeError(
            "OpenAI provider invocation requires AgentRig's pinned OpenAI SDK extra"
        ) from None
    if not isinstance(factory, OpenAIResponsesClientFactory):
        raise RuntimeError("OpenAI provider client factory is invalid")
    return factory


def _contained(root: Path, relative: Path) -> Path:
    selected = (root / relative).resolve(strict=True)
    if not selected.is_relative_to(root) or not selected.is_file():
        raise ValueError("provider invocation input must be a contained regular file")
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


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)
