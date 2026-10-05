"""Lima-specific artifact closure and managed lifecycle registration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import stat
from typing import TYPE_CHECKING, cast

from openrtl.domain.design_session import JsonObject, require

if TYPE_CHECKING:
    from agentrig.capabilities.local_backend import BackendPlan
    from agentrig.integrations.guest_retirement import GuestServiceRetirementAdapter
    from agentrig.integrations.guest_workspace import GuestWorkspaceEndpoint
    from agentrig.integrations.lima_retirement import (
        LimaGenerationFence,
        LimaRetirementObserver,
        LimaSocketRetirer,
    )
    from openrtl.adapters.managed_backend import ManagedLifecycleBackend


@dataclass(frozen=True, slots=True)
class LimaArtifactClosure:
    executable: Path
    executable_sha256: str
    executable_size: int
    state_root: Path
    configuration_file: Path
    configuration_sha256: str
    policy_digest: str
    image_manifest_digest: str
    artifact_closure_sha256: str

    def configuration(self, instance_id: str) -> JsonObject:
        _instance_id(instance_id)
        return {
            "executable": str(self.executable),
            "executable_sha256": self.executable_sha256,
            "state_root": str(self.state_root),
            "configuration_file": str(self.configuration_file),
            "configuration_sha256": self.configuration_sha256,
            "artifact_closure_sha256": self.artifact_closure_sha256,
            "instance_id": instance_id,
        }


def _instance_id(value: object) -> str:
    require(
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value),
        "runtime_backend_configuration_invalid",
    )
    return cast(str, value)


def _strict_object(payload: str) -> JsonObject:
    require(len(payload.encode("utf-8")) <= 65536, "runtime_backend_artifacts_invalid")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            require(key not in result, "runtime_backend_artifacts_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload,
            object_pairs_hook=unique,
            parse_constant=lambda _: require(False, "runtime_backend_artifacts_invalid"),
        )
        require(type(value) is dict, "runtime_backend_artifacts_invalid")
        return cast(JsonObject, value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError):
        raise ValueError("runtime_backend_artifacts_invalid") from None


def _sha256_file(path: Path, *, executable: bool, maximum: int) -> tuple[str, int]:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        mode = info.st_mode
        require(
            stat.S_ISREG(mode)
            and info.st_nlink == 1
            and info.st_uid == os.getuid()
            and mode & 0o022 == 0
            and (not executable or mode & stat.S_IXUSR != 0)
            and 1 <= info.st_size <= maximum,
            "runtime_backend_artifacts_invalid",
        )
        digest = hashlib.sha256()
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            require(bool(chunk), "runtime_backend_artifacts_invalid")
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
        require(
            (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_uid,
                after.st_nlink,
                after.st_size,
                after.st_mtime_ns,
            )
            == (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
            ),
            "runtime_backend_artifacts_invalid",
        )
        return "sha256:" + digest.hexdigest(), info.st_size
    except (OSError, ValueError):
        raise ValueError("runtime_backend_artifacts_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_private_file(path: Path, maximum: int) -> bytes:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        require(
            stat.S_ISREG(info.st_mode)
            and info.st_nlink == 1
            and info.st_uid == os.getuid()
            and info.st_mode & 0o077 == 0
            and 1 <= info.st_size <= maximum,
            "runtime_backend_artifacts_invalid",
        )
        data = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
        require(
            len(data) == info.st_size
            and len(data) <= maximum
            and (after.st_dev, after.st_ino, after.st_mode, after.st_uid,
                 after.st_nlink, after.st_size, after.st_mtime_ns)
            == (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                info.st_nlink, info.st_size, info.st_mtime_ns),
            "runtime_backend_artifacts_invalid",
        )
        return data
    except (OSError, ValueError):
        raise ValueError("runtime_backend_artifacts_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _verify_lima_generated_state(state_root: Path) -> None:
    """Permit only Lima's private generated global files, without reading them."""
    global_state = state_root / "lima" / "_config"
    try:
        info = global_state.lstat()
    except FileNotFoundError:
        return
    require(
        stat.S_ISDIR(info.st_mode)
        and not global_state.is_symlink()
        and info.st_uid == os.getuid()
        and info.st_mode & 0o077 == 0,
        "runtime_backend_artifacts_invalid",
    )
    with os.scandir(global_state) as entries:
        observed = {entry.name: entry for entry in entries}
    require(
        set(observed) <= {"networks.yaml", "user", "user.pub"},
        "runtime_backend_artifacts_invalid",
    )
    for entry in observed.values():
        item = entry.stat(follow_symlinks=False)
        require(
            entry.is_file(follow_symlinks=False)
            and not entry.is_symlink()
            and item.st_uid == os.getuid()
            and item.st_nlink == 1
            and item.st_mode & 0o077 == 0,
            "runtime_backend_artifacts_invalid",
        )


