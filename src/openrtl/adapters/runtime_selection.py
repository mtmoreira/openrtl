"""Opt-in inspection of one current-user rootless endpoint. No lifecycle or pulls."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Awaitable, Callable

from openrtl.domain.design_session import JsonObject, require
from openrtl.domain.simulation_runtime import absolute_path, validate_observation, validate_profile


Process = Callable[[list[str], Path, int, int], Awaitable[tuple[int, bytes]]]
DAEMON_FORMAT = '{{json .ID}}|{{json .OSType}}|{{json .Architecture}}|{{json .SecurityOptions}}|{{json .NCPU}}|{{json .MemTotal}}'
IMAGE_FORMAT = '{{json .Id}}|{{json .Os}}|{{json .Architecture}}'


def _ancestors(path: Path) -> None:
    for selected in (path, *path.parents):
        info = selected.lstat()
        require(not stat.S_ISLNK(info.st_mode), "runtime_path_symlink")
        require(info.st_uid in (0, os.getuid()), "runtime_path_other_owner")
        # Root-owned sticky temporary ancestors are safe for private child paths.
        require(not info.st_mode & 0o022 or
                (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)),
                "runtime_path_writable_by_others")


def local_identity(executable: str, socket: str) -> JsonObject:
    """No socket connection, environment credentials or Docker configuration reads."""
    binary, endpoint = Path(absolute_path(executable)), Path(absolute_path(socket))
    _ancestors(binary)
    _ancestors(endpoint)
    info = endpoint.lstat()
    require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid(), "runtime_socket_not_current_user")
    descriptor = os.open(binary, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        code = os.fstat(stream.fileno())
        require(stat.S_ISREG(code.st_mode) and code.st_nlink == 1 and code.st_size <= 128 * 1024 * 1024 and
                code.st_mode & 0o111 != 0, "runtime_executable_invalid")
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"docker_sha256": "sha256:" + digest,
            "socket_identity": {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid}}


def verify_local_identity(profile: JsonObject) -> None:
    current = local_identity(profile["docker_executable"], profile["socket"])
    require(all(current[key] == profile[key] for key in current), "runtime_local_identity_changed")


def _fields(payload: bytes, count: int) -> list[object]:
    require(len(payload) <= 8192, "runtime_inspection_output_bound")
    try:
        parts = payload.decode("utf-8").strip().split("|")
        require(len(parts) == count, "runtime_inspection_shape_invalid")
        return [json.loads(part) for part in parts]
    except (UnicodeError, json.JSONDecodeError):
        raise ValueError("runtime_inspection_shape_invalid") from None


def _architecture(value: object) -> object:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(str(value), value)


async def observations(profile: JsonObject, config: Path, process: Process) -> tuple[JsonObject, JsonObject]:
    require(config.is_dir() and not config.is_symlink() and not any(config.iterdir()),
            "runtime_config_must_be_empty")
    prefix = [profile["docker_executable"], "--host", "unix://" + profile["socket"], "--config", str(config)]
    code, raw = await process(prefix + ["info", "--format", DAEMON_FORMAT], config, 30, 8192)
    require(code == 0, "runtime_daemon_inspection_failed")
    identity, system, architecture, security, cpus, memory = _fields(raw, 6)
    require(type(security) is list and all(type(item) is str for item in security), "runtime_security_metadata_invalid")
    assert isinstance(security, list)
    daemon = {"id": identity, "os": system, "architecture": _architecture(architecture),
              "rootless": "name=rootless" in security, "cpus": cpus, "memory_bytes": memory}
    # Reject non-rootless daemons before even querying images. No automatic fallback.
    require(daemon["rootless"] is True, "runtime_ownership_unqualified")
    code, raw = await process(prefix + ["image", "inspect", "--format", IMAGE_FORMAT, profile["image_id"]], config, 30, 8192)
    require(code == 0, "runtime_pinned_image_unavailable")
    image_id, image_os, image_arch = _fields(raw, 3)
    return daemon, {"id": image_id, "os": image_os, "architecture": _architecture(image_arch)}


async def inspect_selection(candidate: JsonObject, config: Path, process: Process, *, authorized: bool) -> JsonObject:
    require(authorized is True, "runtime_contact_requires_explicit_consent")
    # Validate all caller-controlled values before invoking the selected executable.
    profile = validate_profile(candidate)
    verify_local_identity(profile)
    daemon, image = await observations(profile, config, process)
    validate_observation(profile, daemon, image)
    verify_local_identity(profile)
    return profile


async def select_runtime(candidate: JsonObject, config: Path, process: Process, *, authorized: bool) -> JsonObject:
    """Bind a reviewed local choice to the first observed daemon, without starting it."""
    require(authorized is True, "runtime_contact_requires_explicit_consent")
    profile = validate_profile(candidate)
    verify_local_identity(profile)
    daemon, image = await observations(profile, config, process)
    selected = validate_profile({**profile, "daemon_id": daemon["id"]})
    validate_observation(selected, daemon, image)
    verify_local_identity(selected)
    return selected
