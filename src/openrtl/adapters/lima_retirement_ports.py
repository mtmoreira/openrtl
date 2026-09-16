"""Concrete Lima CLI retirement ports behind the backend-neutral coordinator."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from collections.abc import Awaitable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, cast

from openrtl.adapters.lima_managed import (
    audit_lima_artifact_closure,
    bind_lima_retirement_adapter,
    lima_retirement_generation_digest,
    validate_lima_retirement_configuration,
)
from openrtl.adapters.managed_backend import managed_backend_fence
from openrtl.domain.design_session import JsonObject

if TYPE_CHECKING:
    from agentrig.core import RunContext
    from agentrig.integrations.bounded_process import ProcessOutput, ProcessSpec
    from agentrig.integrations.guest_retirement import GuestServiceRetirementAdapter
    from agentrig.integrations.guest_workspace import GuestWorkspaceEndpoint
    from agentrig.integrations.lima_retirement import (
        LimaGuestServiceGeneration,
        LimaQuiescenceObservation,
        LimaRetirementObservation,
    )
    from openrtl.adapters.guest_retirement import RetirementBackendDefinition


class LimaRetirementProcess(Protocol):
    def __call__(
        self, spec: ProcessSpec, context: RunContext
    ) -> Awaitable[ProcessOutput]: ...


_FORMAT = "{{json .Name}}|{{json .Dir}}|{{json .Status}}|{{json .VMType}}|{{json .Arch}}"


@dataclass(slots=True)
class LimaCliRetirementPorts:
    """Re-audit one Lima closure and fence its prior authenticated forward."""

    configuration: JsonObject = field(repr=False)
    endpoint: GuestWorkspaceEndpoint = field(repr=False)
    state: Path = field(repr=False)
    execute: LimaRetirementProcess = field(repr=False)
    _active_binding: str | None = field(default=None, init=False, repr=False)
    _root_identity: tuple[int, int, int] | None = field(
        default=None, init=False, repr=False
    )
    _lock_identity: tuple[int, int, int] | None = field(
        default=None, init=False, repr=False
    )
    _last_retirement_effect: Literal["removed"] | None = field(
        default=None, init=False, repr=False
    )

    @property
    def last_retirement_effect(self) -> Literal["removed"] | None:
        """Report only an unlink performed by this object; absence is not removal."""
        return self._last_retirement_effect

    def _check(self, context: RunContext) -> None:
        context.cancellation.raise_if_cancelled()
        if context.deadline is None:
            raise ValueError("Lima retirement deadline required")
        context.deadline.raise_if_expired(context.clock)

    def _validated(self) -> tuple[JsonObject, tuple[int, int, int]]:
        from agentrig.capabilities import BackendFailure

        try:
            validated = validate_lima_retirement_configuration(
                self.configuration, self.endpoint
            )
            managed = cast(JsonObject, validated["managed_configuration"])
            service_lock = cast(JsonObject, validated["service_lock"])
            closure = audit_lima_artifact_closure(
                Path(cast(str, managed["executable"])),
                Path(cast(str, managed["state_root"])),
            )
            if managed != closure.configuration(cast(str, managed["instance_id"])):
                raise BackendFailure("backend_plan_changed")
            return managed, (
                cast(int, service_lock["device"]),
                cast(int, service_lock["inode"]),
                cast(int, service_lock["uid"]),
            )
        except BackendFailure:
            raise
        except Exception:
            raise BackendFailure("backend_plan_changed") from None

    def _run(self, specification: ProcessSpec, context: RunContext) -> ProcessOutput:
        from agentrig.capabilities import BackendFailure
        from agentrig.core import DeadlineExceeded, RunCancelled

        async def invoke() -> ProcessOutput:
            return await self.execute(specification, context)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise BackendFailure("backend_execution_failed")
        try:
            return asyncio.run(invoke())
        except (BackendFailure, DeadlineExceeded, RunCancelled):
            raise
        except Exception:
            raise BackendFailure("backend_execution_failed") from None

    def _instance_stopped(self, context: RunContext) -> JsonObject:
        from agentrig.capabilities import BackendFailure
        from agentrig.integrations.bounded_process import ProcessOutput, ProcessSpec

        self._check(context)
        managed, _ = self._validated()
        executable = cast(str, managed["executable"])
        state_root = cast(str, managed["state_root"])
        instance = "managed-" + cast(str, managed["instance_id"])
        try:
            instance_info = (Path(state_root) / "lima" / instance).lstat()
            if (
                not stat.S_ISDIR(instance_info.st_mode)
                or instance_info.st_mode & 0o077 != 0
                or instance_info.st_uid != os.getuid()
            ):
                raise BackendFailure("backend_staging_failed")
        except BackendFailure:
            raise
        except Exception:
            raise BackendFailure("backend_staging_failed") from None
        specification = ProcessSpec(
            argv=(
                executable,
                "--tty=false",
                "list",
                "--format",
                _FORMAT,
                instance,
            ),
            cwd=state_root,
            environment={
                "LIMA_HOME": state_root + "/lima",
                "HOME": state_root,
                "XDG_CONFIG_HOME": state_root + "/config",
                "XDG_CACHE_HOME": state_root + "/cache",
                "PATH": "/usr/bin:/bin",
                "LC_ALL": "C",
            },
            timeout_seconds=60,
            max_output_bytes=65536,
            reject_overflow=True,
        )
        result = self._run(specification, context)
        self._validated()
        if (
            not isinstance(result, ProcessOutput)
            or result.exit_code != 0
            or result.truncated
            or len(result.stdout) + len(result.stderr) > specification.max_output_bytes
        ):
            raise BackendFailure("backend_execution_failed")
        try:
            fields = [json.loads(value) for value in result.stdout.decode().strip().split("|")]
        except (UnicodeError, ValueError):
            raise BackendFailure("backend_invalid_result") from None
        expected_directory = state_root + "/lima/" + instance
        if (
            len(fields) != 5
            or fields[0] != instance
            or fields[1] != expected_directory
            or fields[2] != "Stopped"
            or fields[3:] != ["vz", "aarch64"]
        ):
            raise BackendFailure("backend_invalid_result")
        self._check(context)
        return managed

    def _open_root(self) -> tuple[int, tuple[int, int, int]]:
        from agentrig.capabilities import BackendFailure

        try:
            descriptor = os.open(
                self.endpoint.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_mode & 0o077 != 0
                or info.st_uid != os.getuid()
            ):
                raise BackendFailure("backend_staging_failed")
            return descriptor, (info.st_dev, info.st_ino, info.st_uid)
        except BackendFailure:
            raise
        except Exception:
            raise BackendFailure("backend_staging_failed") from None

    def _check_root(self, expected: tuple[int, int, int]) -> int:
        from agentrig.capabilities import BackendFailure

        descriptor, identity = self._open_root()
        if identity != expected:
            os.close(descriptor)
            raise BackendFailure("backend_staging_failed")
        return descriptor

    def _socket_present(self, directory: int) -> bool:
        from agentrig.capabilities import BackendFailure

        try:
            info = os.stat("workspace.sock", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except Exception:
            raise BackendFailure("backend_staging_failed") from None
        if (
            not stat.S_ISSOCK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino, info.st_uid)
            != (self.endpoint.device, self.endpoint.inode, self.endpoint.uid)
        ):
            raise BackendFailure("backend_staging_failed")
        return True

    def _service_lock_retained(
        self, directory: int, expected: tuple[int, int, int]
    ) -> None:
        from agentrig.capabilities import BackendFailure

        try:
            info = os.stat(".service.lock", dir_fd=directory, follow_symlinks=False)
        except Exception:
            raise BackendFailure("backend_staging_failed") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_size != 0
            or (info.st_dev, info.st_ino, info.st_uid) != expected
        ):
            raise BackendFailure("backend_staging_failed")

    def _service_lock_available(
        self, directory: int, expected: tuple[int, int, int]
    ) -> None:
        import fcntl

        from agentrig.capabilities import BackendFailure

        descriptor: int | None = None
        try:
            self._service_lock_retained(directory, expected)
            descriptor = os.open(
                ".service.lock",
                os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=directory,
            )
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or info.st_nlink != 1
                or info.st_size != 0
                or (info.st_dev, info.st_ino, info.st_uid) != expected
            ):
                raise BackendFailure("backend_staging_failed")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BackendFailure("backend_staging_failed") from None
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except BackendFailure:
            raise
        except Exception:
            raise BackendFailure("backend_staging_failed") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _generation(
        self, generation: LimaGuestServiceGeneration
    ) -> tuple[int, int, int]:
        from agentrig.capabilities import BackendFailure

        managed, lock_identity = self._validated()
        service_lock: JsonObject = {
            "device": lock_identity[0],
            "inode": lock_identity[1],
            "uid": lock_identity[2],
        }
        if (
            generation.endpoint != self.endpoint
            or generation.generation_digest
            != lima_retirement_generation_digest(managed, self.endpoint, service_lock)
        ):
            raise BackendFailure("backend_plan_changed")
        return lock_identity

    def _require_active(
        self, binding_digest: str | None = None
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        from agentrig.capabilities import BackendFailure

        if (
            self._active_binding is None
            or self._root_identity is None
            or self._lock_identity is None
            or (binding_digest is not None and binding_digest != self._active_binding)
        ):
            raise BackendFailure("backend_plan_blocked")
        return self._root_identity, self._lock_identity

    @contextmanager
    def fence_generation(
        self, generation: LimaGuestServiceGeneration, context: RunContext
    ) -> Iterator[LimaQuiescenceObservation]:
        from agentrig.capabilities import BackendFailure
        from agentrig.integrations.lima_retirement import LimaQuiescenceObservation

        if self._active_binding is not None:
            raise BackendFailure("backend_journal_busy")
        with managed_backend_fence(self.state, context):
            lock_identity = self._generation(generation)
            self._instance_stopped(context)
            directory, identity = self._open_root()
            try:
                self._socket_present(directory)
                self._service_lock_available(directory, lock_identity)
            finally:
                os.close(directory)
            self._active_binding = generation.digest
            self._root_identity = identity
            self._lock_identity = lock_identity
            self._last_retirement_effect = None
            try:
                yield LimaQuiescenceObservation(binding_digest=generation.digest)
            finally:
                try:
                    self._instance_stopped(context)
                    current = self._check_root(identity)
                    try:
                        self._socket_present(current)
                        self._service_lock_available(current, lock_identity)
                    finally:
                        os.close(current)
                finally:
                    self._active_binding = None
                    self._root_identity = None
                    self._lock_identity = None

    def retire_socket(
        self, endpoint: GuestWorkspaceEndpoint, approved_digest: str, context: RunContext
    ) -> None:
        from agentrig.capabilities import BackendFailure
        from agentrig.integrations.guest_service import retire_guest_service

        identity, lock_identity = self._require_active()
        if endpoint != self.endpoint:
            raise BackendFailure("backend_plan_changed")

        def verify_quiescent(
            observed_endpoint: GuestWorkspaceEndpoint, observed_context: RunContext
        ) -> None:
            if observed_endpoint != self.endpoint:
                raise BackendFailure("backend_plan_changed")
            self._require_active()
            self._instance_stopped(observed_context)
            root = self._check_root(identity)
            try:
                self._service_lock_retained(root, lock_identity)
            finally:
                os.close(root)

        retire_guest_service(
            endpoint,
            approved_digest,
            context,
            verify_quiescent=verify_quiescent,
        )
        self._last_retirement_effect = "removed"

    def observe_retired(
        self, generation: LimaGuestServiceGeneration, context: RunContext
    ) -> LimaRetirementObservation:
        from agentrig.capabilities import BackendFailure
        from agentrig.integrations.lima_retirement import LimaRetirementObservation

        identity, lock_identity = self._require_active(generation.digest)
        if self._generation(generation) != lock_identity:
            raise BackendFailure("backend_plan_changed")
        self._instance_stopped(context)
        directory = self._check_root(identity)
        try:
            if self._socket_present(directory):
                raise BackendFailure("backend_staging_failed")
            self._service_lock_available(directory, lock_identity)
        finally:
            os.close(directory)
        return LimaRetirementObservation(binding_digest=generation.digest)


def lima_cli_retirement_registration(
    execute: LimaRetirementProcess | None = None,
) -> RetirementBackendDefinition:
    """Build the default Lima CLI registration with an injectable process seam."""
    from agentrig.integrations.bounded_process import run_bounded_process
    from openrtl.adapters.guest_retirement import RetirementBackendDefinition

    selected = run_bounded_process if execute is None else execute

    def create(
        configuration: JsonObject, endpoint: GuestWorkspaceEndpoint, state: Path
    ) -> GuestServiceRetirementAdapter:
        ports = LimaCliRetirementPorts(configuration, endpoint, state, selected)
        return bind_lima_retirement_adapter(
            configuration,
            endpoint,
            state,
            fence_generation=ports.fence_generation,
            retire_socket=ports.retire_socket,
            observe_retired=ports.observe_retired,
        )

    return RetirementBackendDefinition(
        validate=validate_lima_retirement_configuration,
        create=create,
    )