def audit_lima_artifact_closure(executable: Path, state_root: Path) -> LimaArtifactClosure:
    """Verify the exact executable, restricted configuration and guest image bytes."""
    try:
        from agentrig.capabilities.local_backend import backend_digest
        from agentrig.integrations.lima_configuration import LimaConfiguration
    except ImportError:
        raise ValueError("runtime_backend_sdk_candidate_required") from None

    selected_root = state_root.absolute()
    selected_executable = executable.absolute()
    require(
        selected_root != Path(selected_root.anchor)
        and ".." not in selected_root.parts
        and selected_executable != Path(selected_executable.anchor)
        and ".." not in selected_executable.parts,
        "runtime_backend_artifacts_invalid",
    )
    descriptor: int | None = None
    try:
        descriptor = os.open(selected_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        root_info = os.fstat(descriptor)
        require(
            stat.S_ISDIR(root_info.st_mode)
            and root_info.st_uid == os.getuid()
            and root_info.st_mode & 0o077 == 0,
            "runtime_backend_artifacts_invalid",
        )
    except (OSError, ValueError):
        raise ValueError("runtime_backend_artifacts_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)

    executable_sha256, executable_size = _sha256_file(
        selected_executable, executable=True, maximum=512 * 1024 * 1024
    )
    configuration_file = selected_root / "configuration.json"
    configuration_data = _read_private_file(configuration_file, 65536)
    configuration_sha256 = "sha256:" + hashlib.sha256(configuration_data).hexdigest()
    try:
        configuration_text = configuration_data.decode("utf-8")
    except UnicodeError:
        raise ValueError("runtime_backend_artifacts_invalid") from None
    configuration = _strict_object(configuration_text)
    try:
        images = configuration["images"]
        user = configuration["user"]
        require(
            type(images) is list and len(images) == 1 and type(images[0]) is dict,
            "runtime_backend_artifacts_invalid",
        )
        image = cast(dict[str, object], images[0])
        require(type(user) is dict, "runtime_backend_artifacts_invalid")
        user_value = cast(dict[str, object], user)
        image_path = selected_root / "artifacts" / "guest.raw"
        require(image.get("location") == str(image_path), "runtime_backend_artifacts_invalid")
        image_sha256, image_size = _sha256_file(
            image_path, executable=False, maximum=4 * 1024**3
        )
        require(image.get("digest") == image_sha256, "runtime_backend_artifacts_invalid")
        require(
            type(configuration["cpus"]) is int
            and isinstance(configuration["memory"], str)
            and isinstance(configuration["disk"], str)
            and type(user_value["uid"]) is int,
            "runtime_backend_artifacts_invalid",
        )
        policy = LimaConfiguration(
            state_root=selected_root,
            guest_image_sha256=image_sha256,
            guest_image_size=image_size,
            cpus=cast(int, configuration["cpus"]),
            memory_mib=int(cast(str, configuration["memory"]).removesuffix("MiB")),
            disk_gib=int(cast(str, configuration["disk"]).removesuffix("GiB")),
            guest_uid=cast(int, user_value["uid"]),
        )
        policy.validate(configuration_data)
        _verify_lima_generated_state(selected_root)
        require(
            _sha256_file(selected_executable, executable=True, maximum=512 * 1024 * 1024)
            == (executable_sha256, executable_size)
            and _read_private_file(configuration_file, 65536) == configuration_data,
            "runtime_backend_artifacts_invalid",
        )
        closure = backend_digest(
            {
                "schema": "openrtl.lima-artifact-closure.v1",
                "executable": {"sha256": executable_sha256, "size_bytes": executable_size},
                "configuration_sha256": configuration_sha256,
                "policy_digest": policy.digest,
                "image_manifest_digest": policy.image_manifest.digest,
            }
        )
        return LimaArtifactClosure(
            executable=selected_executable,
            executable_sha256=executable_sha256,
            executable_size=executable_size,
            state_root=selected_root,
            configuration_file=configuration_file,
            configuration_sha256=configuration_sha256,
            policy_digest=policy.digest,
            image_manifest_digest=policy.image_manifest.digest,
            artifact_closure_sha256=closure,
        )
    except Exception as error:
        if type(error) is ValueError and str(error) == "runtime_backend_sdk_candidate_required":
            raise
        raise ValueError("runtime_backend_artifacts_invalid") from None


@dataclass(frozen=True, slots=True)
class LimaArtifactVerifier:
    def __call__(self, plan: BackendPlan) -> bool:
        try:
            configuration = plan.request.configuration
            closure = audit_lima_artifact_closure(
                Path(cast(str, configuration["executable"])),
                Path(cast(str, configuration["state_root"])),
            )
            expected = closure.configuration(cast(str, configuration["instance_id"]))
            require(dict(configuration) == expected, "runtime_backend_artifacts_invalid")
            instance = closure.state_root / "lima" / cast(str, plan.material["instance"])
            try:
                info = instance.lstat()
            except FileNotFoundError:
                return False
            require(
                stat.S_ISDIR(info.st_mode)
                and not instance.is_symlink()
                and info.st_uid == os.getuid()
                and info.st_mode & 0o077 == 0,
                "runtime_backend_artifacts_invalid",
            )
            return True
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            from agentrig.capabilities.local_backend import BackendFailure

            raise BackendFailure("backend_artifacts_invalid") from None


def lima_managed_backend(state: Path) -> ManagedLifecycleBackend:
    try:
        from agentrig.integrations.backend_journal import LocalBackendJournal
        from agentrig.integrations.lima_lifecycle import LimaLifecycleBackend
    except ImportError:
        raise ValueError("runtime_backend_sdk_candidate_required") from None
    return cast(
        "ManagedLifecycleBackend",
        LimaLifecycleBackend(
            journal=LocalBackendJournal(state.absolute() / "backend-operations"),
            verify_artifacts=LimaArtifactVerifier(),
        ),
    )


def validate_lima_retirement_configuration(
    value: JsonObject, endpoint: GuestWorkspaceEndpoint
) -> JsonObject:
    """Validate retirement binding syntax without reading state or contacting Lima."""
    try:
        require(
            set(value)
            == {
                "managed_configuration",
                "lifecycle_operation_id",
                "generation_digest",
                "service_lock",
            }
            and type(value["managed_configuration"]) is dict,
            "runtime_retirement_configuration_invalid",
        )
        managed = cast(JsonObject, value["managed_configuration"])
        require(
            set(managed)
            == {
                "executable",
                "executable_sha256",
                "state_root",
                "configuration_file",
                "configuration_sha256",
                "artifact_closure_sha256",
                "instance_id",
            },
            "runtime_retirement_configuration_invalid",
        )
        instance_id = _instance_id(managed["instance_id"])
        state_root_value = managed["state_root"]
        executable_value = managed["executable"]
        configuration_value = managed["configuration_file"]
        require(
            isinstance(state_root_value, str)
            and isinstance(executable_value, str)
            and isinstance(configuration_value, str),
            "runtime_retirement_configuration_invalid",
        )
        state_root = Path(cast(str, state_root_value))
        executable = Path(cast(str, executable_value))
        configuration = Path(cast(str, configuration_value))
        require(
            state_root.is_absolute()
            and state_root != Path(state_root.anchor)
            and ".." not in state_root.parts
            and executable.is_absolute()
            and executable != Path(executable.anchor)
            and ".." not in executable.parts
            and configuration == state_root / "configuration.json"
            and endpoint.root == state_root / "transport"
            and endpoint.instance_id == instance_id,
            "runtime_retirement_configuration_invalid",
        )
        for key in (
            "executable_sha256",
            "configuration_sha256",
            "artifact_closure_sha256",
            "generation_digest",
        ):
            selected = managed[key] if key in managed else value[key]
            require(
                isinstance(selected, str)
                and len(selected) == 71
                and selected.startswith("sha256:")
                and all(character in "0123456789abcdef" for character in selected[7:]),
                "runtime_retirement_configuration_invalid",
            )
        operation_id = value["lifecycle_operation_id"]
        require(
            isinstance(operation_id, str)
            and len(operation_id) == 32
            and all(character in "0123456789abcdef" for character in operation_id),
            "runtime_retirement_configuration_invalid",
        )
        service_lock_value = value["service_lock"]
        require(
            type(service_lock_value) is dict
            and set(service_lock_value) == {"device", "inode", "uid"},
            "runtime_retirement_configuration_invalid",
        )
        service_lock = cast(JsonObject, service_lock_value)
        require(
            all(type(service_lock[key]) is int and cast(int, service_lock[key]) >= 0
                for key in ("device", "inode", "uid")),
            "runtime_retirement_configuration_invalid",
        )
        require(
            value["generation_digest"]
            == lima_retirement_generation_digest(managed, endpoint, service_lock),
            "runtime_retirement_configuration_invalid",
        )
        return {
            "managed_configuration": dict(managed),
            "lifecycle_operation_id": operation_id,
            "generation_digest": value["generation_digest"],
            "service_lock": dict(service_lock),
        }
    except (KeyError, TypeError, ValueError):
        raise ValueError("runtime_retirement_configuration_invalid") from None


def lima_retirement_generation_digest(
    managed_configuration: JsonObject,
    endpoint: GuestWorkspaceEndpoint,
    service_lock: JsonObject,
) -> str:
    """Bind observed forward and service-lock identities to the Lima closure."""
    from agentrig.capabilities.local_backend import backend_digest

    return backend_digest(
        {
            "schema": "openrtl.lima-retirement-generation.v1",
            "managed_configuration": managed_configuration,
            "endpoint": {
                "root": str(endpoint.root),
                "instance_id": endpoint.instance_id,
                "device": endpoint.device,
                "inode": endpoint.inode,
                "uid": endpoint.uid,
            },
            "service_lock": service_lock,
        }
    )


def bind_lima_retirement_adapter(
    configuration: JsonObject,
    endpoint: GuestWorkspaceEndpoint,
    state: Path,
    *,
    fence_generation: LimaGenerationFence,
    retire_socket: LimaSocketRetirer,
    observe_retired: LimaRetirementObserver,
) -> GuestServiceRetirementAdapter:
    """Bind audited Lima artifacts and a fresh generation to injected live ports."""
    from agentrig.capabilities.local_backend import BackendAction, BackendRequest
    from agentrig.integrations.backend_journal import LocalBackendJournal
    from agentrig.integrations.lima_lifecycle import LimaLifecycleBackend
    from agentrig.integrations.lima_retirement import (
        LimaGuestServiceRetirementAdapter,
        bind_lima_guest_service_generation,
    )

    validated = validate_lima_retirement_configuration(configuration, endpoint)
    managed = cast(JsonObject, validated["managed_configuration"])
    closure = audit_lima_artifact_closure(
        Path(cast(str, managed["executable"])),
        Path(cast(str, managed["state_root"])),
    )
    require(
        managed == closure.configuration(cast(str, managed["instance_id"])),
        "runtime_backend_artifacts_invalid",
    )
    backend = LimaLifecycleBackend(
        journal=LocalBackendJournal(
            state.absolute() / "guest-retirements" / "lifecycle-binding"
        ),
        verify_artifacts=LimaArtifactVerifier(),
    )
    plan = backend.plan(
        BackendRequest(
            action=BackendAction.INSPECT,
            operation_id=cast(str, validated["lifecycle_operation_id"]),
            configuration=managed,
        )
    )
    generation = bind_lima_guest_service_generation(
        backend,
        plan,
        endpoint,
        cast(str, validated["generation_digest"]),
    )
    return LimaGuestServiceRetirementAdapter(
        generation=generation,
        fence_generation=fence_generation,
        retire_socket=retire_socket,
        observe_retired=observe_retired,
    )
