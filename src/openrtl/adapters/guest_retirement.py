"""Backend-neutral guest-service retirement composition and recovery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from openrtl.adapters.managed_backend import _context, _strict_object
from openrtl.domain.design_session import JsonObject, require
from openrtl.onboarding import _open_state

if TYPE_CHECKING:
    from agentrig.integrations.guest_retirement import GuestServiceRetirementAdapter
    from agentrig.integrations.guest_workspace import GuestWorkspaceEndpoint
    from agentrig.integrations.lima_retirement import (
        LimaGenerationFence,
        LimaRetirementObserver,
        LimaSocketRetirer,
    )


class RetirementConfigurationValidator(Protocol):
    def __call__(
        self, value: JsonObject, endpoint: GuestWorkspaceEndpoint
    ) -> JsonObject: ...


class RetirementAdapterFactory(Protocol):
    """Construct from reviewed local bytes only; runtime contact begins in the adapter."""

    def __call__(
        self, configuration: JsonObject, endpoint: GuestWorkspaceEndpoint, state: Path
    ) -> GuestServiceRetirementAdapter: ...


@dataclass(frozen=True, slots=True)
class RetirementBackendDefinition:
    """Pure configuration validator plus an optional effectful adapter factory."""

    validate: RetirementConfigurationValidator
    create: RetirementAdapterFactory | None = None


def lima_retirement_registration(
    *,
    fence_generation: LimaGenerationFence | None = None,
    retire_socket: LimaSocketRetirer | None = None,
    observe_retired: LimaRetirementObserver | None = None,
) -> RetirementBackendDefinition:
    """Build a Lima registration; all three live seams are required for effects."""
    from openrtl.adapters.lima_managed import (
        bind_lima_retirement_adapter,
        validate_lima_retirement_configuration,
    )

    live = (fence_generation, retire_socket, observe_retired)
    require(
        all(value is None for value in live) or all(value is not None for value in live),
        "runtime_retirement_configuration_invalid",
    )
    factory: RetirementAdapterFactory | None = None
    if all(value is not None for value in live):
        assert fence_generation is not None
        assert retire_socket is not None
        assert observe_retired is not None

        def create(
            configuration: JsonObject, endpoint: GuestWorkspaceEndpoint, state: Path
        ) -> GuestServiceRetirementAdapter:
            return bind_lima_retirement_adapter(
                configuration,
                endpoint,
                state,
                fence_generation=fence_generation,
                retire_socket=retire_socket,
                observe_retired=observe_retired,
            )

        factory = create
    return RetirementBackendDefinition(
        validate=validate_lima_retirement_configuration,
        create=factory,
    )


def retirement_registry() -> Mapping[str, RetirementBackendDefinition]:
    """Return built-in backends; callers may replace the whole mapping."""
    try:
        from openrtl.adapters.lima_retirement_ports import lima_cli_retirement_registration

        return {"lima-vz-managed": lima_cli_retirement_registration()}
    except ImportError:
        raise ValueError("runtime_retirement_sdk_candidate_required") from None


def _endpoint(value: object) -> GuestWorkspaceEndpoint:
    try:
        from agentrig.integrations.guest_workspace import GuestWorkspaceEndpoint
    except ImportError:
        raise ValueError("runtime_retirement_sdk_candidate_required") from None

    try:
        require(
            type(value) is dict
            and set(value) == {"root", "instance_id", "device", "inode", "uid"},
            "runtime_retirement_configuration_invalid",
        )
        item = cast(dict[str, object], value)
        root = item["root"]
        require(isinstance(root, str), "runtime_retirement_configuration_invalid")
        return GuestWorkspaceEndpoint(
            root=Path(cast(str, root)),
            instance_id=cast(str, item["instance_id"]),
            device=cast(int, item["device"]),
            inode=cast(int, item["inode"]),
            uid=cast(int, item["uid"]),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("runtime_retirement_configuration_invalid") from None


def _configuration(
    backend_id: str,
    configuration_json: str,
    registrations: Mapping[str, RetirementBackendDefinition] | None,
) -> tuple[RetirementBackendDefinition, GuestWorkspaceEndpoint, JsonObject, JsonObject]:
    try:
        from agentrig.capabilities.local_backend import backend_identifier
    except ImportError:
        raise ValueError("runtime_retirement_sdk_candidate_required") from None

    try:
        backend_identifier(backend_id)
        selected = retirement_registry() if registrations is None else registrations
        require(backend_id in selected, "runtime_retirement_backend_unavailable")
        value = _strict_object(configuration_json)
        require(
            set(value) == {"endpoint", "backend_configuration"}
            and type(value["backend_configuration"]) is dict,
            "runtime_retirement_configuration_invalid",
        )
        endpoint = _endpoint(value["endpoint"])
        backend_configuration = cast(JsonObject, value["backend_configuration"])
        definition = selected[backend_id]
        validated = definition.validate(backend_configuration, endpoint)
        require(type(validated) is dict, "runtime_retirement_configuration_invalid")
        canonical: JsonObject = {
            "endpoint": {
                "root": str(endpoint.root),
                "instance_id": endpoint.instance_id,
                "device": endpoint.device,
                "inode": endpoint.inode,
                "uid": endpoint.uid,
            },
            "backend_configuration": validated,
        }
        return definition, endpoint, validated, canonical
    except ValueError as error:
        code = str(error)
        if code in {
            "runtime_retirement_backend_unavailable",
            "runtime_retirement_configuration_invalid",
            "runtime_retirement_sdk_candidate_required",
        }:
            raise
        raise ValueError("runtime_retirement_configuration_invalid") from None
    except Exception:
        raise ValueError("runtime_retirement_configuration_invalid") from None


def _action(value: str) -> Literal["retire", "inspect"]:
    require(value in {"retire", "inspect"}, "runtime_retirement_configuration_invalid")
    return cast(Literal["retire", "inspect"], value)


def _effects(action: Literal["retire", "inspect"]) -> frozenset[str]:
    effects = {"local_write", "runtime_contact"}
    if action == "retire":
        effects.add("guest_socket_remove")
    return frozenset(effects)


def plan_guest_retirement(
    backend_id: str,
    action: str,
    configuration_json: str,
    operation_id: str,
    registrations: Mapping[str, RetirementBackendDefinition] | None = None,
) -> JsonObject:
    """Create a pure plan; no state, runtime or adapter factory is contacted."""
    try:
        from agentrig.capabilities.local_backend import backend_digest
        from agentrig.integrations.guest_retirement import guest_retirement_operation_digest
    except ImportError:
        raise ValueError("runtime_retirement_sdk_candidate_required") from None

    try:
        selected_action = _action(action)
        definition, endpoint, _, canonical = _configuration(
            backend_id, configuration_json, registrations
        )
        del definition
        approval_digest = guest_retirement_operation_digest(
            endpoint, operation_id, selected_action
        )
        required = _effects(selected_action)
        material: JsonObject = {
            "schema": "openrtl.guest-retirement-plan.v1",
            "backend": backend_id,
            "action": selected_action,
            "operation_id": operation_id,
            "configuration": canonical,
            "retirement_approval_digest": approval_digest,
            "effects": sorted(required),
        }
        return {
            "schema": "openrtl.guest-retirement-review.v1",
            "plan": material,
            "plan_digest": backend_digest(material),
            "required_effects": sorted(required),
            "runtime_contact": False,
            "execution_authorized": False,
            "ready_for_simulation": False,
            "notice": "Review only. Saved plans and journals never restore retirement authority.",
            "m46": "pending",
            "m47": "pending",
        }
    except ValueError as error:
        code = str(error)
        if code.startswith("runtime_retirement_"):
            raise
        raise ValueError("runtime_retirement_configuration_invalid") from None
    except Exception:
        raise ValueError("runtime_retirement_configuration_invalid") from None


def _public_failure(code: str) -> ValueError:
    mapped = {
        "backend_authority_required": "runtime_retirement_authority_required",
        "backend_plan_changed": "runtime_retirement_plan_changed",
        "backend_plan_blocked": "runtime_retirement_backend_blocked",
        "backend_reconciliation_required": "runtime_retirement_reconciliation_required",
        "backend_journal_invalid": "runtime_retirement_journal_invalid",
        "backend_journal_busy": "runtime_retirement_journal_busy",
        "backend_operation_reused": "runtime_retirement_operation_reused",
    }
    return ValueError(mapped.get(code, "runtime_retirement_execution_failed"))


def apply_guest_retirement(
    backend_id: str,
    action: str,
    configuration_json: str,
    operation_id: str,
    expected_plan_digest: str,
    granted_effects: frozenset[str],
    state: Path,
    *,
    timeout_seconds: int = 120,
    registrations: Mapping[str, RetirementBackendDefinition] | None = None,
) -> JsonObject:
    """Apply retirement or observation-only recovery with invocation-local grants."""
    try:
        from agentrig.capabilities.local_backend import BackendFailure
        from agentrig.integrations.backend_journal import LocalBackendJournal
        from agentrig.integrations.guest_retirement import (
            JournaledGuestServiceRetirement,
            guest_retirement_operation_digest,
        )
    except ImportError:
        raise ValueError("runtime_retirement_sdk_candidate_required") from None

    try:
        selected_action = _action(action)
        plan = plan_guest_retirement(
            backend_id, action, configuration_json, operation_id, registrations
        )
        require(plan["plan_digest"] == expected_plan_digest, "runtime_retirement_plan_changed")
        required = frozenset(cast(list[str], plan["required_effects"]))
        require(granted_effects == required, "runtime_retirement_authority_required")
        require(
            type(timeout_seconds) is int and 1 <= timeout_seconds <= 300,
            "runtime_retirement_configuration_invalid",
        )
        definition, endpoint, backend_configuration, _ = _configuration(
            backend_id, configuration_json, registrations
        )
        factory = definition.create
        if factory is None:
            raise ValueError("runtime_retirement_backend_blocked")
        selected_state = state.absolute()
        require(
            selected_state != Path(selected_state.anchor) and ".." not in selected_state.parts,
            "runtime_retirement_configuration_invalid",
        )
        context = _context(timeout_seconds)
        adapter = factory(backend_configuration, endpoint, selected_state)
        operations = selected_state / "guest-retirements" / "backend-operations"
        descriptor = _open_state(operations, create=True)
        os.close(descriptor)
        result = JournaledGuestServiceRetirement(
            journal=LocalBackendJournal(operations), adapter=adapter
        ).apply(
            endpoint,
            operation_id,
            guest_retirement_operation_digest(endpoint, operation_id, selected_action),
            context,
            journal_write_authorized=True,
            action=selected_action,
        )
        return {
            "schema": "openrtl.guest-retirement-result.v1",
            "backend": backend_id,
            "operation_id": result.operation_id,
            "plan_digest": expected_plan_digest,
            "status": result.status,
            "observation": result.observation,
            "reconciled": selected_action == "inspect",
            "runtime_contact": True,
            "execution_authorized": False,
            "ready_for_simulation": False,
            "workspace_retained": True,
            "m46": "pending",
            "m47": "pending",
        }
    except ValueError as error:
        code = str(error)
        if code.startswith("runtime_retirement_"):
            raise
        raise ValueError("runtime_retirement_execution_failed") from None
    except BackendFailure as error:
        raise _public_failure(error.code) from None
    except Exception:
        raise ValueError("runtime_retirement_execution_failed") from None


def guest_retirement_status(state: Path) -> JsonObject:
    """Read the local retirement journal without contacting a backend."""
    from openrtl.adapters.backend_setup import backend_operation_status

    result = backend_operation_status(state.absolute() / "guest-retirements")
    result["schema"] = "openrtl.guest-retirement-status.v1"
    result["notice"] = (
        "Local retirement journal only. Uncertainty requires a fresh authorized "
        "generation-fenced inspection."
    )
    return result
