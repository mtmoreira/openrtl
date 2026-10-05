"""Application-owned composition for replaceable managed local backends."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from openrtl.domain.design_session import JsonObject, require
from openrtl.onboarding import _open_state

if TYPE_CHECKING:
    from agentrig.capabilities.local_backend import (
        BackendAuthority,
        BackendDescriptor,
        BackendPlan,
        BackendRequest,
        BackendResult,
    )
    from agentrig.core import RunContext


class ManagedLifecycleBackend(Protocol):
    """Effectful backend seam; implementations own their reconciliation rules."""

    @property
    def descriptor(self) -> BackendDescriptor: ...

    def plan(self, request: BackendRequest) -> BackendPlan: ...

    async def apply(
        self, plan: BackendPlan, authority: BackendAuthority, context: RunContext
    ) -> BackendResult: ...

    async def reconcile(
        self, inspection: BackendPlan, authority: BackendAuthority, context: RunContext
    ) -> BackendResult: ...


ManagedBackendFactory = Callable[[Path], ManagedLifecycleBackend]

_PUBLIC_ERRORS = {
    "runtime_backend_artifacts_invalid",
    "runtime_backend_authority_required",
    "runtime_backend_configuration_invalid",
    "runtime_backend_execution_failed",
    "runtime_backend_journal_busy",
    "runtime_backend_journal_invalid",
    "runtime_backend_plan_changed",
    "runtime_backend_sdk_candidate_required",
    "runtime_backend_unavailable",
}


def _public_error(error: ValueError, fallback: str) -> ValueError:
    code = str(error)
    return ValueError(code if code in _PUBLIC_ERRORS else fallback)


def _strict_object(payload: str) -> JsonObject:
    require(len(payload.encode("utf-8")) <= 65536, "runtime_backend_configuration_invalid")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, "runtime_backend_configuration_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda _: require(False, "runtime_backend_configuration_invalid"),
        )
        require(type(value) is dict, "runtime_backend_configuration_invalid")
        return cast(JsonObject, value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise ValueError("runtime_backend_configuration_invalid") from None


def managed_backend_registry() -> Mapping[str, ManagedBackendFactory]:
    from openrtl.adapters.lima_managed import lima_managed_backend

    return {"lima-vz-managed": lima_managed_backend}


def _resolve_backend(
    backend_id: str,
    state: Path,
    registrations: Mapping[str, ManagedBackendFactory] | None,
) -> ManagedLifecycleBackend:
    selected = managed_backend_registry() if registrations is None else registrations
    require(backend_id in selected, "runtime_backend_unavailable")
    try:
        return selected[backend_id](state)
    except ValueError as error:
        raise _public_error(error, "runtime_backend_configuration_invalid") from None
    except Exception:
        raise ValueError("runtime_backend_configuration_invalid") from None


def _request(action: str, operation_id: str, configuration_json: str) -> BackendRequest:
    try:
        from agentrig.capabilities.local_backend import BackendAction, BackendRequest
    except ImportError:
        raise ValueError("runtime_backend_sdk_candidate_required") from None
    try:
        return BackendRequest(
            action=BackendAction(action),
            operation_id=operation_id,
            configuration=_strict_object(configuration_json),
        )
    except (TypeError, ValueError):
        raise ValueError("runtime_backend_configuration_invalid") from None


def plan_managed_backend(
    backend_id: str,
    action: str,
    configuration_json: str,
    operation_id: str,
    state: Path,
    registrations: Mapping[str, ManagedBackendFactory] | None = None,
) -> JsonObject:
    """Pure backend plan; constructing a registered adapter performs no I/O."""
    try:
        from agentrig.core._json import thaw_json_value

        backend = _resolve_backend(backend_id, state, registrations)
        plan = backend.plan(_request(action, operation_id, configuration_json))
        value = cast(JsonObject, thaw_json_value(plan.to_json()))
        return {
            "schema": "openrtl.managed-backend-plan.v1",
            "backend": backend_id,
            "plan": value,
            "plan_digest": plan.digest,
            "required_effects": sorted(effect.value for effect in plan.effects),
            "runtime_contact": False,
            "execution_authorized": False,
            "ready_for_simulation": False,
            "m46": "pending",
            "m47": "pending",
        }
    except ValueError as error:
        raise _public_error(error, "runtime_backend_configuration_invalid") from None
    except Exception as error:
        code = getattr(error, "code", None)
        if isinstance(code, str):
            raise ValueError("runtime_" + code) from None
        raise ValueError("runtime_backend_configuration_invalid") from None


def _context(timeout_seconds: int) -> RunContext:
    from agentrig.core import CancellationSource, Deadline, RunContext, RunId
    from agentrig.core.clock import SystemClock
    from agentrig.core.identity import Uuid4IdGenerator

    require(
        type(timeout_seconds) is int and 1 <= timeout_seconds <= 300,
        "runtime_backend_configuration_invalid",
    )
    clock = SystemClock()
    return RunContext.create_root(
        clock=clock,
        id_generator=Uuid4IdGenerator(RunId),
        cancellation=CancellationSource().token,
        deadline=Deadline.after(timeout_seconds, clock),
    )


@contextmanager
def managed_backend_fence(state: Path, context: RunContext) -> Iterator[None]:
    """Exclude cooperating lifecycle changes while retirement observes a generation."""
    import fcntl

    from agentrig.capabilities import BackendFailure
    from agentrig.core import DeadlineExceeded, RunCancelled

    directory: int | None = None
    lock: int | None = None
    try:
        context.cancellation.raise_if_cancelled()
        if context.deadline is not None:
            context.deadline.raise_if_expired(context.clock)
        directory = _open_state(state.absolute() / "backend-fence", create=True)
        lock = os.open(
            "operation.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=directory,
        )
        info = os.fstat(lock)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise BackendFailure("backend_journal_invalid")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackendFailure("backend_journal_busy") from None
        yield None
        context.cancellation.raise_if_cancelled()
        if context.deadline is not None:
            context.deadline.raise_if_expired(context.clock)
    except (BackendFailure, DeadlineExceeded, RunCancelled):
        raise
    except Exception:
        raise BackendFailure("backend_journal_invalid") from None
    finally:
        if lock is not None:
            os.close(lock)
        if directory is not None:
            os.close(directory)


async def apply_managed_backend(
    backend_id: str,
    action: str,
    configuration_json: str,
    operation_id: str,
    expected_plan_digest: str,
    granted_effects: frozenset[str],
    state: Path,
    *,
    reconcile: bool = False,
    timeout_seconds: int = 240,
    registrations: Mapping[str, ManagedBackendFactory] | None = None,
    context: RunContext | None = None,
) -> JsonObject:
    """Apply one exact plan with invocation-local effects; saved state grants nothing."""
    try:
        from agentrig.capabilities.local_backend import BackendAction, BackendAuthority, BackendEffect

        backend = _resolve_backend(backend_id, state, registrations)
        plan = backend.plan(_request(action, operation_id, configuration_json))
        require(plan.digest == expected_plan_digest, "runtime_backend_plan_changed")
        effects = frozenset(BackendEffect(value) for value in granted_effects)
        require(effects == plan.effects, "runtime_backend_authority_required")
        require(
            not reconcile or plan.request.action is BackendAction.INSPECT,
            "runtime_backend_plan_changed",
        )
        directory = _open_state(state.absolute() / "backend-operations", create=True)
        os.close(directory)
        authority = BackendAuthority(
            plan_digest=plan.digest,
            operation_id=plan.request.operation_id,
            effects=effects,
        )
        selected_context = _context(timeout_seconds) if context is None else context
        with managed_backend_fence(state, selected_context):
            result = (
                await backend.reconcile(plan, authority, selected_context)
                if reconcile
                else await backend.apply(plan, authority, selected_context)
            )
        return {
            "schema": "openrtl.managed-backend-result.v1",
            "backend": backend_id,
            "operation_id": result.operation_id,
            "plan_digest": result.plan_digest,
            "status": result.status,
            "binding": cast(JsonObject, dict(result.binding)),
            "reconciled": reconcile,
            "runtime_contact": True,
            "execution_authorized": False,
            "ready_for_simulation": False,
            "notice": "Lifecycle result only. Runtime transport and simulator identity must be selected and reverified separately.",
            "m46": "pending",
            "m47": "pending",
        }
    except ValueError as error:
        raise _public_error(error, "runtime_backend_execution_failed") from None
    except Exception as error:
        code = getattr(error, "code", None)
        if isinstance(code, str):
            raise ValueError("runtime_" + code) from None
        raise ValueError("runtime_backend_execution_failed") from None
